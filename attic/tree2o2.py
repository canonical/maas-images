#!/usr/bin/python

from __future__ import (
    print_function,
)

import argparse
import csv
import hashlib
import logging
import json
import re
import os
import sys
import uuid

from o2lib import (
    base_structure,
    write_stream,
)

from pprint import pprint as pp
from distro_info import UbuntuDistroInfo


logger = logging.getLogger('_querygen_')
logging.basicConfig(format='%(asctime)s  %(levelname)s - %(message)s')
logger.setLevel(logging.DEBUG)

# The extensions we care about
extensions = [
    "tar.gz",
    "disk1.img",
    "uefi1.img",
    "root.tar.gz",
    "vhd.bz2",
    "vhd.zip",
]

# Convience function for creating extensions regex
extensions_re_str = ".*(%s)$" % "|".join(extensions)
extensions_re = re.compile(extensions_re_str)

# Arches to generate on
supported_arches = [
    "i386",
    "amd64",
    "armel",
    "armhf",
]

# Release tags that are acceptable
release_tags = [
    "alpha1",
    "alpha2",
    "alpha3",
    "beta1",
    "beta2",
    "beta3",
    "alpha-1",
    "alpha-2",
    "beta-1",
    "alpha-3",
    "beta-2",
    "beta-3",
    "beta",
    "rc1",
    "rc2"
    "rc"
]

# Don't put these in the suggested publish name
no_tag = [
    "release",
]

versions_info = []


def get_distro_version(key):
    # This function is a simplification of o2lib.uversion.get_distro_info().
    if len(versions_info) == 0:
        with open('/usr/share/distro-info/ubuntu.csv', 'r') as f:
            reader = csv.reader(f, delimiter=',')
            all_rows = [row for row in reader]
            versions_info.extend(all_rows[1:])

    for v in versions_info:
        if key in v:
            # Return the version without the LTS addition.
            return v[0].split(' ')[0]
    return None


def hash_for_file(fname, sumfile):
    contents = ""
    bname = os.path.basename(fname)
    dirname = os.path.dirname(fname)
    with open(os.path.join(dirname, sumfile), "r") as fp:
        contents = fp.read()
    for line in contents.splitlines():
        (cksum, fname) = line.split(None, 1)
        if fname[0] == "*":
            fname = fname[1:]
        if fname == bname:
            return cksum


def md5_for_file(f):
    try:
        return hash_for_file(os.path.basename(f),
                             os.path.join(os.path.dirname(f), "MD5SUMS"))
    except:
        pass

    logger.info("Checksumming [md5] %s", f)
    md5 = hashlib.md5()
    with open(f, 'rb') as r:
        md5.update(r.read())
    return md5.hexdigest()


def sha256_for_file(f):
    try:
        return hash_for_file(os.path.basename(f),
                             os.path.join(os.path.dirname(f), "SHA256SUMS"))
    except:
        pass

    logger.info("Checksumming [sha256] %s", f)
    sha2 = hashlib.sha256()
    with open(f, 'rb') as r:
        sha2.update(r.read())
    return sha2.hexdigest()


def find_files(base_d, checksums=True, subid=None):
    found = {}
    for root, dirs, files in os.walk(base_d):
        for name in files:
            name_f = (os.path.join(root, name))
            if not extensions_re.match(name):
                logger.info("Skipping %s" % name_f.replace(base_d, ''))
            else:
                details = make_file_details(name_f, root, checksums, subid)
                # Construct the dict structure
                if details['suite'] not in found:
                    found[details['suite']] = {}
                if details['stream'] not in found[details['suite']]:
                    found[details['suite']][details['stream']] = {}
                uniq_id = str(uuid.uuid4())
                found[details['suite']][details['stream']][uniq_id] = details
                logger.debug("Added %s" % details['pubname'])
    return found


