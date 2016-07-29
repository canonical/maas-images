#!/usr/bin/python3

import argparse
import glob
import copy
import os
from functools import partial
import hashlib
import re
import shutil
import sys
import subprocess
import yaml

from meph2 import util
from meph2.url_helper import geturl_text
from meph2.commands.dpkg import (
    get_package,
    extract_files_from_packages,
)

from simplestreams import (
    contentsource,
    filters,
    mirrors,
    util as sutil,
    objectstores)

DEF_KEYRING = "/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg"

STREAMS_D = "streams/v1/"

LABELS = ('alpha1', 'alpha2', 'alpha3',
          'beta1', 'beta2', 'beta3',
          'rc', 'release')

COMMON_ARGS = []
COMMON_FLAGS = {
    'dry-run': (('-n', '--dry-run'),
                {'help': 'only report what would be done',
                 'action': 'store_true', 'default': False}),
    'no-sign': (('-u', '--no-sign'),
                {'help': 'do not re-sign files',
                 'action': 'store_true', 'default': False}),
    'max': (('--max',),
            {'help': 'keep at most N versions per product',
             'default': 2, 'type': int}),
    'orphan-data': (('orphan_data',), {'help': 'the orphan data file'}),
    'src': (('src',), {'help': 'the source streams directory'}),
    'target': (('target',), {'help': 'the target streams directory'}),
    'data_d': (('data_d',),
               {'help': ('the base data directory'
                         '("path"s are relative to this)')}),
    'keyring': (('--keyring',),
                {'help': 'gpg keyring to check sjson',
                 'default': DEF_KEYRING}),
}

SUBCOMMANDS = {
    'insert': {
        'help': 'add new items from one stream into another',
        'opts': [
            COMMON_FLAGS['dry-run'], COMMON_FLAGS['no-sign'],
            COMMON_FLAGS['keyring'],
            COMMON_FLAGS['src'], COMMON_FLAGS['target'],
            ('filters', {'nargs': '*', 'default': []}),
        ]
    },
    'import': {
        'help': 'import an image from the specified config into a stream',
        'opts': [
            COMMON_FLAGS['no-sign'], COMMON_FLAGS['keyring'],
            ('import_cfg', {'help':
                            'The config file for the image to import.'}),
            COMMON_FLAGS['target'],
            ]
    },
    'merge': {
        'help': 'merge two product streams together',
        'opts': [
            COMMON_FLAGS['no-sign'],
            COMMON_FLAGS['src'], COMMON_FLAGS['target'],
            ]
    },
    'promote': {
        'help': 'promote a product/version from daily to release',
        'opts': [
            COMMON_FLAGS['dry-run'], COMMON_FLAGS['no-sign'],
            COMMON_FLAGS['keyring'],
            (('-l', '--label'),
             {'default': 'release', 'choices': LABELS,
              'help': 'the label to use'}),
            (('--skip-file-copy',),
             {'help': 'do not copy files, only metadata [TEST_ONLY]',
              'action': 'store_true', 'default': False}),
            COMMON_FLAGS['src'], COMMON_FLAGS['target'],
            ('version', {'help': 'the version_id to promote.'}),
            ('filters', {'nargs': '+', 'default': []}),
        ]
    },
    'clean-md': {
        'help': 'clean streams metadata only to keep "max" items',
        'opts': [
            COMMON_FLAGS['dry-run'], COMMON_FLAGS['no-sign'],
            COMMON_FLAGS['keyring'],
            ('max', {'type': int}), ('target', {}),
            ('filters', {'nargs': '*', 'default': []}),
        ]
    },
    'find-orphans': {
        'help': 'find files in data_d not referenced in a "path"',
        'opts': [
            COMMON_FLAGS['orphan-data'], COMMON_FLAGS['data_d'],
            COMMON_FLAGS['keyring'],
            ('streams_dirs', {'nargs': '*', 'default': []}),
        ],
    },
    'reap-orphans': {
        'help': 'reap orphans listed in orphan-data from data_d',
        'opts': [
            COMMON_FLAGS['orphan-data'], COMMON_FLAGS['dry-run'],
            COMMON_FLAGS['data_d'],
            ('--older', {'default': '3d',
                         'help': ('only remove files orphaned longer than'
                                  'this. if no unit given, default is days.')
                         }),
        ],
    },
    'sign': {
        'help': 'Regenerate index.json and sign the stream',
        'opts': [
            COMMON_FLAGS['data_d'], COMMON_FLAGS['no-sign'],
        ],
    },
}


