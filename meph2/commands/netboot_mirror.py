#!/usr/bin/python3
#   Copyright (C) 2015 Canonical Ltd.
#
#   Author: Scott Moser <scott.moser@canonical.com>
#
#   Simplestreams is free software: you can redistribute it and/or modify it
#   under the terms of the GNU Affero General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or (at your
#   option) any later version.
#
#   Simplestreams is distributed in the hope that it will be useful, but
#   WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
#   or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Affero General Public
#   License for more details.
#
#   You should have received a copy of the GNU Affero General Public License
#   along with Simplestreams.  If not, see <http://www.gnu.org/licenses/>.
import argparse
import sys

from simplestreams import filters
from simplestreams import log
from simplestreams import mirrors
from simplestreams import objectstores

from ..netinst import NetbootMirrorReader, CONTENT_ID


class DotProgress(object):
    def __init__(self, expected=None, columns=80):
        self.curpath = None
        self.printed = None
        self.expected = expected
        self.bytes_read = 0
        self.columns = columns

    def write_progress(self, path, cur, total):
        if self.curpath != path:
            self.printed = 0
            self.curpath = path
            status = ""
            if self.expected:
                status = (" %02s%%" %
                          (int(self.bytes_read * 100 / self.expected)))
            sys.stderr.write('=> %s [%s]%s\n' % (path, total, status))

        if cur == total:
            sys.stderr.write("\n")
            if self.expected:
                self.bytes_read += total
            return

        toprint = int(cur * self.columns / total) - self.printed
        if toprint <= 0:
            return
        sys.stderr.write('.' * toprint)
        sys.stderr.flush()
        self.printed += toprint


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument('--keep', action='store_true', default=False,
                        help='keep items in target up to MAX items '
                             'even after they have fallen out of the source')
    parser.add_argument('--max', type=int, default=None,
                        help='store at most MAX items in the target')
    parser.add_argument('--no-item-download', action='store_true',
                        default=False,
                        help='do not download items with a "path"')
    parser.add_argument('--dry-run', action='store_true', default=False,
                        help='only report what would be done')
    parser.add_argument('--progress', action='store_true', default=False,
                        help='show progress for downloading files')
    parser.add_argument('--arches', default=None,
                        help='comma "," separated list of arches. default all')
    parser.add_argument('--releases', default=None,
                        help='comma "," separated releases. default all')

    parser.add_argument('--verbose', '-v', action='count', default=0)
    parser.add_argument('--log-file', default=sys.stderr,
                        type=argparse.FileType('w'))

    parser.add_argument('output_d')
    parser.add_argument('filters', nargs='*', default=[])

    args = parser.parse_args()

    arches = args.arches
    if arches is not None:
        if "," in args.arches:
            arches = args.arches.split(",")
        else:
            arches = [args.arches]

    releases = args.releases
    if releases is not None:
        if "," in args.releases:
            releases = args.releases.split(",")
        else:
            releases = [args.releases]

    cpath = "streams/v1/%s.json" % CONTENT_ID
    filter_list = filters.get_filters(args.filters)
    mirror_config = {'max_items': args.max, 'keep_items': args.keep,
                     'filters': filter_list,
                     'item_download': not args.no_item_download}

    level = (log.ERROR, log.INFO, log.DEBUG)[min(args.verbose, 2)]
    log.basicConfig(stream=args.log_file, level=level)

    smirror = NetbootMirrorReader(arches=arches, releases=releases)
    tstore = objectstores.FileStore(args.output_d)

    drmirror = mirrors.DryRunMirrorWriter(config=mirror_config,
                                          objectstore=tstore)
    drmirror.sync(smirror, cpath)

    def print_diff(char, items):
        for pedigree, path, size in items:
            fmt = "{char} {pedigree} {path} {size} Mb"
            size = int(size / (1024 * 1024))
            print(fmt.format(
                char=char, pedigree=' '.join(pedigree), path=path, size=size))

    print_diff('+', drmirror.downloading)
    print_diff('-', drmirror.removing)
    print("%d Mb change" % (drmirror.size / (1024 * 1024)))

    if args.dry_run:
        return True

    if args.progress:
        callback = DotProgress(expected=drmirror.size).write_progress
    else:
        callback = None

    tstore = objectstores.FileStore(args.output_d, complete_callback=callback)

    tmirror = mirrors.ObjectFilterMirror(config=mirror_config,
                                         objectstore=tstore)

    tmirror.sync(smirror, cpath)


if __name__ == '__main__':
    main()

# vi: ts=4 expandtab syntax=python
