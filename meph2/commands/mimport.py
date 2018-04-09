#!/usr/bin/python3

import argparse
from datetime import datetime
import hashlib
import os
import re
import subprocess
import sys
import yaml

from meph2 import util
from meph2.commands.dpkg import (
    get_package,
    extract_files_from_packages,
)

from meph2.commands.flags import COMMON_ARGS, SUBCOMMANDS
from meph2.url_helper import geturl_text


def import_sha256(args, product_tree, cfgdata):
    for (release, release_info) in cfgdata['versions'].items():
        if 'arch' in release_info:
            arch = release_info['arch']
        else:
            arch = cfgdata['arch']
        if 'os' in release_info:
            os_name = release_info['os']
        else:
            os_name = cfgdata['os']
        if 'path_version' in release_info:
            path_version = release_info['path_version']
        else:
            path_version = release_info['version']
        product_id = cfgdata['product_id'].format(
            version=release_info['version'], arch=arch)
        url = cfgdata['sha256_meta_data_path'].format(version=path_version)
        images = get_sha256_meta_images(url, args.max)
        base_url = os.path.dirname(url)

        if product_tree['products'].get(product_id) is None:
            print("Creating new product %s" % product_id)
            product_tree['products'][product_id] = {
                'subarches': 'generic',
                'label': 'daily',
                'subarch': 'generic',
                'arch': arch,
                'os': os_name,
                'version': release_info['version'],
                'release': release,
                'versions': {},
            }

        for (image, image_info) in images.items():
            if (
                    product_id in product_tree['products'] and
                    image in product_tree['products'][product_id]['versions']):
                print(
                    "Product %s at version %s exists, skipping" % (
                        product_id, image))
                continue
            print(
                "Downloading and creating %s version %s" % (
                    (product_id, image)))
            image_path = '/'.join([release, arch, image, 'root-tgz'])
            real_image_path = os.path.join(
                os.path.realpath(args.target), image_path)
            if release_info.get('packages') is not None:
                packages = ','.join(release_info['packages'])
            else:
                packages = None
            sha256 = import_qcow2(
                '/'.join([base_url, image_info['img_name']]),
                image_info['sha256'], real_image_path,
                release_info.get('curtin_files'), packages)
            product_tree['products'][product_id]['versions'][image] = {
                'items': {
                    'root-image.gz': {
                        'ftype': 'root-tgz',
                        'sha256': sha256,
                        'path': image_path,
                        'size': os.path.getsize(real_image_path),
                        }
                    }
                }


def import_bootloaders(args, product_tree, cfgdata):
    for firmware_platform in cfgdata['bootloaders']:
        product_id = cfgdata['product_id'].format(
            os=firmware_platform['os'],
            firmware_platform=firmware_platform['firmware-platform'],
            arch=firmware_platform['arch'])
        # Compile a list of the latest packages in the archive this bootloader
        # pulls files from
        src_packages = {}
        for package in firmware_platform['packages']:
            package_info = get_package(
                archive=firmware_platform['archive'], pkg_name=package,
                architecture=firmware_platform['arch'],
                release=firmware_platform['release'], proposed=args.proposed)
            # Some source packages include the package version in the source
            # name. Only take the name, not the version.
            src_package_name = package_info['Source'].split(' ')[0]
            src_packages[src_package_name] = {
                'src_version': package_info['Version'],
                'src_release': firmware_platform['release'],
                'found': False,
            }
        # Check if the bootloader has been built from the latest version of
        # the packages in the archive
        if product_id in product_tree['products']:
            versions = product_tree['products'][product_id]['versions']
            for data in versions.values():
                for item in data['items'].values():
                    src_package = src_packages.get(item['src_package'])
                    if (
                            src_package is not None and
                            src_package['src_version'] == item['src_version']
                            and
                            src_package['src_release'] == item['src_release']):
                        src_packages[item['src_package']]['found'] = True
        bootloader_uptodate = True
        for src_package in src_packages.values():
            if not src_package['found']:
                bootloader_uptodate = False
        # Bootloader built from the latest packages already in stream
        if bootloader_uptodate:
            print(
                "Product %s built from the latest package set, skipping"
                % product_id)
            continue
        # Find an unused version
        today = datetime.utcnow().strftime('%Y%m%d')
        point = 0
        while True:
            version = "%s.%d" % (today, point)
            products = product_tree['products']
            if (
                    product_id not in products or
                    version not in products[product_id]['versions'].keys()):
                break
            point += 1
        if product_tree['products'].get(product_id) is None:
            print("Creating new product %s" % product_id)
            product_tree['products'][product_id] = {
                'label': 'daily',
                'arch': firmware_platform['arch'],
                'arches': firmware_platform['arches'],
                'os': firmware_platform['os'],
                'bootloader-type': firmware_platform['firmware-platform'],
                'versions': {},
                }
        path = os.path.join(
            'bootloaders', firmware_platform['firmware-platform'],
            firmware_platform['arch'], version)
        dest = os.path.join(args.target, path)
        os.makedirs(dest)
        grub_format = firmware_platform.get('grub_format')
        if grub_format is not None:
            dest = os.path.join(dest, firmware_platform['grub_output'])
        print(
            "Downloading and creating %s version %s" % (
                product_id, version))
        items = extract_files_from_packages(
            firmware_platform['archive'], firmware_platform['packages'],
            firmware_platform['arch'], firmware_platform['files'],
            firmware_platform['release'], args.target, path, grub_format,
            firmware_platform.get('grub_config'),
            firmware_platform.get('grub_output'), args.proposed)
        product_tree['products'][product_id]['versions'][version] = {
            'items': items
        }


