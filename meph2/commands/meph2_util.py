#!/usr/bin/python3

import argparse
import copy
import os
import re
from functools import partial
import shutil
import sys
import yaml

try:
    from urllib import request as urllib_request
except ImportError:
    # python2
    import urllib2 as urllib_request

from meph2 import util
from meph2.commands.flags import COMMON_ARGS, SUBCOMMANDS

from simplestreams import (
    filters,
    mirrors,
    util as sutil,
    objectstores)


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

    def remove_item(self, data, src, target, pedigree):
        return

    def remove_version(self, data, src, target, pedigree):
        # sync doesnt filter on things to be removed, so
        # we have to do that here.
        if not filters.filter_item(self.filters, data, src, pedigree):
            return

        self.removed_versions.append(pedigree)

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
            sticky=[
                'di_version', 'kpackage', 'sha256', 'md5', 'path', 'ftype',
                'src_package', 'src_version', 'src_release'])

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
    # it converts a candidate item to a release item and inserts it.

    # we take care of writing file in insert_products
    insert_index_entry = BareMirrorWriter._noop

    def __init__(self, config, objectstore, label):
        super(ReleasePromoteMirror, self).__init__(config=config,
                                                   objectstore=objectstore)
        self.label = label

    def rel2candidate(self, ptree):
        ret = copy.deepcopy(ptree)
        ret['content_id'] = self.fixed_content_id(ret['content_id'])

        for oname in [o for o in ptree.get('products', {})]:
            newname = self.fixed_product_id(oname)
            ptree['products'][newname] = ptree['products'][oname]
            del ptree['products'][oname]

    def fixed_content_id(self, content_id):
        # when promoting from candidate, our content ids get ':candidate'
        # removed
        #  com.ubuntu.maas:candidate:v2:download => com.ubuntu.maas:v2:download
        return(content_id.replace(":candidate", ""))

    def fixed_pedigree(self, pedigree):
        return tuple([self.fixed_product_id(pedigree[0])] + list(pedigree[1:]))

    def fixed_product_id(self, product_id):
        # when promoting from candidate, product ids get '.candidate' removed
        #  com.ubuntu.maas.candidate:v2:boot:13.10:armhf:generic-lpae ->
        #     com.ubuntu.maas:v2:boot:13.10:armhf:generic-lpae
        return product_id.replace(".candidate:", ":")

    def load_products(self, path, content_id):
        # this loads the released products, but returns it in form
        # of candidate products
        ret = super(ReleasePromoteMirror, self).load_products(
            path=path, content_id=self.fixed_content_id(content_id))
        return self.rel2candidate(ret)

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


def main_insert(args):
    (src_url, src_path) = sutil.path_from_mirror_url(args.src, None)
    filter_list = filters.get_filters(args.filters)
    mirror_config = {'max_items': 20, 'keep_items': True,
                     'filters': filter_list}
    policy = partial(util.endswith_policy, src_path, args.keyring)
    smirror = mirrors.UrlMirrorReader(src_url, policy=policy)
    tstore = objectstores.FileStore(args.target)

    if args.dry_run:
        drmirror = DryRunMirrorWriter(config=mirror_config, objectstore=tstore)
        drmirror.sync(smirror, src_path)
        for (pedigree, path, size) in drmirror.downloading:
            fmt = "{pedigree} {path}"
            sys.stderr.write(
                fmt.format(pedigree='/'.join(pedigree), path=path) + "\n")
        return 0

    tmirror = InsertBareMirrorWriter(config=mirror_config, objectstore=tstore)
    tmirror.sync(smirror, src_path)

    util.gen_index_and_sign(args.target, sign=not args.no_sign)
    return 0