class BareMirrorWriter(mirrors.ObjectFilterMirror):
    # this explicitly avoids reference counting and .data/ storage
    # it stores both metadata (streams/*) and files (path elements).
    # items with path will still be copied.
    def __init__(self, config, objectstore):
        super(BareMirrorWriter, self).__init__(config=config,
                                               objectstore=objectstore)
        self.store = objectstore
        self.config = config
        self.tproducts = None
        self.tcontent_id = None
        self.inserted = {}
        self.removed_versions = []

    def _noop(*args):
        return

    _inc_rc = _noop
    _dec_rc = _noop

    def products_data_path(self, content_id):
        return "streams/v1/" + content_id + ".json"

    def load_products(self, path, content_id):
        sys.stderr.write("content_id=%s path=%s\n" % (content_id, path))
        ret = super(BareMirrorWriter, self).load_products(
            path=path, content_id=content_id)
        if not ret:
            ret = util.empty_iid_products(content_id)
        self.tcontent_id = content_id
        self.tproducts = copy.deepcopy(ret)
        return ret

    def insert_item(self, data, src, target, pedigree, contentsource):
        sys.stderr.write("inserting item %s\n" % '/'.join(pedigree))
        if self.tcontent_id not in self.inserted:
            self.inserted[self.tcontent_id] = []
        self.inserted[self.tcontent_id].append(
            (pedigree, sutil.products_exdata(
                src, pedigree, include_top=False,
                insert_fieldnames=False)),)
        return super(BareMirrorWriter, self).insert_item(
            data, src, target, pedigree, contentsource)

    def remove_version(self, data, src, target, pedigree):
        # sync doesnt filter on things to be removed, so
        # we have to do that here.
        if not filters.filter_item(self.filters, data, src, pedigree):
            return

        self.removed_versions.append(pedigree)

    def remove_item(self, data, src, target, pedigree):
        return

    def insert_products(self, path, target, content):
        # insert_item and insert_products would not be strictly necessary
        # they're here, though, to keep a list of those things appended.
        # it allows us to more easily/completely prune a products tree.
        # and also to aid in ReleasePromoteMirror's translation of product
        # names.
        sys.stderr.write("adding products %s\n" % path)
        if self.tcontent_id not in self.inserted:
            self.inserted[self.tcontent_id] = []

        ptouched = set([i[0][0] for i in self.inserted[self.tcontent_id]])
        srcitems = []

        # collect into srcitems a list of all items in the source
        # that are in a product that we touched.
        def get_items(item, tree, pedigree):
            if pedigree[0] not in ptouched:
                return

            flat = sutil.products_exdata(tree, pedigree, include_top=False,
                                         insert_fieldnames=False)
            srcitems.append([pedigree, flat])

        sutil.walk_products(self.tproducts, cb_item=get_items)

        # empty products entries in the target tree for all those we modified
        for pid in ptouched:
            self.tproducts['products'][pid] = {}

        known_ints = ['size']
        for (pedigree, flatitem) in srcitems + self.inserted[self.tcontent_id]:
            for n in known_ints:
                if n in flatitem:
                    flatitem[n] = int(flatitem[n])
            sutil.products_set(self.tproducts, flatitem, pedigree)

        for pedigree in self.removed_versions:
            sutil.products_del(self.tproducts, pedigree)

        sutil.products_condense(
            self.tproducts,
            sticky=['di_version', 'kpackage', 'sha256', 'md5', 'path'])

        self.tproducts['updated'] = sutil.timestamp()

        ret = super(BareMirrorWriter, self).insert_products(
            path=path, target=self.tproducts, content=False)
        return ret


class InsertBareMirrorWriter(BareMirrorWriter):
    # this just no-ops remove_* so it never will occur
    remove_item = BareMirrorWriter._noop
    remove_version = BareMirrorWriter._noop
    remove_product = BareMirrorWriter._noop
    insert_index_entry = BareMirrorWriter._noop