def get_sha256_meta_images(url, max_items=0):
    """ Given a URL to a SHA256SUM file return a dictionary of filenames and
        SHA256 checksums keyed off the file version found as a date string in
        the filename. This is used in cases where simplestream data isn't
        avalible.
    """
    ret = dict()
    content = geturl_text(url)
    # http://cloud.centos.org/centos/ contains images using two version
    # strings. The first is only used on older images and uses the format
    # YYYYMMDD_XX. The second is used on images generated monthly using the
    # format YYMM. We know the second format is referencing the year and month
    # by looking at the timestamp of each image.
    prog = re.compile('([\d]{8}(_[\d]+))|(\d{4})')

    for i in content.split('\n'):
        try:
            sha256, img_name = i.split()
        except ValueError:
            continue
        if (not img_name.endswith('qcow2.xz') and
                not img_name.endswith('qcow2')):
            continue
        m = prog.search(img_name)
        if m is None:
            continue
        img_version = m.group(0)

        # Turn the short version string into a long version string so that MAAS
        # uses the latest version, not the longest
        if len(img_version) == 4:
            img_version = "20%s01_01" % img_version

        # Prefer compressed image over uncompressed
        if (img_version in ret and
                ret[img_version]['img_name'].endswith('qcow2.xz')):
            continue
        ret[img_version] = {
            'img_name': img_name,
            'sha256': sha256,
            }
    if max_items == 0:
        return ret
    else:
        return {
            key: ret[key]
            for key in sorted(ret.keys(), reverse=True)[:max_items]
        }


def import_qcow2(url, expected_sha256, out, curtin_files=None, packages=None):
    """ Call the maas-qcow2targz script to convert a qcow2 or qcow2.xz file at
        a given URL or local path. Return the SHA256SUM of the outputted file.
    """
    # Assume maas-qcow2targz is in the path
    qcow2targz_cmd = ["maas-qcow2targz", url, expected_sha256, out]
    if curtin_files is not None:
        curtin_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "curtin")
        qcow2targz_cmd.append('--curtin-path')
        qcow2targz_cmd.append(curtin_files.format(curtin_path=curtin_path))

    if packages is not None:
        qcow2targz_cmd.append('--packages')
        qcow2targz_cmd.append(packages)

    proc = subprocess.Popen(qcow2targz_cmd)
    proc.communicate()
    if proc.wait() != 0:
        raise subprocess.CalledProcessError(
            cmd=qcow2targz_cmd, returncode=proc.returncode)

    sha256 = hashlib.sha256()
    with open(out, 'rb') as fp:
        while True:
            chunk = fp.read(2**20)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def main_import(args):
    cfg_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "conf", args.import_cfg)
    if not os.path.exists(cfg_path):
        if os.path.exists(args.import_cfg):
            cfg_path = args.import_cfg
        else:
            print("Error: Unable to find config file %s" % args.import_cfg)
            sys.exit(1)

    with open(cfg_path) as fp:
        cfgdata = yaml.load(fp)

    target_product_stream = os.path.join(
        'streams', 'v1', cfgdata['content_id'] + '.json')

    product_tree = util.empty_iid_products(cfgdata['content_id'])
    product_tree['products'] = util.load_products(
        args.target, [target_product_stream])
    product_tree['updated'] = util.timestamp()
    product_tree['datatype'] = 'image-downloads'

    if cfgdata.get('sha256_meta_data_path', None) is not None:
        import_sha256(args, product_tree, cfgdata)
    elif cfgdata.get('bootloaders', None) is not None:
        import_bootloaders(args, product_tree, cfgdata)
    else:
        sys.stderr.write('Unsupported import yaml!')
        sys.exit(1)

    md_d = os.path.join(args.target, 'streams', 'v1')
    if not os.path.exists(md_d):
        os.makedirs(md_d)

    with open(os.path.join(args.target, target_product_stream), 'wb') as fp:
        fp.write(util.dump_data(product_tree))

    util.gen_index_and_sign(args.target, not args.no_sign)


def main():
    subc = SUBCOMMANDS['import']
    parser = argparse.ArgumentParser(description=subc['help'])

    # Top level args
    for (args, kwargs) in COMMON_ARGS:
        parser.add_argument(*args, **kwargs)

    for (args, kwargs) in subc['opts']:
        if isinstance(args, str):
            args = [args]
        parser.add_argument(*args, **kwargs)
    parser.set_defaults(action=main_import)
    parser.add_argument(
        '--proposed', help='Pull bootloaders from proposed',
        action='store_true', default=False)

    args = parser.parse_args()
    if not getattr(args, 'action', None):
        # http://bugs.python.org/issue16308
        parser.print_help()
        return 1

    return args.action(args)


if __name__ == '__main__':
    sys.exit(main())
