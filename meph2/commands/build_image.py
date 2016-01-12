#!/usr/bin/python3

from simplestreams import log

from meph2 import DEF_MEPH2_CONFIG
from meph2.stream import ALL_ITEM_TAGS, create_version

import argparse
import sys
import yaml


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


if __name__ == '__main__':
    main()

# vi: ts=4 expandtab syntax=python
