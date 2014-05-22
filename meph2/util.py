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

import datetime
import json
import glob
import os

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


def default_policy(content, path, keyring=None, ignore_check=False):
    if path.endswith('sjson'):
        if keyring is None and not ignore_check:
            raise Exception('Must use keyring or ignore keycheck')
        return sutil.read_signed(content, keyring=keyring)
    else:
        return content


class LocalMirrorReader(mirrors.MirrorReader):
    def __init__(self, policy=None):
        """ policy should be a function which returns the extracted payload or
        raises an exception if the policy is violated. """
        if policy is not None:
            super(LocalMirrorReader, self).__init__(policy=policy)
        else:
            super(LocalMirrorReader, self).__init__()

    def source(self, path):
        return open(path)


def get_index_files(streams):
    def correct_path(index, stream):
        return os.path.join(os.path.dirname(stream), '..', '..', index)

    possible_files = []
    stream_files = []
    index_files = []

    def possible_file_check(stream, suffixes):
        for suffix in suffixes:
            possible_files.extend(
                glob.glob(os.path.join(stream, suffix + '.json'))
            )
            possible_files.extend(
                glob.glob(os.path.join(stream, suffix + '.sjson'))
            )

    for stream in streams:
        if not os.path.isdir(stream):
            raise Exception('%s not directory as expected' % stream)
        possible_files = []
        possible_file_check(stream, ['index', 'v1/index', 'streams/v1/index'])
        if not possible_files:
            raise Exception('Cannot find index for stream %s' % stream)
        else:
            stream_files.extend(possible_files)
    for stream in stream_files:
        mirror = LocalMirrorReader(policy=default_policy).load_products(stream)
        index_files.extend(
            [correct_path(v['path'], stream) for v in mirror['index'].values()]
        )
    return stream_files, index_files


def get_nonorphan_set(indexes):
    known_set = set()

    def walker(item, tree, pedigree):
        if 'path' in item:
            known_set.add(item['path'])

    for index in indexes:
        try:
            tree = json.load(open(index))
            sutil.walk_products(tree, cb_item=walker)
        except:
            print('Malformed stream data input: %s' % index)
            raise

    return known_set


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

    date = datetime.datetime.now().strftime("%Y-%m-%d")
    orphans = {orphan: date for orphan in orphans_list}
    orphans.update({k: v for k, v in known_orphans.items() if k in orphans})
    try:
        with open(filename, 'w') as orphan_file:
            json.dump(orphans, orphan_file)
    except:
        raise Exception('Cannot write orphan file %s' % filename)


# vi: ts=4 expandtab syntax=python
