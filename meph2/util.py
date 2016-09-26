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
import errno
import hashlib
import json
import os
import re
import sys
import tempfile

# for callers convenience
timestamp = sutil.timestamp

STREAMS_D = "streams/v1/"


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
    tokenizer = r'([0-9]+[dhms])'
    parts = re.findall(tokenizer, string)
    if len(parts) == 0 and string != "":
        # if no unit given, then default to 'd'
        parts = re.findall(tokenizer, string + "d")
    for time_portion in parts:
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


def empty_iid_products(content_id):
    return {'content_id': content_id, 'products': {},
            'datatype': 'image-ids', 'format': 'products:1.0'}


def get_file_info(path, sums=None):
    # return dictionary with size and checksums of existing file
    buflen = 1024*1024

    if sums is None:
        sums = ['sha256']
    sumers = {k: hashlib.new(k) for k in sums}

    ret = {'size': os.path.getsize(path)}
    with open(path, "rb") as fp:
        while True:
            buf = fp.read(buflen)
            for sumer in sumers.values():
                sumer.update(buf)
            if len(buf) != buflen:
                break

    ret.update({k: sumers[k].hexdigest() for k in sumers})
    return ret


def copy_fh(src, path, buflen=1024*8, cksums=None, makedirs=True):
    summer = sutil.checksummer(cksums)
    out_d = os.path.dirname(path)
    if makedirs:
        try:
            os.makedirs(out_d)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
    tf = tempfile.NamedTemporaryFile(dir=out_d, delete=False)
    try:
        while True:
            buf = src.read(buflen)
            summer.update(buf)
            tf.write(buf)
            if len(buf) != buflen:
                break
    finally:
        if summer.check():
            try:
                os.rename(tf.name, path)
            except:
                os.unlink(tf.name)
                raise
        else:
            found = summer.hexdigest()
            try:
                size = os.path.getsize(tf.name)
            except:
                size = "unavailable"
            os.unlink(tf.name)

            msg = ("Invalid checksum for '%s'. size=%s. "
                   "found '%s', expected '%s'" %
                   (path, size, found, str(cksums)))
            raise ValueError(msg)


def dump_data(data, end_cr=True):
    # dump a jsonable data as a string
    bytestr = json.dumps(data, indent=1, sort_keys=True,
                         separators=(',', ': ')).encode('utf-8')
    if end_cr:
        bytestr += b'\n'

    return bytestr


def load_products(path, product_streams):
    products = {}
    for product_stream in product_streams:
        product_stream_path = os.path.join(path, product_stream)
        if os.path.exists(product_stream_path):
            with sutil.contentsource.UrlContentSource(
                    product_stream_path) as tcs:
                product_listing = sutil.load_content(tcs.read())
                products.update(product_listing['products'])
    return products


def load_product_streams(src):
    index_path = os.path.join(src, STREAMS_D, "index.json")
    if not os.path.exists(index_path):
        return []
    with sutil.contentsource.UrlContentSource(index_path) as tcs:
        index = sutil.load_content(tcs.read())
    return [product['path'] for product in index['index'].values()]


def gen_index_and_sign(data_d, sign=True):
    md_d = os.path.join(data_d, "streams", "v1")
    if not os.path.exists(md_d):
        os.makedirs(md_d)
    index = create_index(md_d, files=None)
    with open(os.path.join(md_d, "index.json"), "wb") as fp:
        fp.write(dump_data(index))

    if sign:
        sign_streams_d(md_d)


# vi: ts=4 expandtab syntax=python
