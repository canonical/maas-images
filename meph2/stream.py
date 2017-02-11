from . import DEF_MEPH2_CONFIG, util
from . netinst import (POCKETS, POCKETS_PROPOSED, get_di_kernelinfo,
                       release_common_tags)
from .ubuntu_info import REL2VER

import copy
import os
import subprocess
import sys
import yaml

from simplestreams.log import LOG

ALL_ITEM_TAGS = {'label': 'daily', 'os': 'ubuntu'}

PATH_COMMON = "%(release)s/%(arch)s/"
BOOT_COMMON = PATH_COMMON + "%(version_name)s/%(kname)s/%(flavor)s"
DI_COMMON = PATH_COMMON + "di/%(di_version)s/%(krel)s/%(flavor)s"
PATH_FORMATS = {
    'root-image.gz': PATH_COMMON + "%(version_name)s/root-image.gz",
    'root-image.manifest': (
        PATH_COMMON + "%(version_name)s/root-image.manifest"),
    'squashfs': PATH_COMMON + "%(version_name)s/squashfs",
    'squashfs.manifest': PATH_COMMON + "%(version_name)s/squashfs.manifest",
    'boot-dtb': BOOT_COMMON + "/boot-dtb%(suffix)s",
    'boot-kernel': BOOT_COMMON + "/boot-kernel%(suffix)s",
    'boot-initrd': BOOT_COMMON + "/boot-initrd%(suffix)s",
    'di-dtb': DI_COMMON + "/di-dtb%(suffix)s",
    'di-initrd': DI_COMMON + "/di-initrd%(suffix)s",
    'di-kernel': DI_COMMON + "/di-kernel%(suffix)s",
}
IMAGE_FORMATS = ['auto', 'img-tar', 'root-image', 'root-image-gz',
                 'root-tar', 'squashfs-image']


def read_kdata(info, ret=list):
    # read a kernel data list and return it as a list or a dict.

    # copy it for our modification.
    info = list(info)

    # 7th field is optional in kernel lines in config data
    # so fill it with empty dictionary if not present.
    if len(info) == 6:
        info.append({})

    names = ("krel", "arch", "subarch", "flavor", "kpkg",
             "subarches", "kdata")
    if ret == list:
        return info
    elif ret == dict:
        return dict(zip(names, info))
    else:
        raise ValueError("Unexpected input '%s'" % ret)