def make_file_details(name, root, checksums, subid):
    name_f = (os.path.join(root, name))
    # Only run checksum if we want them. Useful for
    #   debugging
    md5, sha2 = None, None

    if checksums:
        md5 = md5_for_file(name_f)
        sha2 = sha256_for_file(name_f)
        logger.info("MD5 is %s" % md5)
        logger.info("SHA2 is %s" % sha2)
    else:
        # Spoof it
        md5, sha2 = ("FAKE-MD5-%s" % str(uuid.uuid4()).replace('-', ''),
                     "FAKE-SHA256-%s" % str(uuid.uuid4()).replace('-', ''))
        logger.info("MD5 is %s" % md5)
        logger.info("SHA2 is %s" % sha2)

    # Get file sizes
    fstat = os.stat(name_f)
    ftype = [x for x in extensions if x in name_f]

    details = {
        'suite': None,
        'label': None,
        'serial': None,
        'build_name': "server",
        'stream': None,
        'file': {
            'path': name_f,
            'size': fstat.st_size,
            'ftype': ftype[-1],
            'md5': md5,
            'sha256': sha2,
        },
    }

    # Look for a build info and use that if we can
    for rbinfo in ("unpacked/build-info.txt", "build-info.txt"):
        build_info = "%s/%s" % (root, rbinfo)
        if os.path.exists(build_info):
            try:
                with open(build_info, 'r',) as f:
                    for line in f.readlines():
                        l = line.split("=")

                        if l[0] in details:
                            details[l[0]] = l[1].strip()

                details['info_src'] = "build-info.txt"
                break
            except:
                logger.info("Failed to read %s" % build_info)

    # If we can't read the build_info, discern the properties
    if not details['suite'] or not details['serial']:
        logger.debug("Detecting build properties")

        # Assign a build name to things
        if subid:
            details['build_name'] = subid
        elif 'desktop' in name_f:
            details['build_name'] = 'desktop'
        else:
            details['build_name'] = 'server'

        # Divide and conquer on the path elements
        for s in name_f.split('/'):

            # Discern the suite
            if not details['suite'] and s in UbuntuDistroInfo().all:
                logger.debug("Found %s %s" % (s, name_f))
                details['suite'] = s
                details['info_src'] = 'path'
                logger.debug("Detected suite of %s" % details['suite'])

            # Attempt to discern the serial
            if not details['serial']:
                if re.match(r'.*\d{8}$', s) or re.match(r'.*\d{8}\.\d{1,}', s):
                    details['serial'] = "".join([i for i in s
                                                 if (i.isdigit() or i == ".")])
                    details['info_src'] = 'path'
                    logger.debug("Detected serial of %s" % details['serial'])

            # Don't do any more work than needed
            if details['suite'] and details['serial']:
                break

    # find the label
    for s in name_f.split("/"):
        if details['label']:
            break
        if s.startswith('release-'):
            details['label'] = 'release'
        elif s == "daily":
            details['label'] = 'daily'
        elif s == "rc":
            details['label'] = 'rc'
        elif re.match(r'(alpha|beta)[0-9]', s):
            details['label'] = s
        elif re.match(r'(alpha|beta)-[0-9]', s):
            details['label'] = s.replace("-","")

    missing = []
    for req in ('suite', 'serial', 'label'):
        if details.get(req) is None:
            missing.append(req)
    if len(missing):
        raise Exception("Did not find required info for '%s': %s"
                        (name, str(missing)))

    if details['label'] == 'daily':
        details['stream'] = 'daily'
    else:
        details['stream'] = 'releases'


    details['arch'] = [x for x in supported_arches if x in name][-1]

    details['version'] = get_distro_version(details['suite'])

    # Generate the pub nams
    #  ubuntu-natty-11.04-beta1-amd64-server-20110329
    #  ubuntu-maverick-10.10-rc-amd64-server-20100928.4
    #  ubuntu-lucid-10.04-amd64-server-20100827
    #  ubuntu-lucid-10.04-rc-amd64-server-20100420
    #  ubuntu-natty-daily-amd64-desktop-20110925
    #  ubuntu-oneiric-alpha1-amd64-server-20110601
    if details['label'] == "daily" or details['label'].startswith("alpha"):
        # daily or alpha do not get version and have label
        sug_name_template = (
            "ubuntu-%(codename)s-%(label)s-%(arch)s"
            "-%(build)s-%(serial)s")
    elif details['label'] == "release":
        # release get version, no label
        sug_name_template = (
            "ubuntu-%(codename)s-%(version)s-%(arch)s"
            "-%(build)s-%(serial)s")
    else:
        # beta or rc get version and label
        sug_name_template = (
            "ubuntu-%(codename)s-%(version)s-%(label)s-%(arch)s"
            "-%(build)s-%(serial)s")

    details['pubname'] = sug_name_template % {
        'codename': details['suite'],
        'version': details['version'],
        'label': details['label'],
        'arch': details['arch'],
        'build': details['build_name'],
        'serial': details['serial'],
    }
    return details


