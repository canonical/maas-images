#!/usr/bin/python3
#
import distro_info
import sys

## See LP: #1253208 for why this is so complicated.

def get_ubuntu_info(date=None):
    # this returns a sorted list of dicts
    # each dict has information about an ubuntu release.
    # Notably absent is any date information (release or eol)
    # its harder than you'd like to get at data via the distro_info library
    #
    # The resultant dicts looks like this:
    # {'codename': 'saucy', 'devel': True,
    #  'full_codename': 'Saucy Salamander',
    #  'fullname': 'Ubuntu 13.10 "Saucy Salamander"',
    #  'lts': False, 'supported': True, 'version': '13.10'}

    #udi = distro_info.DebianDistroInfo()
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
        sys.stderr.write(
            "distro_info.UbuntuDistroInfo() raised exception (%s)."
            " Using stable release as devel.\n" % e)
        devel = udi.stable(date=date)

    ret = []
    for i, codename in enumerate(codenames):
        ret.append({'lts': lts[i], 'version': versions[i],
                    'supported': codename in supported,
                    'fullname': fullnames[i], 'codename': codename,
                    'full_codename': full_codenames[i],
                    'devel': bool(codename == devel)})

    return ret

_d = get_ubuntu_info()

RELEASES = {d['codename']: d for d in _d}
LTS_RELEASES = [d for d in RELEASES if RELEASES[d]['lts']]
SUPPORTED = {d: v for d, v in RELEASES.items() if v['supported']}
