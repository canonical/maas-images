#!/usr/bin/python3

from simplestreams import log
from simplestreams import util as sutil

from meph2 import DEF_MEPH2_CONFIG, util
from meph2.stream import ALL_ITEM_TAGS, CONTENT_ID, create_version

import argparse
import os
import sys
import yaml


def dump_data(out_d, items, content_id, version_name):
    # items is a dictionary of product_name: [item1_dict, item2_dict...]
    prod_tree = util.empty_iid_products(content_id)
    for prodname, items in items.items():
        for i in items:
            sutil.products_set(prod_tree, items[i],
                               (prodname, version_name, i))

    sutil.products_prune(prod_tree)
    sutil.products_condense(prod_tree, sticky=['di_version', 'kpackage'])

    tsnow = sutil.timestamp()
    prod_tree['updated'] = tsnow
    prod_tree['datatype'] = 'image-downloads'

    dpath = "streams/v1/" + content_id + ".json"
    fdpath = os.path.join(out_d, dpath)
    sdir = os.path.dirname(fdpath)

    if not os.path.isdir(sdir):
        os.makedirs(sdir)

    with open(fdpath, "wb") as fp:
        fp.write(sutil.dump_data(prod_tree))

    # now insert or update an index
    index = util.create_index(sdir)
    with open(os.path.join(sdir, "index.json"), "wb") as fp:
        fp.write(sutil.dump_data(index) + b"\n")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument('--dry-run', action='store_true', default=False,
                        help='only report what would be done')
    parser.add_argument('--enable-di', action='store_true', default=False)
    parser.add_argument('--config', default=DEF_MEPH2_CONFIG, help='v2 config')
    parser.add_argument('--verbose', '-v', action='count', default=0)
    parser.add_argument('--log-file', default=sys.stderr,
                        type=argparse.FileType('w'))

    parser.add_argument('arch', help='dpkg arch to build for')
    parser.add_argument('release',
                        help='ubuntu release/suite (trusty, xenial ...)')
    parser.add_argument('version_name', help='build_serial/version_name')
    parser.add_argument(
        'img_url', help='source image to build from.  will not be modified.')
    parser.add_argument('output_d')

    args = parser.parse_args()

    vlevel = min(args.verbose, 2)
    level = (log.ERROR, log.INFO, log.DEBUG)[vlevel]
    log.basicConfig(stream=args.log_file, level=level)

    with open(args.config, "r") as fp:
        cfgdata = yaml.load(fp)

    cvret = create_version(
        arch=args.arch, release=args.release, version_name=args.version_name,
        img_url=args.img_url, out_d=args.output_d,
        include_di=args.enable_di, cfgdata=cfgdata,
        common_tags=ALL_ITEM_TAGS,
        verbosity=vlevel)

    dump_data(args.output_d, cvret, CONTENT_ID, args.version_name)


if __name__ == '__main__':
    main()

# vi: ts=4 expandtab syntax=python