class ReleasePromoteMirror(InsertBareMirrorWriter):
    # this does not do reference counting or .data/ storage
    # it converts a daily item to a release item and inserts it.

    # we take care of writing file in insert_products
    insert_index_entry = BareMirrorWriter._noop

    def __init__(self, config, objectstore, label):
        super(ReleasePromoteMirror, self).__init__(config=config,
                                                   objectstore=objectstore)
        self.label = label

    def rel2daily(self, ptree):
        ret = copy.deepcopy(ptree)
        ret['content_id'] = self.fixed_content_id(ret['content_id'])

        for oname in [o for o in ptree.get('products', {})]:
            newname = self.fixed_product_id(oname)
            ptree['products'][newname] = ptree['products'][oname]
            del ptree['products'][oname]

    def fixed_content_id(self, content_id):
        # when promoting from daily, our content ids get ':daily' removed
        #  com.ubuntu.maas:daily:v2:download => com.ubuntu.maas:v2:download
        return(content_id.replace(":daily", ""))

    def fixed_pedigree(self, pedigree):
        return tuple([self.fixed_product_id(pedigree[0])] + list(pedigree[1:]))

    def fixed_product_id(self, product_id):
        # when promoting from daily, product ids get '.daily' removed
        #  com.ubuntu.maas.daily:v2:boot:13.10:armhf:generic-lpae ->
        #     com.ubuntu.maas:v2:boot:13.10:armhf:generic-lpae
        return product_id.replace(".daily:", ":")

    def load_products(self, path, content_id):
        # this loads the released products, but returns it in form
        # of daily products
        ret = super(ReleasePromoteMirror, self).load_products(
            path=path, content_id=self.fixed_content_id(content_id))
        return self.rel2daily(ret)

    def insert_item(self, data, src, target, pedigree, contentsource):
        ret = super(ReleasePromoteMirror, self).insert_item(
            data, src, target, pedigree, contentsource)
        # update the label and pedigree of the item that superclass added.
        (ped, item_flat) = self.inserted[self.tcontent_id][-1]
        item_flat['label'] = self.label
        self.inserted[self.tcontent_id][-1] = (
            self.fixed_pedigree(ped), item_flat)
        return ret

    def insert_products(self, path, target, content):
        path = self.fixed_content_id(path)
        ret = super(ReleasePromoteMirror, self).insert_products(
            path=path, target=self.tproducts, content=False)
        return ret


class DryRunMirrorWriter(mirrors.DryRunMirrorWriter):
    removed_versions = []
    tcontent_id = None

    def load_products(self, path, content_id):
        self.tcontent_id = content_id
        return super(DryRunMirrorWriter, self).load_products(path, content_id)

    def remove_version(self, data, src, target, pedigree):
        # src and target are top level products:1.0
        # data is src['products'][ped[0]]['versions'][ped[1]]

        # sync doesnt filter on things to be removed, so
        # we have to do that here..
        if not filters.filter_item(self.filters, data, src, pedigree):
            return
        super(DryRunMirrorWriter, self).remove_version(self,
                                                       data, src,
                                                       target, pedigree)
        self.removed_versions.append((self.tcontent_id, pedigree,))


def get_sha256_meta_images(url):
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
    return ret


def import_qcow2(url, expected_sha256, out, curtin_files=None):
    """ Call the maas-qcow2targz script to convert a qcow2 or qcow2.xz file at
        a given URL or local path. Return the SHA256SUM of the outputted file.
    """
    # Assume maas-qcow2targz is in the path
    qcow2targz_cmd = ["maas-qcow2targz", url, expected_sha256, out]
    if curtin_files:
        curtin_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "curtin")
        qcow2targz_cmd.append(curtin_files.format(curtin_path=curtin_path))
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


def load_product_streams(src):
    index_path = os.path.join(src, STREAMS_D, "index.json")
    if not os.path.exists(index_path):
        return []
    with contentsource.UrlContentSource(index_path) as tcs:
        index = sutil.load_content(tcs.read())
    return [product['path'] for product in index['index'].values()]


