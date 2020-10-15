#!/usr/bin/python3

from collections import OrderedDict
from configparser import ConfigParser
from copy import deepcopy
from datetime import datetime
import argparse
import hashlib
import json
import os
import re
import shutil
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


def import_remote_config(args, product_tree, cfgdata):
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
        if 'image_index' in cfgdata:
            url = cfgdata['image_index'].format(version=path_version)
            images_unordered = get_image_index_images(url)
        else:
            raise ValueError("Undefined remote path")

        revision = release_info.get('revision')
        if revision:
            revision = str(revision)
            if revision not in images_unordered:
                raise ValueError('Revision %s does not exist!' % revision)
            images = {revision: images_unordered[revision]}
        else:
            images = OrderedDict()
            if args.max == 0:
                max_items = len(images_unordered)
            else:
                max_items = args.max
            for key in sorted(
                    images_unordered.keys(), reverse=True)[:max_items]:
                images[key] = images_unordered[key]

        base_url = os.path.dirname(url)

        if product_tree['products'].get(product_id) is None:
            print("Creating new product %s" % product_id)
            product_tree['products'][product_id] = {
                'subarches': 'generic',
                'label': 'candidate',
                'subarch': 'generic',
                'arch': arch,
                'os': os_name,
                'version': release_info['version'],
                'release': release,
                'versions': {},
            }

        for (revision, image_info) in images.items():
            version = '20%s01_%02d' % (
                revision, release_info.get('release', image_info['release']))
            if (
                    product_id in product_tree['products'] and
                    version in product_tree['products'][product_id][
                        'versions']):
                print(
                    "Product %s at version %s exists, skipping" % (
                        product_id, version))
                continue
            print(
                "Downloading and creating %s version %s" % (
                    (product_id, version)))
            image_path = '/'.join([release, arch, version, 'root-tgz'])
            real_image_path = os.path.join(
                os.path.realpath(args.target), image_path)
            if release_info.get('packages') is not None:
                packages = ','.join(release_info['packages'])
            else:
                packages = None
            sha256 = import_qcow2(
                '/'.join([base_url, image_info['file']]),
                image_info['checksum'], real_image_path,
                release_info.get('curtin_files'), packages,
                cfgdata.get('base_mirror'), cfgdata.get('epel_mirror'))
            product_tree['products'][product_id]['versions'][version] = {
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
            # Only check if the latest version in the stream matches the
            # latest version from the archive. This allows bootloaders to
            # be reverted to previous versions.
            data = versions[max(versions.keys())]
            for item in data['items'].values():
                src_package = src_packages.get(item['src_package'])
                if (
                        src_package is not None and
                        src_package['src_version'] == item['src_version'] and
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
                'label': 'candidate',
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


def get_image_index_images(url):
    """ Given a URL to an image-index config file return a dictionary of
        filenames and SHA256 checksums keyed off the revision.
    """
    ret = dict()
    content = geturl_text(url)
    config = ConfigParser()
    config.read_string(content)
    for section in config.values():
        # ConfigParser defines a 'DEFAULT' section with nothing in it...
        if section.name == 'DEFAULT':
            continue
        skip = False
        for required_key in ['name', 'file', 'revision', 'checksum']:
            if required_key not in section:
                sys.stderr.write(
                    "'%s' is undefined in section %s, skipping!\n" % (
                        required_key, section.name))
                skip = True
        if skip:
            continue

        revision = section.get('revision')
        if '_' in revision:
            revision, release = revision.split('_')
        elif '-' in revision:
            revision, release = revision.split('-')
        else:
            release = 1

        # Ignore old unsupported revision format(e.g 20150628_01)
        if len(revision) != 4:
            continue

        ret[revision] = dict(section)
        ret[revision]['release'] = release

    return ret


def import_qcow2(
        url, expected_sha256, out, curtin_files=None, packages=None,
        base_mirror=None, epel_mirror=None):
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

    if base_mirror is not None:
        qcow2targz_cmd.append('--base-mirror')
        qcow2targz_cmd.append(base_mirror)

    if epel_mirror is not None:
        qcow2targz_cmd.append('--epel-mirror')
        qcow2targz_cmd.append(epel_mirror)

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


def import_packer_maas(args, cfgdata):
    for name, data in cfgdata['packer-maas'].items():
        arch = data.get('arch', 'amd64')
        product_id = (
            "com.ubuntu.maas.candidate:{os}-bases:{version}:{arch}".format(
                arch=arch, **data)
        )
        content_id = "com.ubuntu.maas:candidate:{os}-bases-download".format(
            **data)
        target_product_stream = os.path.join(
            'streams', 'v1', content_id + '.json')

        product_tree = util.empty_iid_products(content_id)
        product_tree['products'] = util.load_products(
            args.target, [target_product_stream])
        product_tree['updated'] = util.timestamp()
        product_tree['datatype'] = 'image-ids'
        if product_tree['products'].get(product_id) is None:
            print("Creating new product %s" % product_id)
            product_tree['products'][product_id] = {
                'subarches': 'generic',
                'label': 'candidate',
                'subarch': 'generic',
                'arch': arch,
                'os': data['os'],
                'version': data['version'],
                'release': data['release'],
                'release_title': data['release_title'],
                'versions': {},
            }

        packer_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), '..', '..', 'packer-maas', name))
        if not os.path.exists(packer_dir):
            sys.exit("Error: Unable to find packer directory %s" % name)

        # Packer refuses to run if build artifacts are still around.
        for build_artifact in [
                'output-qemu', "%s.tar.gz" % name, "%s.dd.gz" % name]:
            build_artifact_path = os.path.join(packer_dir, build_artifact)
            if os.path.isdir(build_artifact_path):
                shutil.rmtree(build_artifact_path)
            elif os.path.exists(build_artifact_path):
                os.remove(build_artifact_path)

        packer_template = data.get('template', "%s.json" % name)

        if 'yum_mirror' in data:
            # A yum mirrorlist allows yum to pick the fastest mirror from
            # a set of servers given by a remote host. A yum baseurl is a
            # specific mirror. If 'yum_mirror' is given replace any
            # mirrorlists used in the kickstart file with baseurls. This
            # is needed for the builder as it only allows external access
            # to specific domains.
            orig_kickstart_path = os.path.join(
                packer_dir, 'http', "%s.ks" % name)
            kickstart_path = os.path.join(
                packer_dir, 'http', "%s-maas-images.ks" % name)
            if os.path.exists(kickstart_path):
                os.remove(kickstart_path)
            mirrorlist_re = re.compile(
                r'^.*(?P<mirrorlist>--mirrorlist=[\'"]?\S+[\'"]?)')
            # For CentOS 6
            url_re = re.compile(
                r'^\w*url\s+--url=[\'"]?(?P<url>.+/centos)\S+')
            with open(orig_kickstart_path, 'r') as orig_kickstart, open(kickstart_path, 'w') as kickstart:
                for line in orig_kickstart:
                    mirrorlist_m = mirrorlist_re.search(line)
                    url_m = url_re.search(line)
                    if mirrorlist_m is not None:
                        mirrorlist = mirrorlist_m.group('mirrorlist')
                        mirrorlist_query = mirrorlist.split('/')[-1]
                        if '?' in mirrorlist_query:
                            mirrorlist_query = mirrorlist_query.split(
                                '?', 1)[1]
                        if mirrorlist_query.endswith(("'", '"')):
                            mirrorlist_query = mirrorlist_query[:-1]
                        repo = {'yum_mirror': data['yum_mirror']}
                        for q in mirrorlist_query.split('&'):
                            k, v = q.split('=', 2)
                            repo[k] = v
                        # url and repo lines define this differently...
                        if line.startswith('url'):
                            baseurl = '--url="'
                        else:
                            baseurl = '--baseurl="'
                        if 'release' not in repo:
                            repo['release'] = repo['repo']
                        baseurl += '{yum_mirror}/{release}/{repo}/{arch}'.format(
                            **repo)
                        if data['version'] >= 8:
                            baseurl += '/os'
                        baseurl += '"'
                        line = line.replace(mirrorlist, baseurl)
                    elif url_m is not None:
                        line = line.replace(
                            url_m.group('url'), data['yum_mirror'])
                    kickstart.write(line)

            # Modify the given template to use the modified kickstart file.
            template_path = os.path.join(packer_dir, packer_template)
            with open(template_path, 'r') as f:
                template = json.load(f)
            for builder in template['builders']:
                for i, cmd in enumerate(builder['boot_command']):
                    if cmd.startswith("inst.ks"):
                        builder['boot_command'][i] = cmd.replace(
                            "%s.ks" % name, "%s-maas-images.ks" % name)
            packer_template = "%s-mass-images.json" % name
            template_path = os.path.join(packer_dir, packer_template)
            if os.path.exists(template_path):
                os.remove(template_path)
            with open(template_path, 'w') as f:
                json.dump(template, f, indent=4)

        # Add the given Curtin hooks when creating the tar from the
        # disk image.
        if 'curtin_hooks' in data:
            curtin_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "curtin")
            curtin_hooks = os.path.realpath(
                data['curtin_hooks'].format(curtin_path=curtin_path))
        else:
            curtin_hooks = ''

        # Packer must be run in the same directory as the template so the post
        # processor can convert image into something usable by MAAS.
        packer_path = os.environ.get('PACKER_PATH', 'packer')
        packer_cmd = [packer_path, 'build']
        # Set packer variables which are used to define the path to an ISO
        # if required to build the image.
        if 'packer_vars' in data:
            for key, value in data['packer_vars'].items():
                packer_cmd += ['-var', "%s=%s" % (key, value)]
        packer_cmd += [packer_template]
        env = deepcopy(os.environ)
        env['CURTIN_HOOKS'] = curtin_hooks
        proc = subprocess.run(packer_cmd, cwd=packer_dir, env=env)
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(
                cmd=' '.join(packer_cmd), returncode=proc.returncode)

        packer_image = os.path.join(packer_dir, "%s.tar.gz" % name)
        if os.path.exists(packer_image):
            ftype = 'root-tgz'
        else:
            packer_image = os.path.join(packer_dir, "%s.dd.gz" % name)
            if not os.path.exists(packer_image):
                sys.exit("Error: Unable to find image from Packer!")
            ftype = 'root-dd.gz'

        date = datetime.now().strftime("%Y%m%d")
        version_template = date + "_%02d"
        version_num = 1
        for version in product_tree['products'][product_id]['versions']:
            existing_version, existing_version_num = version.split('_')
            existing_version_num = int(existing_version_num)
            if existing_version == date and version_num < existing_version_num:
                version_num = existing_version_num + 1
        version = version_template % version_num
        while version in product_tree['products'][product_id]['versions']:
            version_num += 1
            version = version_template % version_num

        image_path = '/'.join(
            [data['os'], str(data['release']), arch, version, ftype])
        real_image_path = os.path.join(
            os.path.realpath(args.target), image_path)
        real_image_dir = os.path.dirname(real_image_path)
        if not os.path.exists(real_image_dir):
            os.makedirs(real_image_dir)
        shutil.move(packer_image, real_image_path)

        ftype_data = util.get_file_info(real_image_path)
        ftype_data['ftype'] = ftype
        ftype_data['path'] = image_path
        product_tree['products'][product_id]['versions'][version] = {
            'items': {
                ftype: ftype_data,
                }
            }

        md_d = os.path.join(args.target, 'streams', 'v1')
        if not os.path.exists(md_d):
            os.makedirs(md_d)

        with open(os.path.join(args.target, target_product_stream), 'wb') as fp:
            fp.write(util.dump_data(product_tree))

            