def consolidate_found(found, base_d=None, stream="releases", subid=None,
                      namespace=None, product_namespace=None):
    """
        Conslidates the found files into the Simple Streams
        Format
            base_d: Base directory
            stream: releases, or dailies
            subid: the subid to use, i.e. None, or Azure
            out_d: where to drop the files
            namespace: The identification, i.e com.ubuntu.cloud
    """

    # complete_namespace adds the namespace and the product_namespace
    #   i.e if namespace is 'com.ubuntu.cloud' and product_namespace is
    #       'releases:download' then it would be
    #           'com.ubuntu.cloud:releases:download'
    complete_namespace = namespace

    if 'releases' not in stream:
        complete_namespace = "%s.%s" % (complete_namespace, stream)

    # Get the header for the output
    consolidated = base_structure(complete_namespace, cloud='download')
    content_id = "%s:download" % complete_namespace

    # Iterate over the found dict to find the items
    for item in found:

        # Evaludate only the stream that we want to
        #   i.e releases versus daily
        if stream in found[item]:
            anon = {}

            # This iterates over all the files.
            for element in found[item][stream]:
                simple = found[item][stream][element]

                # See if product exists, i.e "server:raring:amd64"
                if ('version' in simple and 'build_name' in simple
                        and 'suite' in simple and 'arch' in simple):
                    # Identify the product
                    _bname = simple['build_name']
                    if subid:
                        _bname = subid

                    product = "%s:%s:%s" % (
                        _bname,
                        simple['version'],
                        simple['arch'],
                    )

                    # Append namespace to the front of the product
                    #       i.e com.ubuntu.cloud:server:12.04:i386
                    if product_namespace:
                        product = "%s:%s" % (complete_namespace, product)

                    # If the product isn't in the list, add it
                    if product not in anon:
                        anon[product] = {
                            'release': simple['suite'],
                            'version': simple['version'],
                            'arch': simple['arch'],
                            'versions': {},
                        }

                    # If the serial isn't known to the product, add it
                    if simple['serial'] not in anon[product]['versions']:
                        anon[product]['versions'][simple['serial']] = {
                            'pubname': simple['pubname'],
                            'label': simple['label'],
                            'items': {},
                        }

                    # Add the file type to product serial items
                    file_item = simple['file']
                    file_type = file_item['ftype']
                    file_serial = simple['serial']

                    # /me being lazy, because this damn spec changes way
                    # too much to be useful for anything
                    fr = "%s/%s" % (base_d, stream)
                    _fpath = file_item['path'].replace(fr, '')
                    if _fpath[0] == '/':
                        file_item['path'] = _fpath[1:]
                    else:
                        file_item['path'] = _fpath

                    # Create the new item now...
                    new_item = {}
                    for k, v in file_item.iteritems():
                        new_item[k] = v

                    # Finally add the new item to the consolidated dict
                    items = anon[product]['versions'][file_serial]['items']
                    items[file_type] = new_item

                # Something isn't right...
                else:
                    logger.warn("Invalid data encountered.")
                    pp(simple)

            # Add consolidated values to the stream
            for k, v in anon.iteritems():
                consolidated['products'][k] = v

    return consolidated, content_id


def report(consolidated, content_id, out_d, stream, gpg_key):
    final_out = "%s/%s" % (out_d, stream)
    if out_d:
        write_stream(final_out, consolidated, content_id, gpg_key_id=gpg_key)
    else:
        logger.info(
            "The following would have been written with --out_d defined.")
        print(json.dumps(consolidated, sort_keys=True, indent=3))


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--prefix',
        action="store",
        default="cpc",
        help="Cloud Image Prefix")
    parser.add_argument(
        '--out_d',
        action="store",
        default=None,
        required=False,
        help="Location to stuff query tree")
    parser.add_argument(
        '--base_d',
        action="store",
        default=None,
        required=True,
        help="Location of tree to create query from")
    parser.add_argument(
        '--subid',
        action="store",
        default=None,
        required=False,
        help="Variant id, i.e. Azure, Vagrant")
    parser.add_argument(
        '--namespace',
        action="store",
        default="com.ubuntu.cloud",
        required=False,
        help="Namespace to use")
    parser.add_argument(
        '--sub_namespace',
        action="store",
        default="released:download",
        required=False,
        help="Sub-namespace to use")
    parser.add_argument(
        "--stream",
        action="store",
        default="releases",
        required=True,
        help="Stream to use")
    parser.add_argument(
        "--no_sums",
        action="store_false",
        default=True,
        help="Skip checksumming of files")
    parser.add_argument(
        "--gpg_key",
        action="store",
        default=None,
        help="GPG key to sign with")

    opts = parser.parse_args()

    found = find_files(opts.base_d, checksums=opts.no_sums, subid=opts.subid)

    consolidated, content_id, = consolidate_found(
        found,
        base_d=opts.base_d,
        subid=opts.subid,
        namespace=opts.namespace,
        product_namespace=opts.sub_namespace,
        stream=opts.stream,
    )
    report(consolidated, content_id, opts.out_d, opts.stream, opts.gpg_key)

    # Redundant, but who cares?
    sys.exit(0)
