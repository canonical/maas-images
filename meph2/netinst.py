#!/usr/bin/python3


import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import queue

import simplestreams
from simplestreams import mirrors
from simplestreams import objectstores
from simplestreams import log
from simplestreams import util as sutil
from simplestreams.log import LOG

from .url_helper import geturl, geturl_len, geturl_text, UrlError
from .util import dump_data
from .ubuntu_info import REL2VER, SUPPORTED


APACHE_PARSE_RE = re.compile(r'''
    href="
    (?P<name>[^"]*)".*      # filename
    (?P<date>               # date, varies w/ apache ver
     (..-...-....\ ..:..)   # 01-Jun-2015 (apache 2.2)
      |
     (....-..-..\ ..:..)    # 2015-06-01 (apache 2.4)
    )
    .*?
    (?P<size>\d+[^\s<]*|-)  # size, or '-' for dirs
    ''', re.X)

NUM_THREADS = 10
PRIMARY_MIRROR = "http://archive.ubuntu.com/ubuntu/dists"
PORTS_MIRROR = "http://ports.ubuntu.com/ubuntu-ports/dists"
HTTP_MIRRORS = {
    "i386": PRIMARY_MIRROR,
    "amd64": PRIMARY_MIRROR,
    'default': PORTS_MIRROR,
}

# add 'proposed': '-proposed' to get proposed pocket also
POCKETS = {
    "release": "",
    "updates": "-updates",
}
POCKETS_PROPOSED = POCKETS.copy()
POCKETS_PROPOSED.update({'proposed': '-proposed'})

ARCHES = ("i386", "amd64", "ppc64el", "armhf", "arm64", "s390x")
YYYYMMDD_RE = re.compile("20[0-9][0-9](0[0-9]|1[012])[0-3][0-9]ubuntu.*")
FILES_PREFIX = "files/"

# this is a blacklist of things that look like kernel flavors
# in their path, but are not.
INVALID_KERNEL_FLAVORS = ("xen", "gtk")

GPG_KEYRING = "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
CONTENT_ID = "com.ubuntu.installer:released:netboot"

FLAVOR_COLLISIONS = {
    "omap4": "om4",
    "generic-lpae": "glp",
}

KERNEL_FLAVORS = (
    'armadaxp',
    'generic',
    'generic-lpae',
    'highbank',
    'keystone',
    'non-pae',
    'omap',
    'omap4',
)

DTB_TO_FORMAT = {
    "apm-mustang.dtb": "xgene"
}

IMAGE_FORMATS = (
    'beagleboard',
    'default',
    'pandaboard',
    'tegra',
    'wandboard',
    'wandboard-quad',
    'xgene',
)

FTYPE_MATCHES = {
    "initrd": re.compile(r"(initrd.gz|initrd.ubuntu|uInitrd)$").search,
    "kernel": re.compile(r"(kernel.ubuntu|linux|uImage|"
                         "vmlinux|vmlinuz)$").search,
    "dtb": re.compile(r".dtb$").search,
}

# #
# # Under a path like: MIRROR/precise-updates/main/installer-i386/
# #  we find a listing of directories like:
# #      20101020ubuntu229
# #      20101020ubuntu230
# #      20101020ubuntu231
# #      current
# #  and under that:
# #     images/netboot/ubuntu-installer/<ARCH>/{linux,initrd.gz}
# #     images/SHA256SUMS{,.gpg}


class NetbootMirrorReader(mirrors.MirrorReader):
    fpath = FILES_PREFIX
    content_id = CONTENT_ID
    _products = {}
    _pathmap = {}

    def __init__(self, releases=None, arches=None, pockets=None):
        if releases is None:
            releases = SUPPORTED.keys()

        if arches is None:
            arches = ARCHES

        self.releases = releases
        self.arches = arches
        self.pockets = pockets

        def policy(content, path):
            return content

        super(NetbootMirrorReader, self).__init__(policy=policy)

        self._get_products(path=None)

    def source(self, path):
        if path == "streams/v1/index.json":
            return self._get_index()
        elif path == "streams/v1/%s.json" % self.content_id:
            return simplestreams.contentsource.MemoryContentSource(
                url=None, content=dump_data(self._get_products(path)))
        elif path in self._pathmap:
            LOG.debug("request for %s %s" % (path, self._pathmap[path]))
            cs = simplestreams.contentsource.UrlContentSource
            return cs(self._pathmap[path])
        raise Exception("Bad path: %s" % path)

    def _get_products(self, path=None):
        if self._products:
            return self._products

        (rdata, pathmap) = get_products_data(content_id=self.content_id,
                                             releases=self.releases,
                                             arches=self.arches,
                                             pockets=self.pockets)

        self._pathmap = pathmap
        self._products = rdata

    def _get_index(self):
        return({'index': {
            self.content_id: {
            }
        }})

    def _get_file(self, path):
        pass


