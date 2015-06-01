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
import requests
import urllib.request

import simplestreams
from simplestreams import mirrors
from simplestreams import objectstores
from simplestreams import log
from simplestreams.log import LOG

if __name__ == '__main__':
    from ubuntu_info import RELEASES, LTS_RELEASES, SUPPORTED
else:
    from .ubuntu_info import RELEASES, LTS_RELEASES, SUPPORTED


APACHE_PARSE_RE = re.compile(r'href="([^"]*)".*(..-...-.... '
                             r'..:..).*?(\d+[^\s<]*|-)')

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
# POCKETS.update({'proposed': '-proposed'})

ARCHES = ("i386", "amd64", "ppc64el", "armhf", "arm64")
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

DTB_TO_FORMAT = {
    "apm-mustang.dtb": "xgene"
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


def get_url_len(url):
    if url.startswith("file:///"):
        path = url[len("file://"):]
        return os.stat(path).st_size
    if os.path.exists(url):
        return os.stat(url).st_size

    request = urllib.request.Request(url)
    request.get_method = lambda: 'HEAD'
    response = urllib.request.urlopen(request)
    return int(response.headers.get('content-length', 0))


class NetbootMirrorReader(mirrors.MirrorReader):
    fpath = FILES_PREFIX
    content_id = CONTENT_ID
    _products = {}
    _pathmap = {}

    def __init__(self, releases=None, arches=None):
        if releases is None:
            releases = SUPPORTED.keys()

        if arches is None:
            arches = ARCHES

        self.releases = releases
        self.arches = arches

        def policy(content, path):
            return content

        super(NetbootMirrorReader, self).__init__(policy=policy)

        self._get_products(path=None)

    def source(self, path):
        if path == "streams/v1/index.json":
            return self._get_index()
        elif path == "streams/v1/%s.json" % self.content_id:
            return simplestreams.contentsource.MemoryContentSource(
                url=None, content=simplestreams.util.dump_data(
                    self._get_products(path)))
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
                                             arches=self.arches)

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
        fp.write(requests.get(url).content)


def gpg_check(filepath, gpgpath, keyring=GPG_KEYRING):
    cmd = ['gpgv', '--keyring=%s' % keyring, gpgpath, filepath]

    _output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
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
        html = requests.get(url).text
    except IOError as e:
        print('error fetching %s: %s' % (url, e))
        return
    if not url.endswith('/'):
        url += '/'
    files = APACHE_PARSE_RE.findall(html)
    dirs = []
    for name, date, size in files:
        if size.strip() == '-':
            size = 'dir'
        pubdate = simplestreams.util.timestamp(
            time.mktime(time.strptime(date, "%d-%b-%Y %H:%M")))
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
    # or a tuple of release-kernel, kernel-flavor, initrd-flavor, filetype

    # at the moment the only 'initrd-flavor' that we're supporting is netboot
    if path.find("netboot") < 0 and path.find("device-tree") < 0:
        return None
    iflavor = "netboot"

    path = path.replace("/netboot/", "/")

    # 'xen' is only historic now, but was a initrd flavor.
    path = path.replace("-xen/xen", "/xen")
    path = re.sub("netboot/ubuntu-installer/[^/]*/", "netboot/generic/", path)
    toks = path.split("/")
    if len(toks) == 2:
        if toks[0] == "netboot":
            path = "netboot/generic/" + toks[1]
        else:
            path = "netboot/" + toks[0] + "/" + toks[1]

    # generic/xgene/uInitrd is generic flavor,
    # xgene-uboot or xgene-uboot-mustang image
    imgfmt = "default"
    if (len(toks) == 4 and toks[1] == "generic" and (toks[3].startswith('uI'))):
        path = "%s-netboot/%s/%s" % (release, toks[1], toks[3])
        imgfmt = toks[2]
        # xgene-uboot -> xgene
        imgfmt = re.sub("-uboot$", "", imgfmt)
    elif (len(toks) == 2 and toks[0] == "device-tree" and
        toks[1].endswith('dtb')):
        path = "%s-netboot/generic/%s" % (release, toks[1])
        if toks[1] in DTB_TO_FORMAT:
            imgfmt = DTB_TO_FORMAT[toks[1]]
    # trusty & utopic used this layout for arm64
    elif (len(toks) == 3 and toks[0] == "generic" and
            (toks[2].startswith('uI') or toks[2].endswith('dtb'))):
        path = "%s-netboot/%s/%s" % (release, toks[0], toks[2])
        imgfmt = toks[1]

    path = re.sub("^netboot/", "%s-netboot/" % release, path)
    if path.find("-netboot/") < 1:
        return None
    path = re.sub("/ubuntu-installer/[^/]*/", "/", path)
    path = re.sub("/(vmlinu[xz]|linux|uImage)$", "/kernel", path)
    path = re.sub("/(initrd.gz|initrd|uInitrd)$", "/initrd", path)
    path = re.sub("/[^/]*.dtb", "/dtb", path)
    path = re.sub("-netboot", "", path)
    try:
        (frel, kflavor, ftype) = path.split("/")
    except ValueError:
        return None

    # realize that 'utopic-generic' is not a kernel flavor but
    # 'generic' flavor in utopic release.
    if frel == release:
        for r in RELEASES.keys():
            if kflavor.startswith(r + "-"):
                frel, kflavor = kflavor.split("-", 1)
                break

    if kflavor in INVALID_KERNEL_FLAVORS:
        return None

    if ftype not in ("kernel", "initrd", "dtb"):
        return None

    # frel (file release) is 'quantal' for hardware enablement
    # kernel from quantal, while 'release' is 'precise'.
    # this check below effectively only allows <release>-netboot/
    # for LTS releases > lucid.
    if (frel != release and
            (release not in LTS_RELEASES or release < "precise")):
        return None

    ret = {'kernel-flavor': kflavor, 'ftype': ftype, 'kernel-release': frel,
           'image-format': imgfmt}
    if ftype == 'initrd':
        ret['initrd-flavor'] = iflavor
    else:
        # this works around bug in condense.
        ret['initrd-flavor'] = None

    return ret


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

    regex = re.compile(".*(netboot|device-tree)")
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

            data['size'] = get_url_len("/".join((curp, path,)))
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


def get_products_data(content_id=CONTENT_ID, arches=ARCHES, releases=None):

    in_queue = queue.Queue()
    out_queue = queue.Queue()

    if releases is None:
        releases = SUPPORTED.keys()

    num_places = len(releases) * len(POCKETS) * len(arches)
    places = "%s * %s * %s" % (releases, [p for p in POCKETS], arches)
    num_t = min(num_places, NUM_THREADS)

    LOG.info("mining d-i data from %s places in %s threads. [%s]." %
             (num_places, num_t, places))

    for i in range(num_t):
        t = MineNetbootMetaData(in_queue, out_queue, i)
        t.setDaemon(True)
        t.start()

    for release in releases:
        ver = RELEASES[release]['version']
        for (pocket, psuffix) in POCKETS.items():
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
    #     info = requests.get(url).text
    #     for line in info.splitlines():
    #         (cksum, fpath) = line.split()
    #         print(fpath, get_file_item_data(fpath))

    # print(json.dumps(mine_md(sys.argv[1]), indent=1))
    ret = get_products_data()
    print(json.dumps(ret, indent=1))

if __name__ == '__main__':
    main()
