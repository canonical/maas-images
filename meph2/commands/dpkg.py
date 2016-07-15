from platform import linux_distribution
import shutil
import subprocess
import hashlib
import io
import lzma
import os
import re
import sys
import tempfile
import urllib.request
import glob


def get_distro_release():
    """Returns the release name for the running distro."""
    disname, version, codename = linux_distribution()
    return codename


def get_file(url):
    """Downloads the file from the given URL into memory.

    :param url" URL to download
    :return: File data, or None
    """
    # Build a newer opener so that the environment is checked for proxy
    # URLs. Using urllib2.urlopen() means that we'd only be using the
    # proxies as defined when urlopen() was called the first time.
    try:
        response = urllib.request.build_opener().open(url)
        return response.read()
    except urllib.error.URLError as e:
        sys.stderr.write("Unable to download %s: %s" % (url, str(e.reason)))
        sys.exit(1)
    except BaseException as e:
        sys.stderr.write("Unable to download %s: %s" % (url, str(e)))
        sys.exit(1)


def get_sha256(data):
    """Returns the SHA256SUM for the provided data."""
    sha256 = hashlib.sha256()
    sha256.update(data)
    return sha256.hexdigest()


def gpg_verify_data(signature, data_file):
    """Verify's data using the signature."""
    tmp = tempfile.mkdtemp(prefix='maas-images')
    sig_out = os.path.join(tmp, 'verify.gpg')
    with open(sig_out, 'wb') as stream:
        stream.write(signature)

    data_out = os.path.join(tmp, 'verify')
    with open(data_out, 'wb') as stream:
        stream.write(data_file)

    subprocess.check_output(
        ['gpgv', '--keyring', '/etc/apt/trusted.gpg', sig_out, data_out],
        stderr=subprocess.STDOUT)

    shutil.rmtree(tmp, ignore_errors=True)


# Cache packages
_packages = {}


def get_packages(base_url, architecture, pkg_name):
    """Gets the package list from the archive verified."""
    global _packages
    release_url = '%s/%s' % (base_url, 'Release')
    path = 'main/binary-%s/Packages.xz' % architecture
    packages_url = '%s/%s' % (base_url, path)
    if packages_url in _packages:
        return _packages[packages_url]
    release_file = get_file(release_url)
    release_file_gpg = get_file('%s.gpg' % release_url)
    gpg_verify_data(release_file_gpg, release_file)

    # Download the packages file and verify the SHA256SUM
    pkg_data = get_file(packages_url)
    sha256sum = re.search(
        ("^\s*?(\w{64})\s*[0-9]+\s+%s$" % path).encode('utf-8'),
        release_file,
        re.MULTILINE).group(1)
    if get_sha256(pkg_data).encode('utf-8') != sha256sum:
        sys.stderr.write("Unable to verify %s" % packages_url)
        sys.exit(1)

    _packages[packages_url] = {}
    compressed = io.BytesIO(pkg_data)
    with lzma.LZMAFile(compressed) as uncompressed:
        pkg_name = None
        package = {}
        for line in uncompressed:
            line = line.decode('utf-8')
            if line == '\n':
                _packages[packages_url][pkg_name] = package
                pkg_name = None
                package = {}
                continue
            key, value = line.split(': ', 1)
            value = value.strip()
            if key == 'Package':
                pkg_name = value
            else:
                package[key] = value

    return _packages[packages_url]


def get_package(archive, pkg_name, architecture, release=None, dest=None):
    """Look through the archives for package metadata. If a dest is given
    download the package.

    :return: A dictionary containing the packages meta info or if a dest is
             given the path of the downloaded file."""
    global _packages
    release = get_distro_release() if release is None else release
    for dist in ('%s-updates' % release, release):
        base_url = '%s/dists/%s' % (archive, dist)
        packages = get_packages(base_url, architecture, pkg_name)
        if pkg_name in packages:
            package = packages[pkg_name]
            if dest is not None:
                pkg_data = get_file('%s/%s' % (archive, package['Filename']))
                if package['SHA256'] != get_sha256(pkg_data):
                    sys.stderr.write(
                        'SHA256 mismatch on %s from %s' % (pkg_name, base_url))
                    sys.exit(1)
                pkg_path = os.path.join(
                    dest, os.path.basename(package['Filename']))
                with open(pkg_path, 'wb') as stream:
                    stream.write(pkg_data)
            return package
    return None


def extract_files_from_packages(
        archive, packages, architecture, files, release, dest,
        grub_format=None):
    tmp = tempfile.mkdtemp(prefix='maas-images-')
    for package in packages:
        package = get_package(archive, package, architecture, release, tmp)
        pkg_path = os.path.join(tmp, os.path.basename(package['Filename']))
        if pkg_path is None:
            sys.stderr.write('%s not found in archives!' % package)
            sys.exit(1)
        subprocess.check_output(['dpkg', '-x', pkg_path, tmp])

    if grub_format is None:
        for f in files:
            shutil.copyfile(
                "%s/%s" % (tmp, f),
                "%s/%s" % (dest, os.path.basename(f)))
    else:
        # You can only tell grub to use modules from one directory
        modules_path = "%s/%s" % (tmp, files[0])
        modules = []
        for module_path in glob.glob("%s/*.mod" % modules_path):
            module_filename = os.path.basename(module_path)
            module_name, _ = os.path.splitext(module_filename)
            modules.append(module_name)
        subprocess.check_output(
            ['grub-mkimage',
            '-o', dest,
            '-O', grub_format,
            '-d', modules_path,
            '-c', dest] + modules)
    shutil.rmtree(tmp)