def download(url, target):
    with open(target, "wb") as fp:
        fp.write(geturl(url))


def gpg_check(filepath, gpgpath, keyring=GPG_KEYRING):
    cmd = ['gpgv', '--keyring=%s' % keyring, gpgpath, filepath]

    subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    return


def get_file_sums_list(url, keyring=GPG_KEYRING, mfilter=None):
    # given url that has SHA256SUMS and MD5SUMS files at
    # url/SHA256SUMS and url/MD5SUMS respectively get those
    # files and return a dict with:
    #   {path: {'md5': md5sum, 'sha256': sha256sum},...}
    tmpd = tempfile.mkdtemp()
    suminfo = (("sha256", "SHA256SUMS", "SHA256SUMS.gpg", True),
               ("md5", "MD5SUMS", "MD5SUMS.gpg", False))

    if url[-1] != "/":
        url = url + "/"

    files = {}
    try:
        for (kname, fname, gpgfname, check) in suminfo:
            l_fname = os.path.join(tmpd, fname)
            l_gpgfname = os.path.join(tmpd, gpgfname)

            LOG.debug("downloading %s" % url + fname)
            download(url + fname, l_fname)

            if check and keyring:
                LOG.debug("downloading gpg %s" % url + gpgfname)
                download(url + gpgfname, l_gpgfname)
                try:
                    gpg_check(l_fname, l_gpgfname, keyring=keyring)
                except subprocess.CalledProcessError as e:
                    LOG.warn("Failed gpg check of %s against %s. "
                             "keyring=%s, output: %s" %
                             (url + fname, url + gpgfname, keyring, e.output))
                    raise

            with open(l_fname, "r") as fp:
                sumdata = fp.read()

            for line in sumdata.splitlines():
                (cksum, curpath) = line.split()
                if curpath.startswith("./"):
                    curpath = curpath[2:]

                if mfilter is None or mfilter(curpath):
                    if curpath not in files:
                        files[curpath] = {}
                    files[curpath][kname] = cksum

    finally:
        shutil.rmtree(tmpd)

    return files


def list_apache_dirs(url):
    # this is modified from
    # http://stackoverflow.com/questions/686147/url-tree-walker-in-python
    # the change is just to make it return only dirs, and not recurse.
    try:
        html = geturl_text(url)
    except UrlError as e:
        if e.code == 404:
            print('skipping 404: %s: %s' % (url, e))
            return []
        else:
            raise e
    if not url.endswith('/'):
        url += '/'
    dirs = []
    for m in APACHE_PARSE_RE.finditer(html):
        name = m.group('name')
        date = m.group('date')
        size = m.group('size')
        if size.strip() == '-':
            size = 'dir'
        try:
            # Apache 2.2 style dates
            dateobj = time.strptime(date, "%d-%b-%Y %H:%M")
        except ValueError:
            # Apache 2.4 style dates
            dateobj = time.strptime(date, "%Y-%m-%d %H:%M")

        pubdate = simplestreams.util.timestamp(time.mktime(dateobj))
        if name.endswith('/'):
            dirs += [(name[:-1], pubdate)]

    # return name
    return dirs


