#!/usr/bin/python
#
#  Shared library for generation of SimpleStreams Data
#  Copyright (C) 2013 Ben Howard <ben.howard@canonical.com>
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#

import re
import json
import os
import subprocess

from collections import OrderedDict
from datetime import datetime
from uversion import get_distro_info
from pytz import country_names

# Daily regex
daily_re_str = "(%s)" % "|".join(get_distro_info(None, all=True, index=2))
daily_re = re.compile(daily_re_str)
codename_re = re.compile(daily_re_str)

# Arches to generate on
supported_arches = [
    "i386",
    "amd64",
    "armel",
    "armhf",
    ]

# Regex for matching supported architectures
supported_arch_re_str = "(.*%s.*)" % ".*|.*".join(supported_arches)
arches_re = re.compile(supported_arch_re_str)

# Stream tags that are acceptable
release_tags = [
    "alpha1",
    "alpha2",
    "beta1",
    "beta2",
    "beta",
    ]

# Regex for matching release tags
release_re_str = "(%s)" % "|".join(release_tags)
release_re = re.compile(release_re_str)

# Stream tags that are acceptable
stream_tags = [
    "alpha-1",
    "alpha-2",
    "beta-1",
    "beta-2",
    "beta",
    "releases",
    "daily",
    ]

# Used to determine the serial
serial_re = re.compile('\d{8}$')


def base_structure(catalog_product_id, cloud):
    catalog = OrderedDict()
    catalog['updated'] = date_str()
    catalog['datatype'] = "image-ids"
    catalog['content_id'] = '%s:%s' % (catalog_product_id, cloud)
    catalog['products'] = {}
    catalog['format'] = "products:1.0"
    catalog['_aliases'] = { 'crsn': {} }

    return catalog


def date_str():
    return datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S +0000")

def sign_file(path, gpg_key=None):
    signed_f = path.replace('.json','.sjson')
    gpg_out = path + ".gpg"
    gpg_asc = path + ".asc"

    # Make sure things are clean
    if os.path.exists(signed_f):
        os.unlink(signed_f)
    if os.path.exists(gpg_out):
        os.unlink(gpg_out)
    if os.path.exists(gpg_asc):
        os.unlink(gpg_asc)

    inline = ['gpg']
    detached = ['gpg']

    # Define the default key
    if gpg_key:
        inline.extend(['--default-key', gpg_key])
        detached.extend(['--default-key', gpg_key])

    # Command for the .sjson
    inline.extend([
            '--batch',
            '--clearsign',
            '--detach-sign', path,
            ])

    # Command for the .gpg
    detached.extend([
            '--batch',
            '--armor',
            '--sign', path,
            ])

    # Produce inline signature
    subprocess.check_output(inline)
    out_f = path + ".asc"
    if not os.path.exists(out_f):
        raise Exception("Missing signed GPG file!")
    else:
        os.rename(out_f, signed_f)

    # Produce .gpg signature
    subprocess.check_output(detached)
    if not os.path.exists(out_f):
        raise Exception("Missing detached signature!")
    else:
        os.rename(out_f, gpg_out)


def write_stream(out_d, catalog, catalog_product_id, gpg_key_id=None, cloud=None):
    """
        Writes the catalog output and then gets it signed
    """

    file_f = "%s/streams/v1/%s.json" % (out_d, catalog_product_id)
    if cloud:
        file_f = file_f.replace('.json', ':%s.json' % cloud)

    base_d = os.path.dirname(file_f)

    if not os.path.exists(base_d):
        os.makedirs(base_d)

    with open(file_f, 'w') as f:
        json.dump(catalog, f, indent=1)
    f.close()

    sign_file(file_f, gpg_key_id)

def locations_crsn(loc):
    """
        Function to standardize the CRSN (Cloud Region Short Name)
            This should be used when the locations use names that
            use standard geo identifiers like compass points,
            country names, and standard regional names.
    """
    # Standardize the location
    _loc = re.sub('(\(|\)|-|/)', ' ', loc.lower())

    # Compass locations
    compass = {
        'north': 'nn',
        'northeast': 'ne',
        'northwest': 'nw',
        'south': 'ss',
        'southeast': 'se',
        'southwest': 'sw',
        'central': 'cc',
        'east': 'ee',
        'west': 'ww',
        }

    # General GEO's
    geos = {
        'europe': 'eu',
        'asia': 'as',
        'asiapacific': 'apac',
        'southamerica': 'sa',
        'us': 'us',
        }

    csrn_d = {
        'geo': None,
        'compass': None,
        'digit': '1',
        }

    for item in _loc.split():

        # Check if item is a digit
        if item.isdigit():
            csrn_d['digit'] = item
            continue

        # Get the country ISO3166 abbreviation
        try:
            country_short = [ abbr for abbr, name in country_names.items() \
                                if name.lower() == item ][-1]
            country_short = country_short.lower()
            csrn_d['geo'] = country_short
            continue
        except Exception as e:
            pass

        # If we get here, we may be GEO
        if item in geos:
            csrn_d['geo'] = geos[item]
            continue

        # Finally, compass
        if item in compass:
            csrn_d['compass'] = compass[item]

    return "%(geo)s%(compass)s%(digit)s" % csrn_d
