#!/usr/bin/python3
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

# this is patched over the top of distro_info
# to allow newer data here then available in the pkg installed distro_info

# copied from simplestreams tools/ubuntu_versions.py

# This data is only used if python-distro-info is not available and
# the user has set environment variable SS_REQUIRE_DISTRO_INFO=0
__RELEASE_DATA = (
    # version, full codename, lts
    ("8.04", "Hardy Heron", True),
    ("10.04", "Lucid Lynx", True),
    ("11.10", "Oneiric Ocelot", False),
    ("12.04", "Precise Pangolin", True),
    ("12.10", "Quantal Quetzal", False),
    ("13.04", "Raring Ringtail", False),
    ("13.10", "Saucy Salamander", False),
    ("14.04", "Trusty Tahr", True),
    ("14.10", "Utopic Unicorn", False),
    ("15.04", "Vivid Vervet", False),
    ("15.10", "Wily Werewolf", False),
    ("16.04", "Xenial Xerus", True),
    ("16.10", "Yakkety Yak", False),
    ("17.04", "Zesty Zapus", False),
    ("17.10", "Artful Aardvark", False),
)


def _get_fulldata(version, full_codename, lts):
    codename = full_codename.split()[0].lower()
    return {
        'codename': codename,
        'lts': lts,
        'release_title': "%s LTS" % version if lts else version,
        'release_codename': full_codename,
        'version': version,
    }


def get_ubuntu_info(date=None):
    # this returns a sorted list of dicts
    # each dict has information about an ubuntu release.
    # Notably absent is any date information (release or eol)
    # its harder than you'd like to get at data via the distro_info library
    #
    # The resultant dicts have the following fields:
    #  codename: single word codename of ubuntu release ('saucy' or 'trusty')
    #  devel: boolean, is this the current development release
    #  lts: boolean, is this release an LTS
    #  supported: boolean: is this release currently supported
    #  release_codename: the full code name ('Saucy Salamander', 'Trusty Tahr')
    #  version: the numeric portion only ('13.10', '14.04')
    #  release_title: numeric portion + " LTS" if this is an lts
    #                 '13.10', '14.04 LTS"

    udi = distro_info.UbuntuDistroInfo()
    # 'all' is a attribute, not a function. so we can't ask for it formated.
    # s2all and us2all are lists, the value of each is the index
    # where that release should fall in 'all'.
    allcn = udi.all
    s2all = [allcn.index(c) for c in
             udi.supported(result="codename", date=date)]
    us2all = [allcn.index(c) for c in
              udi.unsupported(result="codename", date=date)]

    def getall(result, date):
        ret = [None for f in range(0, len(allcn))]
        for i, r in enumerate(udi.supported(result=result, date=date)):
            ret[s2all[i]] = r
        for i, r in enumerate(udi.unsupported(result=result, date=date)):
            ret[us2all[i]] = r
        return [r for r in ret if r is not None]

    codenames = getall(result="codename", date=date)
    fullnames = getall(result="fullname", date=date)
    lts = [bool('LTS' in f) for f in fullnames]
    versions = [x.replace(" LTS", "") for x in
                getall(result="release", date=date)]
    full_codenames = [x.split('"')[1] for x in fullnames]
    supported = udi.supported(date=date)
    try:
        devel = udi.devel(date=date)
    except distro_info.DistroDataOutdated as e:
        import sys
        sys.stderr.write(
            "WARN: distro_info.UbuntuDistroInfo() raised exception (%s). "
            "Using stable release as devel.\n" % e)
        devel = udi.stable(date=date)
    ret = []

    # hack_all, because we're using '_rows', which is not supported
    # however it is the only real way to get at EOL, and is convenient
    # series there is codename to us
    hack_all = {i['series']: i for i in udi._rows}
    for i, codename in enumerate(codenames):
        title = "%s LTS" % versions[i] if lts[i] else versions[i]
        eol = hack_all[codename]['eol'].strftime("%Y-%m-%d")
        ret.append({'lts': lts[i], 'version': versions[i],
                    'supported': codename in supported,
                    'codename': codename,
                    'support_eol': eol,
                    'release_codename': full_codenames[i],
                    'devel': bool(codename == devel),
                    'release_title': title})

    return ret


__HARDCODED_REL2VER = {}
for __t in __RELEASE_DATA:
    __v = _get_fulldata(*__t)
    __HARDCODED_REL2VER[__v['codename']] = __v


try:
    import distro_info
    info = get_ubuntu_info()
    REL2VER = {}
    for r in info:
        if r['codename'] < "hardy":
            continue
        REL2VER[r['codename']] = r.copy()

    for r in __HARDCODED_REL2VER:
        if r not in REL2VER:
            REL2VER[r] = __HARDCODED_REL2VER[r]

except ImportError:
    import os
    import sys
    if os.environ.get("SS_REQUIRE_DISTRO_INFO", "1") not in ("0"):
        pkg = "python3-distro-info"
        if sys.version_info.major == 2:
            pkg = "python-distro-info"
        raise ValueError("Please install package %s "
                         "or set SS_REQUIRE_DISTRO_INFO=0" % pkg)
    REL2VER = __HARDCODED_REL2VER

LTS_RELEASES = [d for d in REL2VER if REL2VER[d]['lts']]
SUPPORTED = {d: v for d, v in REL2VER.items() if v['supported']}

if __name__ == '__main__':
    import json
    print(json.dumps(REL2VER, indent=1))