def get_file_item_data(path, release="base"):
    # input like file names at
    # http://archive.ubuntu.com/ubuntu/dists/precise/main/installer-i386
    #    /current/images/SHA256SUMS
    # http://ports.ubuntu.com/ubuntu-ports/dists/trusty/main
    #    /installer-armhf/current/images/MD5SUMS
    #    /installer-ppc64el/current/images/MD5SUMS
    # return either None (not a kernel/initrd/dtb)
    # or a dictionary of:
    #   release-kernel, kernel-flavor, initrd-flavor, filetype, image_format
    #   release-kernel is 'wily' if trusty-hwe-w
    #   filetype is 'dtb', 'initrd.' or 'kernel'
    # at the moment the only 'initrd-flavor' that we're supporting is netboot
    ftype = None
    image_format = None
    initrd_flavor = "netboot"
    kernel_flavor = None
    kernel_release = None
    # image-format
    #  "beagleboard", "default", "pandaboard",
    #  "tegra", "wandboard", "wandboard-quad", "xgene",

    # kernel-flavor
    # "armadaxp", "generic", "generic-lpae", "highbank",
    # "keystone", "non-pae", "omap", "omap4",

    ptoks = path.split("/")

    # paths with 'xen' or 'cdrom' would be other initrd-flavors
    # essentially just blacklist paths with these toks
    other_iflavor_toks = ('xen', 'cdrom', 'gtk')
    for other in other_iflavor_toks:
        if other + "/" in path:
            return None

    # file type
    for (cftype, match) in FTYPE_MATCHES.items():
        if match(path):
            ftype = cftype
            break
    if not ftype:
        return None

    # kernel release.  all kernel release paths start with <release>-
    releases = REL2VER.keys()
    kernel_release = release
    for rel in REL2VER.keys():
        if path.startswith(rel + "-"):
            kernel_release = rel
            break

    # image format
    bname = ptoks[-1]
    image_format = 'default'
    if bname in DTB_TO_FORMAT:
        # specific/known path based on basename
        image_format = DTB_TO_FORMAT[bname]
    elif 'xgene-uboot' in ptoks:
        image_format = 'xgene'
    elif len(ptoks) == 4:
        for ifmt in IMAGE_FORMATS:
            if ifmt in ptoks:
                image_format = ifmt
                break

    # kernel flavor
    # if an element of te path contains a known kernel flavor
    # or <release>-<flavor>
    kernel_flavor = "generic"
    for kflav in KERNEL_FLAVORS:
        if kflav in ptoks:
            kernel_flavor = kflav
            break
        else:
            for r in releases:
                if "%s-%s" % (r, kflav) in ptoks:
                    kernel_flavor = kflav
                    break

    return {'ftype': ftype, 'image-format': image_format,
            'initrd-flavor': initrd_flavor, "kernel-flavor": kernel_flavor,
            'kernel-release': kernel_release}


def get_kfile_key(release, kernel_release, kflavor, iflavor, ftype,
                  imgfmt=None, basename=None):
    # create the 'item_id' for a kernel file
    # return 2 chars of release, 2 chars of kernel_release
    #        3 chars of kflavor, 3 chars of iflavor, 2 chars for file
    if iflavor is None:
        iflavtok = "0"
    else:
        iflavtok = iflavor[0:3]
    flavtok = FLAVOR_COLLISIONS.get(kflavor, kflavor[0:3])
    if ftype == "dtb" and basename:
        if basename.endswith(".dtb"):
            basename = basename[:-4]
        fmttok = "." + basename
    elif imgfmt:
        fmttok = "." + imgfmt
    else:
        fmttok = ""

    return (release[0:2] + kernel_release[0:2] + flavtok + ftype[0:2] +
            iflavtok + fmttok)