def main_merge(args):
    src_product_streams = util.load_product_streams(args.src)
    target_product_streams = util.load_product_streams(args.target)
    src_products = util.load_products(args.src, src_product_streams)
    target_products = util.load_products(args.target, target_product_streams)

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

    util.gen_index_and_sign(args.target, not args.no_sign)


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

    util.gen_index_and_sign(args.target, not args.no_sign)
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

    util.gen_index_and_sign(mirror_url, not args.no_sign)
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
        if not args.now and not util.read_timestamp(when) + delta < now:
            continue
        if args.dry_run:
            sys.stderr.write('Reaping %s orphaned on %s\n' % (orphan, when))
        else:
            sutil.rm_f_file(location)
            reaped.add(orphan)
            try:
                os.removedirs(os.path.dirname(location))
            except OSError:
                pass

    if not args.dry_run:
        util.write_orphan_file(args.orphan_data, known_orphans.keys() - reaped)
    return 0


def main_sign(args):
    util.gen_index_and_sign(args.data_d)
    return 0


def main_remove_version(args):
    filter_list = filters.get_filters(args.filters)
    product_streams = util.load_product_streams(args.data_d)
    resign = False

    for product_stream in product_streams:
        product_stream_path = os.path.join(args.data_d, product_stream)
        content = util.load_content(product_stream_path)
        products = content['products']
        write_stream = False
        for product, data in products.items():
            if (
                    filters.filter_dict(filter_list, data) and
                    args.version in data['versions']):
                print('Removing %s from %s' % (args.version, product))
                if not args.dry_run:
                    del data['versions'][args.version]
                    resign = write_stream = True
        if write_stream:
            with open(product_stream_path, 'wb') as f:
                f.write(util.dump_data(content).strip())
    if resign:
        util.gen_index_and_sign(args.data_d, not args.no_sign)
    return 0


def main_copy_version(args):
    filter_list = filters.get_filters(args.filters)
    product_streams = util.load_product_streams(args.data_d)
    resign = False

    for product_stream in product_streams:
        product_stream_path = os.path.join(args.data_d, product_stream)
        content = util.load_content(product_stream_path)
        products = content['products']
        write_stream = False
        for product, data in products.items():
            if (
                    filters.filter_dict(filter_list, data) and
                    args.from_version in data['versions']):
                print('Copying %s to %s in %s' % (
                    args.from_version, args.to_version, product))
                if not args.dry_run:
                    new_version = copy.deepcopy(
                        data['versions'][args.from_version])
                    for item in new_version['items'].values():
                        old_path = os.path.join(args.data_d, item['path'])
                        item['path'] = item['path'].replace(
                            args.from_version, args.to_version)
                        new_path = os.path.join(args.data_d, item['path'])
                        if not os.path.exists(new_path):
                            os.makedirs(
                                os.path.dirname(new_path), exist_ok=True)
                            shutil.copy(
                                old_path, new_path, follow_symlinks=False)
                    data['versions'][args.to_version] = new_version
                    resign = write_stream = True
        if write_stream:
            with open(product_stream_path, 'wb') as f:
                f.write(util.dump_data(content).strip())
    if resign:
        util.gen_index_and_sign(args.data_d, not args.no_sign)
    return 0


def main_import(args):
    """meph2-util import wraps the preferred command 'meph2-import'.

    'meph2-util import' is left for backwards compatibility, but relies
    on modules not in the standard library in python3.2 (specifically lzma).
    meph2-util needs to run with only dependencies available in the
    Ubuntu 12.04 (precise) distro."""

    sys.stderr.write(
       "=== WARNING: DEPRECATED ===\n" + main_import.__doc__ + "\n")

    from meph2.commands import mimport
    return(mimport.main_import(args))


def get_stream_label(product_streams):
    """Returns the label for the stream.

    This assumes the stream uses a consistent label and is identified in
    the stream's filename using for format FQDN:label:[version]?:name"""
    stream_label = None
    for product_stream in product_streams:
        stream = os.path.basename(product_stream).split(':')
        label = stream[1]
        if stream_label:
            assert label == stream_label, "All labels must be identical!"
        else:
            stream_label = label
    return stream_label


def get_stream_name_without_label(product_stream):
    stream_name = os.path.basename(product_stream).split(':')
    del stream_name[1]
    return ':'.join(stream_name)


def get_product_name_without_label(product_name, label):
    m = re.search(
        r"^(?P<fqdn>.*)[\.:]%s(?P<product>:.*)$" % label, product_name)
    assert m, "Unable to find label %s in product %s!" % (label, product_name)
    return ''.join(m.groups())


