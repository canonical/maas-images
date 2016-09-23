#!/usr/bin/python3

from simplestreams import log
from simplestreams import util as sutil

from meph2 import DEF_MEPH2_CONFIG, util
from meph2.stream import (
    create_version, IMAGE_FORMATS)

import argparse
import copy
import json
import os
import sys
import yaml


def dump_stream_data(out_d, cvdata, content_id, version_name):
    # cvdata is a dictionary of
    #  {product_name: {'item_name': {}, 'item2_name': {}}
    prod_tree = util.empty_iid_products(content_id)
    for prodname, items in cvdata.items():
        for i in items:
            sutil.products_set(prod_tree, items[i],
                               (prodname, version_name, i))

    sutil.products_prune(prod_tree, preserve_empty_products=True)
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
        fp.write(util.dump_data(prod_tree))

    # now insert or update an index
    index = util.create_index(sdir)
    with open(os.path.join(sdir, "index.json"), "wb") as fp:
        fp.write(util.dump_data(index))


def dump_json_data(fname, cvdata, version_name):
    item_list = []
    for prodname, items in cvdata.items():
        for item_name, item in items.items():
            cur = items[item_name].copy()
            cur.update({'product_name': prodname,
                        'version_name': version_name,
                        'item_name': item_name})
            item_list.append(cur)
    with open(fname, "w") as fp:
        fp.write(json.dumps(item_list, indent=2,
                            sort_keys=True, separators=(',', ': ')))


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument('--dry-run', action='store_true', default=False,
                        help='only report what would be done')
    parser.add_argument('--enable-di', action='store_true', default=False)
    parser.add_argument('--config', default=DEF_MEPH2_CONFIG, help='v2 config')
    parser.add_argument('--image-format', default=None,
                        help='format of img in img_url.',
                        choices=IMAGE_FORMATS)
    parser.add_argument('--flat-json', metavar='FILE', default=None,
                        help='dump json metadata to FILE')
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
        verbosity=vlevel, img_format=args.image_format)

    dump_stream_data(args.output_d, copy.deepcopy(cvret),
                     cfgdata['content_id'], args.version_name)
    if args.flat_json:
        dump_json_data(args.flat_json, cvret, args.version_name)
    sys.exit(0)


if __name__ == '__main__':
    main()

# vi: ts=4 expandtab syntax=python