def load_products(path, product_streams):
    products = {}
    for product_stream in product_streams:
        product_stream_path = os.path.join(path, product_stream)
        if os.path.exists(product_stream_path):
            with contentsource.UrlContentSource(
                    product_stream_path) as tcs:
                product_listing = sutil.load_content(tcs.read())
                products.update(product_listing['products'])
    return products


def gen_index_and_sign(data_d, sign=True):
    md_d = os.path.join(data_d, "streams", "v1")
    if not os.path.exists(md_d):
        os.makedirs(md_d)
    index = util.create_index(md_d, files=None)
    with open(os.path.join(md_d, "index.json"), "wb") as fp:
        fp.write(util.dump_data(index))

    if sign:
        util.sign_streams_d(md_d)


def main_insert(args):
    (src_url, src_path) = sutil.path_from_mirror_url(args.src, None)
    filter_list = filters.get_filters(args.filters)

    mirror_config = {'max_items': 20, 'keep_items': True,
                     'filters': filter_list}

    policy = partial(util.endswith_policy, src_path, args.keyring)
    smirror = mirrors.UrlMirrorReader(src_url, policy=policy)

    if args.dry_run:
        smirror = mirrors.UrlMirrorReader(src_url, policy=policy)
        tstore = objectstores.FileStore(args.target)
        drmirror = DryRunMirrorWriter(config=mirror_config, objectstore=tstore)
        drmirror.sync(smirror, src_path)
        for (pedigree, path, size) in drmirror.downloading:
            fmt = "{pedigree} {path}"
            sys.stderr.write(
                fmt.format(pedigree='/'.join(pedigree), path=path) + "\n")
        return 0

    smirror = mirrors.UrlMirrorReader(src_url, policy=policy)
    tstore = objectstores.FileStore(args.target)
    tmirror = InsertBareMirrorWriter(config=mirror_config, objectstore=tstore)
    tmirror.sync(smirror, src_path)

    gen_index_and_sign(args.target)
    return 0


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
        images = get_sha256_meta_images(url)
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
            sha256 = import_qcow2(
                '/'.join([base_url, image_info['img_name']]),
                image_info['sha256'], real_image_path,
                release_info.get('curtin_files'))
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


def get_file_info(f):
    size = 0
    sha256 = hashlib.sha256()
    with open(f, 'rb') as f:
        for chunk in iter(lambda: f.read(2**15), b''):
            sha256.update(chunk)
            size += len(chunk)
    return sha256.hexdigest(), size


def import_bootloaders(args, product_tree, cfgdata):
    for bootloader in cfgdata['bootloaders']:
        product_id = cfgdata['product_id'].format(
            bootloader=bootloader['bootloader'])
        package = get_package(
            bootloader['archive'], bootloader['packages'][0],
            bootloader['arch'], bootloader['release'])

        if (
                product_id in product_tree['products'] and
                package['Version'] in product_tree['products'][product_id][
                    'versions']):
            print(
                "Product %s at version %s exists, skipping" % (
                    product_id, package['Version']))
            continue
        if product_tree['products'].get(product_id) is None:
            print("Creating new product %s" % product_id)
            product_tree['products'][product_id] = {
                'label': 'daily',
                'arch': bootloader['arch'],
                'subarch': 'generic',
                'subarches': 'generic',
                'os': 'bootloader',
                'release': bootloader['bootloader'],
                'versions': {},
                }
        path = os.path.join(
            'bootloaders', bootloader['bootloader'], bootloader['arch'],
            package['Version'])
        dest = os.path.join(args.target, path)
        os.makedirs(dest)
        grub_format = bootloader.get('grub_format')
        if grub_format is not None:
            dest = os.path.join(dest, bootloader['grub_output'])
        print(
            "Downloading and creating %s version %s" % (
                product_id, package['Version']))
        extract_files_from_packages(
            bootloader['archive'], bootloader['packages'],
            bootloader['arch'], bootloader['files'], bootloader['release'],
            dest, grub_format, bootloader.get('grub_config'))
        if grub_format is not None:
            sha256, size = get_file_info(dest)
            product_tree['products'][product_id]['versions'][
                package['Version']] = {
                'items': {
                    bootloader['grub_output']: {
                        'ftype': 'bootloader',
                        'sha256': sha256,
                        'path': os.path.join(
                            path, bootloader['grub_output']),
                        'size': size,
                        }
                    }
                }
        else:
            items = {}
            for i in bootloader['files']:
                basename = os.path.basename(i)
                dest_file = os.path.join(dest, basename)
                if '*' in dest_file or '?' in dest_file:
                    # Process multiple files copied with a wildcard
                    unglobbed_files = glob.glob(dest_file)
                elif ',' in dest_file:
                    # If we're renaming the file from the package use the new
                    # name.
                    _, basename = i.split(',')
                    dest_file = os.path.join(dest, basename)
                    unglobbed_files = [dest_file]
                else:
                    unglobbed_files = [dest_file]
                for f in unglobbed_files:
                    basename = os.path.basename(f)
                    sha256, size = get_file_info(f)
                    items[basename] = {
                        'ftype': 'bootloader',
                        'sha256': sha256,
                        'path': os.path.join(path, basename),
                        'size': size,
                    }
                product_tree['products'][product_id]['versions'][
                    package['Version']] = {'items': items}