def mine_md(url, release):
    # url is like:
    #  http://archive.ubuntu.com/ubuntu/dists/precise-updates/main
    #      /installer-i386/
    # returns versions dict with
    #  versions[serial]['items'] entries
    #  where serial are each directory listed in url
    #  and items are files, with a 'url' to full url to that file
    if url.endswith("/"):
        url = url[:-1]

    dirs = list_apache_dirs(url)
    usable = [f for f in dirs if YYYYMMDD_RE.match(f[0])]

    versions = {}

    regex = re.compile("^(.*netboot|.*device-tree|generic/)")
    for (di_ver, pubdate) in usable:
        versions[di_ver] = {'items': {}}
        curp = '/'.join((url, di_ver, 'images',))
        flist = get_file_sums_list(curp, mfilter=regex.match)
        for path in flist:
            # files likely start with './'
            if path.startswith("./"):
                path = path[2:]

            data = get_file_item_data(path, release=release)
            if data is None:
                continue

            data['size'] = geturl_len("/".join((curp, path,)))
            data['url'] = curp + "/" + path
            data['pubdate'] = pubdate
            data['basename'] = path[path.rfind('/')+1:]

            key = get_kfile_key(release=release,
                                kernel_release=data.get('kernel-release'),
                                kflavor=data.get('kernel-flavor'),
                                iflavor=data.get('initrd-flavor'),
                                ftype=data.get('ftype'),
                                imgfmt=data.get('image-format'),
                                basename=data.get('basename'))

            if key in versions[di_ver]['items']:
                raise Exception(
                    "Name Collision: %s[%s]: %s.\nCollided with: %s" %
                    (key, release, data, versions[di_ver]['items'][key]))

            curfile = flist[path].copy()
            curfile.update(data)
            versions[di_ver]['items'][key] = curfile

    return versions


class MineNetbootMetaData(threading.Thread):
    def __init__(self, in_queue, out_queue, name):
        threading.Thread.__init__(self)
        self.in_queue = in_queue
        self.out_queue = out_queue
        self.name = name

    def run(self):
        fprefix = FILES_PREFIX
        while True:
            data = self.in_queue.get()

            release = data['release']
            arch = data['arch']

            LOG.debug("%s mining %s from %s" %
                      (self.name, release, data['inst_url']))
            try:
                found = mine_md(url=data['inst_url'], release=release)
            except Exception as e:
                LOG.warn("%s mining %s at %s failed" %
                         (self.name, release, data['inst_url']), exc_info=1)
                data['error'] = e
                self.out_queue.put(data)
                self.in_queue.task_done()
                continue

            # now create a mapping, "local path" -> full url
            data['map'] = {}
            try:
                for serial, vdata in found.items():
                    for item in vdata['items'].values():
                        ndir = fprefix + '/'.join((release, arch, serial,
                                                   item['kernel-release'],
                                                   item['kernel-flavor'],))
                        if item['ftype'] == "kernel":
                            npath = ndir + "/kernel"
                        elif item['ftype'] == 'initrd':
                            npath = ndir + "/initrd-%s" % item['initrd-flavor']
                        elif item['ftype'] == 'dtb':
                            npath = ndir + "/dtb"
                        else:
                            raise Exception("unknown ftype '%s' in '%s'" %
                                            (item['ftype'], item))

                        if item.get('ftype') == 'dtb':
                            npath = npath + "." + item['basename']
                        elif 'image-format' in item and item['image-format']:
                            npath = npath + "." + item['image-format']

                        if npath in data['map']:
                            msg = ("npath collide '%s'. old: %s\n new: %s\n" %
                                   (npath, data['map'][npath], item))
                            raise ValueError(msg)

                        item['path'] = npath
                        data['map'][npath] = item['url']
                        del item['url']
            except Exception as e:
                LOG.warn("path creation failed: %s rel=%s" %
                         (self.name, release), exc_info=1)
                LOG.warn("excption: %s" % e)
                data['error'] = e
                self.out_queue.put(data)
                self.in_queue.task_done()
                continue

            data['versions'] = found
            data['error'] = False

            self.out_queue.put(data)
            self.in_queue.task_done()


