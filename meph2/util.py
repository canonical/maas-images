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


# vi: ts=4 expandtab syntax=python