def get_diff(source, target, promote=False, new_versions_only=False, latest_only=False):

    src_product_streams = util.load_product_streams(source, True)
    src_label = get_stream_label(src_product_streams)
    target_product_streams = util.load_product_streams(target, True)
    target_label = get_stream_label(target_product_streams)
    diff = {}

    # Iterate over both streams to make sure we capture anything
    # missing.
    for product_stream in src_product_streams + target_product_streams:
        diff_stream_name = get_stream_name_without_label(product_stream)
        if src_label in product_stream:
            src_product_stream = product_stream
            target_product_stream = product_stream.replace(
                src_label, target_label)
        else:
            src_product_stream = product_stream.replace(
                target_label, src_label)
            target_product_stream = product_stream

        src_product_stream_path = os.path.join(source, src_product_stream)
        target_product_stream_path = os.path.join(
            target, target_product_stream)

        src_stream_missing = False
        target_stream_missing = False
        if src_label in product_stream:
            try:
                content = util.load_content(src_product_stream_path, True)
            except OSError:
                src_stream_missing = True
            try:
                other_content = util.load_content(
                    target_product_stream_path, True)
            except OSError:
                target_stream_missing = True
        else:
            try:
                content = util.load_content(target_product_stream_path, True)
            except OSError:
                target_stream_missing = True
            try:
                other_content = util.load_content(
                    src_product_stream_path, True)
            except OSError:
                src_stream_missing = True

        # Verify the product stream exists in both streams.
        if src_stream_missing or target_stream_missing:
            if diff_stream_name not in diff:
                diff[diff_stream_name] = {
                    'not_merged': (
                        src_label if src_stream_missing else target_label)
                }
            else:
                diff[diff_stream_name]['not_merged'] = (
                    src_label if src_stream_missing else target_label)
            continue

        for product, data in content['products'].items():
            if src_label in product:
                label = src_label
                other_label = target_label
            else:
                label = target_label
                other_label = src_label
            other_product = product.replace(label, other_label)
            diff_product_name = get_product_name_without_label(product, label)
            # Verify the product is in both streams.
            if other_product not in other_content['products']:
                if diff_stream_name not in diff:
                    diff[diff_stream_name] = {}
                if diff_product_name not in diff[diff_stream_name]:
                    diff[diff_stream_name][diff_product_name] = {}
                if promote:
                    diff[diff_stream_name][diff_product_name]['labels'] = [
                        label,
                        other_label,
                    ]
                else:
                    diff[diff_stream_name][diff_product_name]['labels'] = [
                        label,
                    ]
                continue
            else:
                other_data = other_content['products'][other_product]
            for key, value in data.items():
                if key == 'versions':
                    if new_versions_only and target_label in product:
                        continue
                    for version, version_data in value.items():
                        other_versions = other_data.get('versions', {})
                        if version in other_versions:
                            assert version_data == other_data[
                                'versions'][version], (
                                    "%s %s exists in both streams but data "
                                    " does not match!" % (product, version))
                        else:
                            if latest_only:
                                newer_found = False
                                for other_version in other_versions:
                                    if other_version > version:
                                        newer_found = True
                                        break
                                if newer_found:
                                    continue
                            if diff_stream_name not in diff:
                                diff[diff_stream_name] = {}
                            if diff_product_name not in diff[diff_stream_name]:
                                diff[diff_stream_name][diff_product_name] = {}
                            if (
                                    'versions' not in diff[diff_stream_name][
                                        diff_product_name]
                                    or latest_only
                                    ):
                                diff[diff_stream_name][diff_product_name][
                                    'versions'] = {}
                            if version not in diff[diff_stream_name][
                                    diff_product_name]['versions']:
                                diff[diff_stream_name][diff_product_name][
                                    'versions'][version] = {}
                            if promote:
                                diff[diff_stream_name][diff_product_name][
                                    'versions'][version]['labels'] = [
                                        label,
                                        other_label,
                                    ]
                            else:
                                diff[diff_stream_name][diff_product_name][
                                    'versions'][version]['labels'] = [label]
                elif key == 'label':
                    # Label is expected to be different
                    continue
                elif value != other_data.get(key):
                    if diff_stream_name not in diff:
                        diff[diff_stream_name] = {}
                    if diff_product_name not in diff[diff_stream_name]:
                        diff[diff_stream_name][diff_product_name] = {}
                    # Keep dictionary order consistent
                    if label == src_label:
                        diff[diff_stream_name][diff_product_name][key] = {
                            src_label: value,
                            target_label: other_data.get(key),
                        }
                    else:
                        diff[diff_stream_name][diff_product_name][key] = {
                            src_label: other_data.get(key),
                            target_label: value,
                        }
    return diff