def get_products_data(content_id=CONTENT_ID, arches=ARCHES, releases=None,
                      pockets=None):

    in_queue = queue.Queue()
    out_queue = queue.Queue()

    if releases is None:
        releases = SUPPORTED.keys()

    if pockets is None:
        pockets = POCKETS

    num_places = len(releases) * len(pockets) * len(arches)
    places = "%s * %s * %s" % (releases, [p for p in pockets], arches)
    num_t = min(num_places, NUM_THREADS)

    LOG.info("mining d-i data from %s places in %s threads. [%s]." %
             (num_places, num_t, places))

    for i in range(num_t):
        t = MineNetbootMetaData(in_queue, out_queue, i)
        t.setDaemon(True)
        t.start()

    for release in releases:
        ver = REL2VER[release]['version']
        for (pocket, psuffix) in pockets.items():
            for arch in arches:
                mirror = HTTP_MIRRORS.get(arch, HTTP_MIRRORS.get('default'))
                path = "/%s/main/installer-%s" % (release + psuffix, arch)
                data = {
                    'arch': arch,
                    'pocket': pocket,
                    'psuffix': psuffix,
                    'version': ver,
                    'release': release,
                    'inst_url': mirror + path,
                }
                in_queue.put(data)
    in_queue.join()
    LOG.info("finished mining of %s. now processing." % places)

    count = 0
    # now we process data serially.
    # data in out_queue looks just like what was put in
    # but has a 'versions' entry now and a 'map' entry
    dom = "com.ubuntu.installer"
    rdata = {'products': {}, 'format': 'products:1.0',
             'content_id': content_id}
    pathmap = {}
    errors = []
    while True:
        try:
            count = count + 1
            data = out_queue.get(block=False)
            out_queue.task_done()
            if data['error']:
                errors.append(data['error'])
                continue

            pname = (dom + ":netboot:%(version)s:%(arch)s" % data)

            versions = data['versions'].copy()
            for _k, v in versions.items():
                v['pocket'] = data['pocket']

            if pname not in rdata['products']:
                rdata['products'][pname] = {
                    'release': data['release'], 'version': data['version'],
                    'arch': data['arch'], 'versions': versions}
                rdata['products'][pname].update(
                    release_common_tags(data['release']))
            else:
                rdata['products'][pname]['versions'].update(versions)

            pathmap.update(data['map'])

            # print("%d: %s: %s" % (count, pname, rdata['products'][pname]))

        except queue.Empty:
            out_queue.join()
            break

    if len(errors):
        LOG.warn("There were %s errors, raising first" % len(errors))
        raise errors[0]

    simplestreams.util.products_condense(rdata)
    return (rdata, pathmap)


def get_di_kernelinfo(releases=None, arches=None, asof=None, pockets=None):
    # this returns a mirror reference and dict tree like
    # items['precise']['amd64']['generic']['saucy']['kernel']
    # where nodes are flattened to have data (including url)
    smirror = NetbootMirrorReader(releases=releases, arches=arches,
                                  pockets=pockets)
    netproducts = smirror._get_products()

    # TODO: implement 'asof' to get the right date, right now only returns
    # latest.

    items = {}
    tree_order = ('release', 'arch', 'kernel-flavor', 'kernel-release',
                  'image-format')

    def fillitems(item, tree, pedigree):
        flat = sutil.products_exdata(tree, pedigree)
        path = [flat[t] for t in tree_order]
        cur = items
        for tok in path:
            if tok not in cur:
                cur[tok] = {}
            cur = cur[tok]

        flat['url'] = smirror.source(item['path']).url
        ftype = 'di-' + flat['ftype']
        if (ftype not in cur or
                cur[ftype]['version_name'] < flat['version_name']):
            cur[ftype] = flat.copy()

    sutil.walk_products(netproducts, cb_item=fillitems)

    return (smirror, items)


def release_common_tags(release):
    relkeys = ('release', 'release_codename', 'release_title', 'support_eol')
    return {k: v for k, v in REL2VER[release].items() if k in relkeys}


def main():
    log.basicConfig(stream=sys.stderr, level=log.DEBUG)
    smirror = NetbootMirrorReader(arches=["armhf"], releases=["trusty"])
    tstore = objectstores.FileStore("out.d")
    tmirror = mirrors.ObjectStoreMirrorWriter(config=None, objectstore=tstore)

    cpath = "streams/v1/%s.json" % CONTENT_ID
    tmirror.sync(smirror, cpath)


def main_test():
    # import sys
    # for a in sys.argv[1:]:
    #     print(get_file_item_data(a, rel="precise"))
    # for url in sys.argv[1:]:
    #     info = geturl_text(url)
    #     for line in info.splitlines():
    #         (cksum, fpath) = line.split()
    #         print(fpath, get_file_item_data(fpath))

    # print(json.dumps(mine_md(sys.argv[1]), indent=1))
    ret = get_products_data()
    print(json.dumps(ret, indent=1))

if __name__ == '__main__':
    # executable as 'python3 -m meph2.netinst'
    main()
