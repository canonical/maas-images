#!/usr/bin/python3

import argparse
import copy
import os
from functools import partial
import shutil
import sys

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
    # it converts a daily item to a release item and inserts it.

    # we take care of writing file in insert_products
    insert_index_entry = BareMirrorWriter._noop

    def __init__(self, config, objectstore, label):
        super(ReleasePromoteMirror, self).__init__(config=config,
                                                   objectstore=objectstore)
        self.label = label

    def rel2daily(self, ptree):
        ret = copy.deepcopy(ptree)
        ret['content_id'] = self.fixed_content_id(ret['content_id'])

        for oname in [o for o in ptree.get('products', {})]:
            newname = self.fixed_product_id(oname)
            ptree['products'][newname] = ptree['products'][oname]
            del ptree['products'][oname]

    def fixed_content_id(self, content_id):
        # when promoting from daily, our content ids get ':daily' removed
        #  com.ubuntu.maas:daily:v2:download => com.ubuntu.maas:v2:download
        return(content_id.replace(":daily", ""))

    def fixed_pedigree(self, pedigree):
        return tuple([self.fixed_product_id(pedigree[0])] + list(pedigree[1:]))

    def fixed_product_id(self, product_id):
        # when promoting from daily, product ids get '.daily' removed
        #  com.ubuntu.maas.daily:v2:boot:13.10:armhf:generic-lpae ->
        #     com.ubuntu.maas:v2:boot:13.10:armhf:generic-lpae
        return product_id.replace(".daily:", ":")

    def load_products(self, path, content_id):
        # this loads the released products, but returns it in form
        # of daily products
        ret = super(ReleasePromoteMirror, self).load_products(
            path=path, content_id=self.fixed_content_id(content_id))
        return self.rel2daily(ret)

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
        if not util.read_timestamp(when) + delta < now:
            continue
        if args.dry_run:
            sys.stderr.write('Reaping %s orphaned on %s\n' % (orphan, when))
        else:
            sutil.rm_f_file(location)
            reaped.add(orphan)
            try:
                os.removedirs(os.path.dirname(location))
            except:
                pass

    if not args.dry_run:
        util.write_orphan_file(args.orphan_data, known_orphans.keys() - reaped)
    return 0


def main_sign(args):
    util.gen_index_and_sign(args.data_d)
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