def main_diff(args):

    diff = get_diff(args.src, args.target, args.promote, args.new_versions_only, args.latest_only)

    def output(buff):
        buff.write("# Generated by %s-%s\n" % (
            os.path.basename(sys.argv[0]), util.get_version()))
        buff.write("# Generated on %s\n" % sutil.timestamp())
        buff.write("# Source: %s\n" % args.src)
        buff.write("# Target: %s\n" % args.target)
        buff.write("# new-versions-only: %s\n" % args.new_versions_only)
        buff.write("# latest-only: %s\n" % args.latest_only)
        buff.write("# promote: %s\n" % args.promote)
        buff.write("\n")
        yaml.safe_dump(diff, buff)

    if args.output:
        if os.path.exists(args.output):
            os.remove(args.output)
        with open(args.output, 'w') as f:
            output(f)
    else:
        output(sys.stdout)
    return 0


def find_stream(diff_product_stream, product_streams):
    found = False
    for product_stream in product_streams:
        if diff_product_stream == get_stream_name_without_label(
                product_stream):
            found = True
            break
    # New product streams should be merged in.
    assert found, "Target stream %s not found!" % diff_product_stream
    return product_stream


def copy_items(version_data, src_path, target_path):
    for item in version_data['items'].values():
        src_item_path = os.path.join(src_path, item['path'])
        target_item_path = os.path.join(target_path, item['path'])
        if os.path.exists(target_item_path):
            # Items in a product may be referenced multiple times.
            # e.g all kernel versions of the same arch use the same SquashFS.
            continue
        os.makedirs(os.path.dirname(target_item_path), exist_ok=True)
        if os.path.exists(src_item_path):
            print("INFO: Copying %s to %s" % (src_item_path, target_item_path))
            # Attempt to use a hard link when both streams are on the same
            # filesystem to save space. Will fallback to a copy.
            try:
                os.link(src_item_path, target_item_path)
            except OSError:
                shutil.copy2(src_item_path, target_item_path)
        else:
            print("INFO: Downloading %s to %s" % (
                src_item_path, target_item_path))
            urllib_request.urlretrieve(src_item_path, target_item_path)
        assert util.get_file_info(target_item_path)['sha256'] == item[
            'sha256'], ("Target file %s hash %s does not match!" % (
                target_item_path, item['sha256']))


def patch_versions(
        value, args, target_label, target_product, target_data, target_path,
        src_content, src_product_stream_path, src_label, src_path):
    write_product_stream = False
    for version, version_data in value.items():
        if version in target_data['versions']:
            if target_label in version_data.get('labels', []):
                # If the version already exists in the target stream skip
                # adding it. This allows CPC to run a nightly cron job.
                print("INFO: Skipping, version %s already exists!" % version)
            else:
                print("INFO: Deleting version %s" % version)
                del target_data['versions'][version]
                write_product_stream = True
        elif target_label in version_data.get('labels', []):
            print("INFO: Adding version %s to %s" % (version, target_product))
            assert src_product_stream_path, (
                "A source must be given when adding a version to a product!")
            write_product_stream = True
            if not src_content:
                src_content = util.load_content(src_product_stream_path, True)
            src_product = target_product.replace(target_label, src_label)
            src_data = src_content['products'][src_product]
            target_data['versions'][version] = src_data['versions'][version]
            if not args.dry_run:
                copy_items(
                    target_data['versions'][version], src_path, target_path)
    return write_product_stream