def main_import(args):
    cfg_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "conf", args.import_cfg)
    if not os.path.exists(cfg_path):
        if os.path.exists(args.import_cfg):
            cfg_path = args.import_cfg
        else:
            sys.exit("Error: Unable to find config file %s" % args.import_cfg)

    with open(cfg_path) as fp:
        cfgdata = yaml.safe_load(fp)

    if 'packer-maas' in cfgdata:
        import_packer_maas(args, cfgdata)
    else:
        target_product_stream = os.path.join(
            'streams', 'v1', cfgdata['content_id'] + '.json')

        product_tree = util.empty_iid_products(cfgdata['content_id'])
        product_tree['products'] = util.load_products(
            args.target, [target_product_stream])
        product_tree['updated'] = util.timestamp()
        product_tree['datatype'] = 'image-downloads'

        if cfgdata.get('image_index') is not None:
            import_remote_config(args, product_tree, cfgdata)
        elif cfgdata.get('bootloaders') is not None:
            import_bootloaders(args, product_tree, cfgdata)
        else:
            sys.exit('Unsupported import yaml!\n')

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

    args = parser.parse_args()
    if not getattr(args, 'action', None):
        # http://bugs.python.org/issue16308
        parser.print_help()
        return 1

    return args.action(args)


if __name__ == '__main__':
    sys.exit(main())
