#!/usr/bin/python3

from simplestreams import util as sutil
from simplestreams import contentsource
from simplestreams import log
from simplestreams.log import LOG
from simplestreams import mirrors
from simplestreams import filters

from meph2 import util, DEF_MEPH2_CONFIG
from meph2.stream import ALL_ITEM_TAGS, CONTENT_ID, create_version

import argparse
import copy
import os
import sys
import yaml

CLOUD_IMAGES_DAILY = ("http://cloud-images.ubuntu.com/daily/"
                      "streams/v1/com.ubuntu.cloud:daily:download.json")
MAAS_EPHEM2_DAILY = ("http://maas.ubuntu.com/images/ephemeral-v2/daily/"
                     "streams/v1/com.ubuntu.maas:daily:v2:download.json")

FORCE_URL = "force"  # a fake target url that will have nothing in it
DEFAULT_ARCHES = {
    'i386': ['i386'],
    'i586': ['i386'],
    'i686': ['i386'],
    'x86_64': ['i386', 'amd64', 'armhf', 'arm64'],
    'ppc64le': ['ppc64el'],
    'armhf': ['armhf'],
    'aarch64': ['arm64', 'armhf'],
}


def v2_to_cloudimg_products(prodtree):
    # this turns a v2 products tree into a cloud-image products tree.
    # it pays attention only to products with krel == release
    # (in an attempt to only get "primary")
    ret = util.empty_iid_products("com.ubuntu.cloud:daily:download")
    for product in prodtree.get('products'):
        if not (prodtree['products'][product].get('krel') ==
                prodtree['products'][product].get('release')):
            continue

        # com.ubuntu.maas:boot:12.04:amd64:hwe-s =>
        # com.ubuntu.cloud.daily:server:12.04:amd64
        tprod = ("com.ubuntu.cloud.daily:server:%(version)s:%(arch)s" %
                 prodtree['products'][product])

        if tprod not in ret['products']:
            ret['products'][tprod] = {'versions': {}}
        for vername in prodtree['products'][product].get('versions'):
            if vername not in ret['products'][tprod]['versions']:
                ret['products'][tprod]['versions'][vername] = {}

    return ret


