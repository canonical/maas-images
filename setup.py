from distutils.core import setup
from glob import glob
import os

VERSION = '0.1.0'


def is_f(p):
    return os.path.isfile(p)

setup(
    name="maas-images",
    description='Build imagse for maas',
    version=VERSION,
    author='Scott Moser',
    author_email='scott.moser@canonical.com',
    license="AGPL",
    url='http://launchpad.net/maas-images/',
    packages=[
        'meph2',
        'meph2.commands',
    ],
    scripts=glob('bin/*')
)
