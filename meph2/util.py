#   Copyright (C) 2013 Canonical Ltd.
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

from simplestreams import util as sutil
from simplestreams import mirrors

from functools import partial
import datetime
import json
import os
import re
import sys


def create_index(target_d, files=None, path_prefix="streams/v1/"):
    if files is None:
        files = [f for f in os.listdir(target_d) if f.endswith(".json")]

    ret = {'index': {}, 'format': 'index:1.0', 'updated': sutil.timestamp()}
    for f in files:
        with open(os.path.join(target_d, f), "r") as fp:
            data = sutil.load_content(fp.read())
        fmt = data.get('format')
        cid = data.get('content_id')
        if fmt == "index:1.0" or not (fmt and cid):
            continue
        optcopy = ('datatype', 'updated', 'format')
        item = {k: data.get(k) for k in optcopy if data.get(k)}
        if data.get('format') == "products:1.0":
            item['products'] = sorted([p for p in data['products'].keys()])

        item['path'] = path_prefix + f

        ret['index'][cid] = item

    return ret


def sign_streams_d(path, status_cb=None):
    for root, _dirs, files in os.walk(path):
        for f in [f for f in files if f.endswith(".json")]:
            signjson_file(os.path.join(root, f), status_cb=status_cb)


def signjson_file(fname, status_cb=None):
    # input fname should be .json
    # creates .json.gpg and .sjson
    content = ""
    with open(fname, "r") as fp:
        content = fp.read()
    (changed, scontent) = sutil.make_signed_content_paths(content)

    if status_cb:
        status_cb(fname)

    sutil.sign_file(fname, inline=False)
    if changed:
        sutil.sign_content(scontent, sutil.signed_fname(fname, inline=True),
                           inline=True)
    else:
        sutil.sign_file(fname, inline=True)

    return


class PathListerMirrorWriter(mirrors.BasicMirrorWriter):
    paths = set()

    def load_products(self, path=None, content_id=None):
        return {}

    def insert_item(self, data, src, target, pedigree, contentsource):
        data = sutil.products_exdata(src, pedigree)
        if 'path' in data:
            self.paths.add(data['path'])


def endswith_policy(initial_path, keyring, content, path):
    if initial_path.endswith('sjson'):
        return sutil.read_signed(content, keyring=keyring)
    else:
        return content


def get_nonorphan_set(streams, data_d, keyring=None):
    non_orphaned = set()
    for stream in streams:
        (mirror_url, initial_path) = sutil.path_from_mirror_url(stream, None)

        smirror = mirrors.UrlMirrorReader(
            mirror_url, mirrors=[data_d],
            policy=partial(endswith_policy, initial_path, keyring))
        lmirror = PathListerMirrorWriter()
        lmirror.sync(smirror, initial_path)

        non_orphaned.update(lmirror.paths)

    return non_orphaned


def read_timedelta(string):
    timedelta = datetime.timedelta()
    for time_portion in re.findall(r'([0-9]+[dhms])', string):
        num = int(time_portion[:-1])
        specifier = time_portion[-1]
        if specifier == 'd':
            timedelta += datetime.timedelta(days=num)
        elif specifier == 'h':
            timedelta += datetime.timedelta(hours=num)
        elif specifier == 'm':
            timedelta += datetime.timedelta(minutes=num)
        elif specifier == 's':
            timedelta += datetime.timedelta(seconds=num)
        else:
            raise ValueError(
                'Unexpected specifier for timedelta, given %s' % time_portion)

    return timedelta


def read_timestamp(ts, fmt="%a, %d %b %Y %H:%M:%S %z"):
    return datetime.datetime.strptime(ts, fmt)


def read_orphan_file(filename):
    if not os.path.exists(filename):
        raise Exception(
            '%s orphan file does not exist' % filename
        )
    try:
        with open(filename) as orphan_file:
            known_orphans = json.load(orphan_file)
            return known_orphans
    except:
        raise Exception(
            '%s exists but is not a valid orphan file' % filename
        )


def write_orphan_file(filename, orphans_list):
    known_orphans = {}
    if os.path.exists(filename):
        known_orphans = read_orphan_file(filename)

    date = sutil.timestamp()
    orphans = {orphan: date for orphan in orphans_list}
    orphans.update({k: v for k, v in known_orphans.items() if k in orphans})
    try:
        if filename == "-":
            json.dump(orphans, sys.stdout, indent=1)
            sys.stdout.write("\n")
        else:
            with open(filename, 'w') as orphan_file:
                json.dump(orphans, orphan_file, indent=1)
                orphan_file.write("\n")
    except:
        raise Exception('Cannot write orphan file %s' % filename)


# vi: ts=4 expandtab syntax=python