def main_import(args):
    cfg_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "conf", args.import_cfg)
    if not os.path.exists(cfg_path):
        if os.path.exists(args.import_cfg):
            cfg_path = args.import_cfg
        else:
            print("Error: Unable to find config file %s" % args.import_cfg)
            os.exit(1)

    with open(cfg_path) as fp:
        cfgdata = yaml.load(fp)

    target_product_stream = os.path.join(
        'streams', 'v1', cfgdata['content_id'] + '.json')

    product_tree = util.empty_iid_products(cfgdata['content_id'])
    product_tree['products'] = load_products(
        args.target, [target_product_stream])
    product_tree['updated'] = sutil.timestamp()
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

    gen_index_and_sign(args.target, not args.no_sign)


def main_merge(args):
    src_product_streams = load_product_streams(args.src)
    target_product_streams = load_product_streams(args.target)
    src_products = load_products(args.src, src_product_streams)
    target_products = load_products(args.target, target_product_streams)

    for (product_name, product_info) in src_products.items():
        for (version, version_info) in product_info['versions'].items():
            for (item, item_info) in version_info['items'].items():
                if product_name in target_products:
                    target_product = target_products[product_name]
                    target_version = target_product['versions'][version]
                    target_item = target_version['items'][item]
                    if item_info['sha256'] != target_item['sha256']:
                        sys.stderr.write(
                            "Error: SHA256 of %s and %s do not match!\n" %
                            (item_info['path'], target_item['path']))
                        sys.exit(1)
                    else:
                        continue
                file_src = os.path.join(args.src, item_info['path'])
                file_target = os.path.join(args.target, item_info['path'])
                target_dir = os.path.dirname(file_target)
                if not os.path.exists(target_dir):
                    os.makedirs(target_dir)
                shutil.copy2(file_src, file_target)
    for product_stream in src_product_streams:
        shutil.copy2(
            os.path.join(args.src, product_stream),
            os.path.join(args.target, product_stream))

    gen_index_and_sign(args.target, not args.no_sign)


def main_promote(args):
    (src_url, src_path) = sutil.path_from_mirror_url(args.src, None)
    filter_list = filters.get_filters(args.filters)

    filter_list.extend(filters.get_filters(['version_name=%s' % args.version]))
    print("filter_list=%s" % filter_list)

    mirror_config = {'max_items': 100, 'keep_items': True,
                     'filters': filter_list,
                     'item_download': not args.skip_file_copy}

    policy = partial(util.endswith_policy, src_path, args.keyring)

    if args.dry_run:
        smirror = mirrors.UrlMirrorReader(src_url, policy=policy)
        tstore = objectstores.FileStore(args.target)
        drmirror = DryRunMirrorWriter(config=mirror_config, objectstore=tstore)
        drmirror.sync(smirror, src_path)
        for (pedigree, path, size) in drmirror.downloading:
            fmt = "{pedigree} {path}"
            sys.stderr.write(
                fmt.format(pedigree='/'.join(pedigree), path=path) + "\n")
        return 0

    smirror = mirrors.UrlMirrorReader(src_url, policy=policy)
    tstore = objectstores.FileStore(args.target)
    tmirror = ReleasePromoteMirror(config=mirror_config, objectstore=tstore,
                                   label=args.label)
    tmirror.sync(smirror, src_path)

    gen_index_and_sign(args.target, not args.no_sign)
    return 0