class CloudImg2Meph2Sync(mirrors.BasicMirrorWriter):
    def __init__(self, config, out_d, target, v2config, verbosity=0):
        super(CloudImg2Meph2Sync, self).__init__(config=config)
        self.out_d = out_d
        self.target = target
        self.v2config = v2config
        self.filters = self.config.get('filters', [])

        with open(v2config) as fp:
            cfgdata = yaml.load(fp)
        self.cfgdata = cfgdata
        self.releases = [k['release'] for k in self.cfgdata['releases']]
        arches = set()
        for r in cfgdata['releases']:
            for k in r['kernels']:
                arches.add(k[1])
        self.arches = arches
        self._di_kinfo = {}
        self.content_t = None

    def load_products(self, path=None, content_id=None):
        if content_id != "com.ubuntu.cloud:daily:download":
            raise ValueError("Not expecting to sync with content_id: %s" %
                             content_id)

        if self.target == FORCE_URL:
            my_prods = util.empty_iid_products(CONTENT_ID)
        else:
            with contentsource.UrlContentSource(self.target) as tcs:
                my_prods = sutil.load_content(tcs.read())

        # need the list syntax to not update the dict in place
        for p in [p for p in my_prods['products']]:
            if "daily:v2" not in p:
                LOG.warn("skipping old product %s" % p)
                del(my_prods['products'][p])

        self.content_t = my_prods
        return v2_to_cloudimg_products(my_prods)

    def insert_item(self, data, src, target, pedigree, contentsource):
        # create the ephemeral root

        flat = sutil.products_exdata(src, pedigree)
        arch = flat['arch']
        release = flat['release']
        vername = flat['version_name']

        # these are copied from the source stream if they're present
        copy_if_avail = ('os', 'os_title', 'release_title', 'release_codename')
        all_item_tags = ALL_ITEM_TAGS.copy()
        all_item_tags.update({k: flat[k] for k in copy_if_avail if k in flat})

        cvret = create_version(
            arch=arch, release=release, version_name=vername,
            img_url=contentsource.url, out_d=self.out_d, include_di=True,
            cfgdata=self.cfgdata, common_tags=all_item_tags)

        for prodname, items in cvret.items():
            for i in items:
                sutil.products_set(self.content_t, items[i],
                                   (prodname, vername, i))

    def insert_products(self, path, target, content):
        tree = copy.deepcopy(self.content_t)
        sutil.products_prune(tree)
        # stop these items from copying up when we call condense
        sutil.products_condense(tree,
                                sticky=['di_version', 'kpackage'])

        tsnow = sutil.timestamp()
        tree['updated'] = tsnow
        tree['datatype'] = 'image-downloads'

        dpath = "streams/v1/" + CONTENT_ID + ".json"
        fdpath = os.path.join(self.out_d, dpath)
        sdir = os.path.dirname(fdpath)
        LOG.info("writing data: %s", dpath)

        if not os.path.isdir(sdir):
            os.makedirs(sdir)

        with open(fdpath, "wb") as fp:
            fp.write(sutil.dump_data(tree))

        # now insert or update an index
        LOG.info("updating index in %s" % sdir)
        index = util.create_index(sdir)
        with open(os.path.join(sdir, "index.json"), "wb") as fp:
            fp.write(sutil.dump_data(index) + b"\n")

    def filter_index_entry(self, data, src, pedigree):
        if pedigree[0] != "com.ubuntu.cloud:daily:download":
            LOG.info("skipping index entry %s" % '/'.join(pedigree))
            return False
        return True

    def filter_product(self, data, src, target, pedigree):
        flat = sutil.products_exdata(src, pedigree)
        if flat['release'] not in self.releases:
            return False
        if flat['arch'] not in self.arches:
            return False
        return True

    def filter_item(self, data, src, target, pedigree):
        if data['ftype'] != "tar.gz":
            return False
        return filters.filter_item(self.filters, data, src, pedigree)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument('--max', type=int, default=1,
                        help='store at most MAX items in the target')
    parser.add_argument('--dry-run', action='store_true', default=False,
                        help='only report what would be done')
    parser.add_argument('--arches', action='append',
                        default=[], help='which arches to build, "," delim')
    parser.add_argument('--source', default=CLOUD_IMAGES_DAILY,
                        help='cloud images mirror')
    parser.add_argument('--target', default=MAAS_EPHEM2_DAILY,
                        help="maas ephemeral v2 mirror.  "
                             'Use "%s" to force build [DEV ONLY!]' % FORCE_URL)
    parser.add_argument('--keyring', action='store', default=None,
                        help='keyring to be specified to gpg via --keyring')
    parser.add_argument('--config', default=DEF_MEPH2_CONFIG, help='v2 config')
    parser.add_argument('--verbose', '-v', action='count', default=0)
    parser.add_argument('--log-file', default=sys.stderr,
                        type=argparse.FileType('w'))

    parser.add_argument('output_d')
    parser.add_argument('filters', nargs='*', default=[])

    args = parser.parse_args()

    if len(args.arches) == 0:
        try:
            karch = os.uname()[4]
            arches = DEFAULT_ARCHES[karch]
        except KeyError:
            msg = "No default arch list for kernel arch '%s'. Try '--arches'."
            sys.stderr.write(msg % karch + "\n")
            return False
    else:
        arches = []
        for f in args.arches:
            arches.extend(f.split(","))

    arch_filter = "arch~(" + "|".join(arches) + ")"

    filter_list = filters.get_filters([arch_filter] + args.filters)

    (source_url, initial_path) = sutil.path_from_mirror_url(args.source, None)

    def policy(content, path):  # pylint: disable=W0613
        if initial_path.endswith('sjson'):
            return sutil.read_signed(content, keyring=args.keyring)
        else:
            return content

    mirror_config = {'max_items': args.max, 'filters': filter_list}

    vlevel = min(args.verbose, 2)
    level = (log.ERROR, log.INFO, log.DEBUG)[vlevel]
    log.basicConfig(stream=args.log_file, level=level)

    smirror = mirrors.UrlMirrorReader(source_url, policy=policy)

    LOG.info(
        "summary: \n " + '\n '.join([
            "source: %s" % args.source,
            "target: %s" % args.target,
            "output: %s" % args.output_d,
            "arches: %s" % args.arches,
            "filters: %s" % filter_list,
        ]) + '\n')

    tmirror = CloudImg2Meph2Sync(config=mirror_config, out_d=args.output_d,
                                 target=args.target, v2config=args.config,
                                 verbosity=vlevel)

    tmirror.sync(smirror, initial_path)


if __name__ == '__main__':
    main()

# vi: ts=4 expandtab syntax=python
