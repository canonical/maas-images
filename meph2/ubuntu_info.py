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

# copied from simplestreams tools/ubuntu_versions.py
import distro_info


def get_ubuntu_info(date=None):
    # this returns a sorted list of dicts
    # each dict has information about an ubuntu release.
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
    supported_esm = udi.supported_esm(date=date)
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
    eol_esm_key = "eol-esm"
    try:
        ubuntu_rows = udi._rows
    except AttributeError:
        ubuntu_rows = [row.__dict__ for row in udi._releases]
        # if we are using _rows directly then the dict
        # key for ESM eol is different to when we use a
        # dict representation of DistroRelease objects
        eol_esm_key = "eol_esm"

    hack_all = {i['series']: i for i in ubuntu_rows}
    for i, codename in enumerate(codenames):
        title = "%s LTS" % versions[i] if lts[i] else versions[i]
        eol = hack_all[codename]['eol'].strftime("%Y-%m-%d")

        if eol_esm_key in hack_all[codename] and \
                hack_all[codename][eol_esm_key]:
            eol_esm = hack_all[codename][eol_esm_key].strftime("%Y-%m-%d")
        else:
            # If eol_esm is None then this release does not receive ESM support
            # As such we should set the esm_eol to the same as eol
            eol_esm = eol
        release_date = hack_all[codename]['release'].strftime("%Y-%m-%d")
        ret.append({'lts': lts[i], 'version': versions[i],
                    'supported': codename in supported,
                    'supported_esm': codename in supported_esm,
                    'codename': codename,
                    'support_eol': eol,
                    'support_esm_eol': eol_esm,
                    'release_codename': full_codenames[i],
                    'release_date': release_date,
                    'devel': bool(codename == devel),
                    'release_title': title})

    return ret


REL2VER = {k['codename']: k for k in get_ubuntu_info()}

# If you needed to add an entry to REL2VER for a newer release
# then was available in distro_info, then run this program as main, and
# then follow the output to add an entry like this
# REL2VER["newcodename"] = {
#    "lts": False, "supported": True, "release_title": "18.10",
#    "devel": True, "release_codename": "Crazy Canvas",
#    "version": "18.10", "codename": "crazy", "support_eol": "2019-07-31",
#    "release_date": "2018-10-20"}

LTS_RELEASES = [d for d in REL2VER if REL2VER[d]['lts']]
SUPPORTED = {d: v for d, v in REL2VER.items() if v['supported']}
SUPPORTED_ESM = {d: v for d, v in REL2VER.items() if v['supported_esm']}

if __name__ == '__main__':
    import json
    print(json.dumps(REL2VER, indent=1))