def main_clean_md(args):
    (mirror_url, mirror_path) = sutil.path_from_mirror_url(args.target, None)
    filter_list = filters.get_filters(args.filters)

    mirror_config = {'max_items': args.max, 'keep_items': False,
                     'filters': filter_list}

    policy = partial(util.endswith_policy, mirror_path, args.keyring)

    if args.dry_run:
        smirror = mirrors.UrlMirrorReader(mirror_url, policy=policy)
        tstore = objectstores.FileStore(mirror_url)
        drmirror = DryRunMirrorWriter(config=mirror_config, objectstore=tstore)
        drmirror.sync(smirror, mirror_path)
        for content_id, pedigree in drmirror.removed_versions:
            sys.stderr.write("remove " + content_id + " " +
                             '/'.join(pedigree) + "\n")
        return 0

    smirror = mirrors.UrlMirrorReader(mirror_url, policy=policy)
    tstore = objectstores.FileStore(mirror_url)
    tmirror = BareMirrorWriter(config=mirror_config, objectstore=tstore)
    tmirror.sync(smirror, mirror_path)

    gen_index_and_sign(mirror_url, not args.no_sign)
    return 0


def main_find_orphans(args):
    data_d = args.data_d
    streams_d = args.streams_dirs
    if os.path.exists(os.path.join(data_d, 'streams/v1')) and not streams_d:
        streams_d.append(data_d)

    # used to check validity of existent orphan file at beginning
    if os.path.exists(args.orphan_data):
        util.read_orphan_file(args.orphan_data)

    orphans = []

    non_orphans = util.get_nonorphan_set(streams_d, data_d, args.keyring)

    for (path, dirs, files) in os.walk(data_d):
        if os.path.join(path, '').startswith(
                os.path.join(data_d, 'streams', '')):
            continue
        if os.path.join(path, '').startswith(
                os.path.join(data_d, '.data', '')):
            continue

        for file_ in files:
            location = os.path.relpath(os.path.join(path, file_), data_d)
            if location not in non_orphans:
                orphans.append(location)

    util.write_orphan_file(args.orphan_data, orphans)
    return 0


def main_reap_orphans(args):
    data_d = args.data_d
    known_orphans = util.read_orphan_file(args.orphan_data)

    now = util.read_timestamp(sutil.timestamp())
    delta = util.read_timedelta(args.older)
    reaped = set()

    for orphan, when in known_orphans.items():
        location = os.path.join(data_d, orphan)
        if not util.read_timestamp(when) + delta < now:
            continue
        if args.dry_run:
            sys.stderr.write('Reaping %s orphaned on %s\n' % (orphan, when))
        else:
            sutil.rm_f_file(location)
            reaped.add(orphan)
            try:
                os.removedirs(os.path.dirname(location))
            except:
                pass

    if not args.dry_run:
        util.write_orphan_file(args.orphan_data, known_orphans.keys() - reaped)
    return 0


def main_sign(args):
    gen_index_and_sign(args.data_d)
    return 0


def main():
    parser = argparse.ArgumentParser()

    # Top level args
    for (args, kwargs) in COMMON_ARGS:
        parser.add_argument(*args, **kwargs)

    subparsers = parser.add_subparsers()
    for subcmd in sorted(SUBCOMMANDS.keys()):
        val = SUBCOMMANDS[subcmd]
        sparser = subparsers.add_parser(subcmd, help=val['help'])
        mfuncname = 'main_' + subcmd.replace('-', '_')
        sparser.set_defaults(action=globals()[mfuncname])
        for (args, kwargs) in val['opts']:
            if isinstance(args, str):
                args = [args]
            sparser.add_argument(*args, **kwargs)

    args = parser.parse_args()
    if not getattr(args, 'action', None):
        # http://bugs.python.org/issue16308
        parser.print_help()
        return 1

    return args.action(args)


if __name__ == '__main__':
    sys.exit(main())

# vi: ts=4 expandtab syntax=python
