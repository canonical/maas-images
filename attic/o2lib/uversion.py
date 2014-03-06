import csv
import sys

versions_info = []
def get_distro_info(
    key,
    index=0,
    chomp=True,
    all=False,
    strict=True,
    dates=True,
    ):

    """
        key: what to look for
        index: return a specific index in the string
                0: version string
                1: Full name
                2: Codename
        chomp: return stuff before space
        all: return the full string

        Examples:

        Calling with "get_distro_info('raring', chomp=True, index=None) gets:
            ['13.04', 'Raring Ringtail', 'raring', '2012-10-18', '2013-04-25', '2014-10-25']

        Calling with "get_distro_info('raring') gets:
            13.04

        Calling with "get_distro_info('precise') gets:
            12.04 LTS

        Calling with "get_distro_info('precise', chomp=True) gets:
            12.04

        Calling with "get_distro_info(None, all=True, index=2) gets:
            ['warty', 'hoary', 'breezy', 'dapper', 'edgy', 'feisty'...]
    """

    def chomper(l):
        if chomp:
            if isinstance(l, list):
                l = [ x.split()[0] for x in l ]
            else:
                l = l.split()[0]
        return l

    # Strip extraneous releases
    if key:
        split = key.split('.')
        if len(split) > 2:
            key = ".".join([split[0], split[1]])

    # Only read the version info once
    if len(versions_info) <= 0:
        with open('/usr/share/distro-info/ubuntu.csv', 'r') as f:
            reader = csv.reader(f, delimiter=',')
            count = 0
            for row in reader:
                if count >= 1:
                    _row = row[0:3]
                    if dates:
                        _row = row
                    versions_info.append(_row)
                count += 1
        f.close()

    # All printing all the info
    if all:
        if str(index).isdigit():
            _ret = []
            for v in versions_info:
                _ret.append(v[index])
            return chomper(_ret)

    for v in versions_info:
        for item in v:
            if key in item and 'LTS' not in key:

                if strict and key != item:
                    next

                if str(index).isdigit():
                   return chomper(v[index])

                elif isinstance(index, list):
                    _ret = []
                    for i in index:
                        _ret.append(v[i])

                    return chomper(_ret)

                else:
                    return chomper(v)

    return None