def create_version(arch, release, version_name, img_url, out_d,
                   include_di=True, cfgdata=None, common_tags=None,
                   verbosity=0, img_format=None):
    # arch: what dpkg arch (amd64, i386, ppc64el) to build this for
    # release: codename (trusty)
    # version_name: serial/build-number YYYYMMDD[.X])
    # img_url: url to the image to use for reference
    # out_d: where to store the stream output
    # include_di: should we scrape di data?
    # cfgdata: the v2 config file loaded as data
    # common_tags: these are applied to all items
    #
    # return value is a dictionary of
    #  {product_name: {'item_name': item, 'item2_name': item},
    #   product_name2: {item_name': item, 'item2_name': item},
    #   ...}
    # each 'item' above is a dictionary like:
    #   {'arch': 'amd64', 'path': 'xenial/amd64/....', 'sha256': ..}
    if common_tags is None:
        common_tags = {}

    mci2e_flags = []
    if verbosity:
        mci2e_flags.append('-' + 'v' * verbosity)

    if img_format is not None:
        if img_format not in IMAGE_FORMATS:
            raise ValueError("img_format='%s' invalid.  Must be one of: %s" %
                             img_format, IMAGE_FORMATS)
        mci2e_flags.append('--format=%s' % img_format)

    if cfgdata is None:
        with open(DEF_MEPH2_CONFIG) as fp:
            cfgdata = yaml.load(fp)

    rdata = None
    for r in cfgdata['releases']:
        if r['release'] == release:
            if rdata is not None:
                raise ValueError("Multiple entries with release=%s in config",
                                 release)
            rdata = r

    arches = set([read_kdata(i, dict)['arch'] for i in rdata['kernels']])
    if arch not in arches:
        msg = (
            "arch '%(arch)s' is not supported for release '%(release)s'.\n"
            "Release has architectures: %(arches)s.\n"
            "To support, add kernel info to config." %
            {'arch': arch, 'release': release, 'arches': arches})
        LOG.warn(msg)
        sys.stderr.write(msg + "\n")
        return {}

    version = rdata['version']
    if isinstance(version, float):
        raise ValueError("release '%s' in config had version as a float (%s) "
                         "It must be a string." % (release, version))

    enable_proposed = cfgdata.get('enable_proposed', False)

    # default kernel can be:
    #  string or None: use this as the value for all arch
    #  dictionary of arch with default in 'default'.
    #    If no default, use linux-generic
    #      {armhf: linux-highbank, 'default': 'linux-foo'}
    dkdata = rdata.get('builtin_kernel')
    if isinstance(dkdata, str) or dkdata is None:
        builtin_kernel = dkdata
    elif isinstance(dkdata, dict):
        if arch in dkdata:
            builtin_kernel = dkdata[arch]
        else:
            builtin_kernel = dkdata.get('default', 'linux-generic')

    if builtin_kernel:
        bkparm = "--kernel=%s" % builtin_kernel
    else:
        bkparm = "--kernel=none"

    mci2e = os.environ.get('MAAS_CLOUDIMG2EPH2', "maas-cloudimg2eph2")

    if include_di:
        di_pockets = POCKETS
        if enable_proposed:
            di_pockets = POCKETS_PROPOSED

        (di_mirror, _all_di_data) = get_di_kernelinfo(
            releases=[release], arches=[arch], pockets=di_pockets)
        di_kinfo = _all_di_data[release][arch]

    newitems = {}

    subs = {'release': release, 'arch': arch, 'version_name': version_name,
            'version': version, 'product_id_pre': cfgdata['product_id_pre']}

    rootimg_path = PATH_FORMATS['root-image.gz'] % subs

    krd_packs = []
    squashfs = cfgdata.get('squashfs', False)
    base_boot_keys = ['boot-kernel', 'boot-initrd']
    if squashfs and img_url.endswith('.squashfs'):
        base_ikeys = base_boot_keys + ['squashfs', 'squashfs.manifest']
        manifest_path = PATH_FORMATS['squashfs.manifest'] % subs
        newpaths = set((PATH_FORMATS['squashfs'] % subs, manifest_path))
    else:
        base_ikeys = base_boot_keys + ['root-image.gz', 'root-image.manifest']
        manifest_path = PATH_FORMATS['root-image.manifest'] % subs
        newpaths = set((rootimg_path, manifest_path))

    if enable_proposed:
        mci2e_flags.append("--proposed")

    gencmd = ([mci2e] + mci2e_flags +
              [bkparm, "--arch=%s" % arch,
               "--manifest=%s" % os.path.join(out_d, manifest_path),
               img_url, os.path.join(out_d, rootimg_path)])

    kdata_defaults = {'suffix': "", 'di-format': "default", 'dtb': ""}

    for info in rdata['kernels']:
        (krel, karch, psubarch, flavor, kpkg, subarches, kdata) = (
            read_kdata(info))

        if karch != arch:
            continue

        for i in kdata_defaults:
            if i not in kdata:
                kdata[i] = kdata_defaults[i]

        # The subarch cannot contain the kernel flavor. We add it to the
        # product name so different kernels can be shown in the stream as
        # part of the product name.
        if flavor != 'generic':
            # If edge is in the subarch make sure it comes after the kflavor
            split_psubarch = psubarch.split('-')
            if split_psubarch[-1] == 'edge':
                split_psubarch.insert(-1, flavor)
            else:
                split_psubarch.append(flavor)
            product_psubarch = '-'.join(split_psubarch)
        else:
            product_psubarch = psubarch

        subs.update({'krel': krel, 'kpkg': kpkg, 'flavor': flavor,
                     'psubarch': product_psubarch, 'subarch': psubarch,
                     'suffix': kdata["suffix"]})

        kname = cfgdata.get('kname', '%(krel)s') % subs
        subs.update({'kname': kname})

        ikeys = copy.deepcopy(base_ikeys)
        boot_keys = copy.deepcopy(base_boot_keys)

        dtb = kdata.get('dtb')
        if dtb:
            ikeys.append('boot-dtb')
            boot_keys.append('boot-dtb')

        if include_di:
            msg = "flavor=%s krel=%s kdata=%s" % (flavor, krel, kdata)
            try:
                curdi = di_kinfo[flavor][krel][kdata['di-format']]
            except KeyError:
                raise KeyError("no d-i kernel info for " + msg)

            di_version = curdi['di-kernel']['version_name']
            subs.update({'di_version': di_version})
            di_keys = ['di-kernel', 'di-initrd']
            if dtb:
                di_keys.append('di-dtb')
            ikeys += di_keys
        else:
            curdi = "DI_NOT_ENABLED"
            di_keys = []

        prodname = (
            "%(product_id_pre)s:%(version)s:%(arch)s:%(psubarch)s" % subs)
        if prodname in newitems:
            raise ValueError("duplicate prodname %s from %s" %
                             (prodname, subs))

        common = {'subarches': ','.join(subarches), 'krel': krel,
                  'release': release, 'version': version, 'arch': arch,
                  'subarch': psubarch, 'kflavor': flavor}
        common.update(ALL_ITEM_TAGS)

        if release in REL2VER:
            common.update(release_common_tags(release))

        if common_tags:
            common.update(common_tags)

        items = {}
        for i in ikeys:
            # Allow root-image.manifest and squashfs.image to have different
            # filenames but keep the same ftype.
            if 'manifest' in i:
                ftype = 'manifest'
            else:
                ftype = i
            items[ftype] = {'ftype': ftype, 'path': PATH_FORMATS[i] % subs,
                            'size': None, 'sha256': None}
            items[ftype].update(common)

        for key in di_keys:
            items[key]['sha256'] = curdi[key]['sha256']
            items[key]['size'] = int(curdi[key]['size'])
            items[key]['_opath'] = curdi[key]['path']
            items[key]['di_version'] = di_version

        for key in boot_keys:
            items[key]['kpackage'] = kpkg

        pack = [
            kpkg,
            os.path.join(out_d, items['boot-kernel']['path']),
            os.path.join(out_d, items['boot-initrd']['path']),
        ]
        if dtb:
            dtb_path = items['boot-dtb']['path']
            pack.append("--dtb=%s=%s" % (dtb, os.path.join(out_d, dtb_path)))
            newpaths.add(dtb_path)

        if 'kihelper' in kdata:
            pack.append('--kihelper=%s' % kdata['kihelper'])

        krd_packs.append(pack)

        newpaths.add(items['boot-kernel']['path'])
        newpaths.add(items['boot-initrd']['path'])

        newitems[prodname] = items

    for pack in krd_packs:
        gencmd.append('--krd-pack=' + ','.join(pack))

    if len([p for p in newpaths
            if not os.path.exists(os.path.join(out_d, p))]) == 0:
        LOG.info("All paths existed, not re-generating: %s" % newpaths)
    else:
        LOG.info("running: %s" % gencmd)
        subprocess.check_call(gencmd)
        LOG.info("finished: %s" % gencmd)

        if img_url.endswith('squashfs'):
            base_dir = os.path.join(out_d, release, arch, version_name)
            src_squash = os.path.join(base_dir, os.path.basename(img_url))
            if squashfs:
                # If we're publishing a SquashFS file rename it to its
                # filetype.
                dst_squash = os.path.join(base_dir, 'squashfs')
                os.rename(src_squash, dst_squash)
                # The root-img is used to generate the kernels and initrds. If
                # we're publishing the SquashFS image then we don't want to
                # publish the root-img, we can safely clean it up.
                src_rootimg_path = os.path.join(
                    base_dir, os.path.basename(rootimg_path))
                os.remove(src_rootimg_path)
            else:
                # If we're not publishing the SquashFS image but used it to
                # generate the root-img clean it up.
                os.remove(src_squash)

    # get checksum and size of new files created
    file_info = {}
    for path in newpaths:
        file_info[path] = util.get_file_info(os.path.join(out_d, path))

    for prodname in newitems:
        items = newitems[prodname]
        for item in items.values():
            item.update(file_info.get(item['path'], {}))

            lpath = os.path.join(out_d, item['path'])
            # items with _opath came from a di mirror
            if ('_opath' in item and not os.path.exists(lpath)):
                if not os.path.exists(os.path.dirname(lpath)):
                    os.makedirs(os.path.dirname(lpath))
                try:
                    srcfd = di_mirror.source(item['_opath'])
                    util.copy_fh(src=srcfd, path=lpath, cksums=item)
                except ValueError as e:
                    raise ValueError("%s had bad checksum (%s). %s" %
                                     (srcfd.url, item['_opath'], e))

            for k in [k for k in item.keys() if k.startswith('_')]:
                del item[k]

    return newitems


# vi: ts=4 expandtab syntax=python