def main_patch(args):
    streams = args.streams[0]
    regenerate_index = False
    if len(streams) == 1:
        target_path = streams[0]
        target_product_streams = util.load_product_streams(streams[0])
        target_label = get_stream_label(target_product_streams)
        src_path = None
        src_product_streams = []
        src_label = None
    elif len(streams) == 2:
        target_path = streams[1]
        target_product_streams = util.load_product_streams(streams[1])
        target_label = get_stream_label(target_product_streams)
        src_path = streams[0]
        src_product_streams = util.load_product_streams(streams[0], True)
        src_label = get_stream_label(src_product_streams)
    else:
        raise AssertionError("A max of 2 streams can be given!")

    if args.input:
        with open(args.input, 'r') as f:
            diff = yaml.safe_load(f)
    else:
        diff = yaml.safe_load(sys.stdin)

    if diff is None:
        print("WARNING: No diff defined!")
        return 0

    for product_stream, stream_data in diff.items():
        write_product_stream = False
        target_stream = find_stream(product_stream, target_product_streams)
        target_product_stream_path = os.path.join(target_path, target_stream)
        if src_product_streams:
            src_stream = find_stream(product_stream, src_product_streams)
            src_product_stream_path = os.path.join(src_path, src_stream)
        else:
            src_product_stream_path = None

        for product, product_data in stream_data.items():
            found_product = False
            product_regex = re.compile(r"^%s$" % product)
            target_content = util.load_content(target_product_stream_path)
            # Only load source content when promoting a version. This allows
            # users to create a patch to modify the values or remove versions
            # without needing a source.
            src_content = None
            for target_product, target_data in list(
                    target_content['products'].items()):
                if product_regex.search(get_product_name_without_label(
                        target_product, target_label)):
                    found_product = True
                    print(
                        "INFO: Found matching product in target for %s, %s" % (
                            product, target_product))
                    for key, value in product_data.items():
                        if key == 'labels':
                            if target_label not in value:
                                print(
                                    "INFO: Deleting product %s" %
                                    target_product)
                                del target_content['products'][target_product]
                                regenerate_index = write_product_stream = True
                                break
                        elif key == 'versions':
                            ret = patch_versions(
                                value, args, target_label, target_product,
                                target_data, target_path, src_content,
                                src_product_stream_path, src_label,
                                src_path)
                            regenerate_index |= ret
                            write_product_stream |= ret
                        elif (
                                target_label in value and
                                target_data.get(key) != value[target_label]):
                            print(
                                "INFO: Updating key %s %s -> %s" % (
                                    key, target_data.get(key),
                                    value[target_label]))
                            regenerate_index = write_product_stream = True
                            target_data[key] = value[target_label]
            if not found_product:
                assert src_product_stream_path, (
                    "A source must be given when adding a new product!")
                src_content = util.load_content(
                    src_product_stream_path, True)
                for src_product, src_data in src_content['products'].items():
                    if (
                            product_regex.search(
                                get_product_name_without_label(
                                    src_product, src_label))
                            and target_label in product_data.get('labels', [])
                            ):
                        write_product_stream = found_product = True
                        print("INFO: Promoting %s into %s" % (
                            product, target_stream))
                        new_product = src_product.replace(
                            src_label, target_label)
                        target_content['products'][new_product] = src_data
                        target_content['products'][new_product][
                            'label'] = target_label
                        if not args.dry_run:
                            for version_data in src_data['versions'].values():
                                copy_items(version_data, src_path, target_path)
            if write_product_stream and not args.dry_run:
                print("INFO: Writing %s" % target_product_stream_path)
                os.remove(target_product_stream_path)
                with open(target_product_stream_path, 'wb') as f:
                    f.write(util.dump_data(target_content).strip())
            else:
                # Validate the modified stream is still valid during
                # a dry run.
                util.dump_data(target_content).strip()
    if regenerate_index and not args.dry_run:
        util.gen_index_and_sign(target_path, sign=not args.no_sign)
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
