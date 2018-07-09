from platform import linux_distribution
import shutil
import subprocess
import hashlib
import io
import os
import re
import sys
import tarfile
import tempfile
import glob

from meph2.url_helper import geturl

try:
    import lzma
except ImportError:
    from backports import lzma

# Cache packages
_packages = {}


def get_distro_release():
    """Returns the release name for the running distro."""
    disname, version, codename = linux_distribution()
    return codename


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


def get_packages(base_url, architecture, pkg_name):
    """Gets the package list from the archive verified."""
    global _packages
    release_url = '%s/%s' % (base_url, 'Release')
    path = 'main/binary-%s/Packages.xz' % architecture
    packages_url = '%s/%s' % (base_url, path)
    if packages_url in _packages:
        return _packages[packages_url]
    release_file = geturl(release_url)
    release_file_gpg = geturl('%s.gpg' % release_url)
    gpg_verify_data(release_file_gpg, release_file)

    # Download the packages file and verify the SHA256SUM
    pkg_data = geturl(packages_url)
    regex_path = re.escape(path)
    sha256sum = re.search(
        ("^\s*?([a-fA-F0-9]{64})\s*[0-9]+\s+%s$" % regex_path).encode('utf-8'),
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


def dpkg_a_newer_than_b(ver_a, ver_b):
    ret = subprocess.call(['dpkg', '--compare-versions', ver_a, 'ge', ver_b])
    return ret == 0


def get_package(
        archive, pkg_name, architecture, release=None, dest=None,
        proposed=False):
    """Look through the archives for package metadata. If a dest is given
    download the package.

    :return: A dictionary containing the packages meta info or if a dest is
             given the path of the downloaded file."""
    global _packages
    release = get_distro_release() if release is None else release
    package = None
    # Find the latest version of the package
    dists = ('%s-updates' % release, '%s-security' % release, release)
    if proposed:
        dists = ('%s-proposed' % release,) + dists
    sys.stderr.write('Searching %s for %s\n' % (', '.join(dists), pkg_name))
    for dist in dists:
        base_url = '%s/dists/%s' % (archive, dist)
        packages = get_packages(base_url, architecture, pkg_name)
        if pkg_name in packages:
            if package is None or dpkg_a_newer_than_b(
                    packages[pkg_name]['Version'], package['Version']):
                package = packages[pkg_name]
                sys.stderr.write('Found %s-%s in %s\n' %
                                 (pkg_name, package['Version'], dist))
    # Download it if it was found and a dest was set
    if package is not None and dest is not None:
        pkg_data = geturl('%s/%s' % (archive, package['Filename']))
        if package['SHA256'] != get_sha256(pkg_data):
            sys.stderr.write(
                'SHA256 mismatch on %s from %s' % (pkg_name, base_url))
            sys.exit(1)
        pkg_path = os.path.join(dest, os.path.basename(package['Filename']))
        with open(pkg_path, 'wb') as stream:
            stream.write(pkg_data)
        package['files'] = []
        output = subprocess.check_output(['dpkg', '-c', pkg_path])
        for line in output.decode('utf-8').split('\n'):
            # The file is the last column in the list.
            file_info = line.split()
            # Last line is just a newline
            if len(file_info) == 0:
                continue
            if file_info[-1].startswith('./'):
                # Remove leading './' if it exists
                f = file_info[-1][2:]
            elif file_info[-1].startswith('/'):
                # Removing leading '/' if it exists
                f = file_info[-1][1:]
            else:
                f = file_info[-1]
            if f != '':
                package['files'].append(f)
    return package


def get_file_info(f):
    size = 0
    sha256 = hashlib.sha256()
    with open(f, 'rb') as f:
        for chunk in iter(lambda: f.read(2**15), b''):
            sha256.update(chunk)
            size += len(chunk)
    return sha256.hexdigest(), size


def make_item(ftype, src_file, dest_file, stream_path, src_packages):
    sha256, size = get_file_info(dest_file)
    for src_package in src_packages:
        if src_file in src_package['files']:
            return {
                'ftype': ftype,
                'sha256': sha256,
                'path': stream_path,
                'size': size,
                'src_package': src_package['src_package'],
                'src_version': src_package['src_version'],
                'src_release': src_package['src_release'],
            }
    raise ValueError("%s not found in src_packages" % src_file)


def archive_files(items, target):
    """Archive multiple files from a src_package into archive.tar.xz."""
    archive_items = {}
    new_items = {}
    # Create a mapping of source packages and the files that came from them.
    for item in items.values():
        key = "%(src_package)s-%(src_release)-%(src_version)" % item
        if archive_items.get(key) is None:
            archive_items[key] = {
                'src_package': item['src_package'],
                'src_release': item['src_release'],
                'src_version': item['src_version'],
                'files': [item['path']],
            }
        else:
            archive_items[key]['files'].append(item['path'])
    for item in archive_items.values():
        stream_path = os.path.join(
            os.path.dirname(item['files'][0]),
            '%s.tar.xz' % item['src_package'])
        full_path = os.path.join(target, stream_path)
        tar = tarfile.open(full_path, 'w:xz')
        for f in item['files']:
            item_full_path = os.path.join(target, f)
            tar.add(item_full_path, os.path.basename(item_full_path))
            os.remove(item_full_path)
        tar.close()
        sha256, size = get_file_info(full_path)
        new_items[item['src_package']] = {
            'ftype': 'archive.tar.xz',
            'sha256': sha256,
            'path': stream_path,
            'size': size,
            'src_package': item['src_package'],
            'src_release': item['src_release'],
            'src_version': item['src_version'],
        }
    return new_items


def extract_files_from_packages(
        archive, packages, architecture, files, release, target, path,
        grub_format=None, grub_config=None, grub_output=None, proposed=False):
    tmp = tempfile.mkdtemp(prefix='maas-images-')
    src_packages = []
    for package in packages:
        package = get_package(
            archive, package, architecture, release, tmp, proposed=proposed)
        pkg_path = os.path.join(tmp, os.path.basename(package['Filename']))
        if pkg_path is None:
            sys.stderr.write('%s not found in archives!' % package)
            sys.exit(1)
        subprocess.check_output(['dpkg', '-x', pkg_path, tmp])
        new_source_package = True
        for src_package in src_packages:
            if src_package['src_package'] == package['Source']:
                new_source_package = False
                src_package['files'] += package['files']
        if new_source_package:
            # Some source packages include the package version in the source
            # name. Only take the name, not the version.
            src_package = package['Source'].split(' ')[0]
            src_packages.append({
                'src_package': src_package,
                'src_version': package['Version'],
                'src_release': release,
                'files': package['files'],
            })
    dest = os.path.join(target, path)
    items = {}
    if grub_format is None:
        for i in files:
            if '*' in i or '?' in i:
                # Copy all files using a wild card
                src = "%s/%s" % (tmp, i)
                unglobbed_files = glob.glob(src)
                for f in unglobbed_files:
                    basename = os.path.basename(f)
                    dest_file = "%s/%s" % (dest, basename)
                    stream_path = "%s/%s" % (path, basename)
                    shutil.copyfile(f, dest_file)
                    pkg_file = f[len(tmp):]
                    while pkg_file.startswith('/'):
                        pkg_file = pkg_file[1:]
                    items[basename] = make_item(
                        'bootloader', pkg_file, dest_file, stream_path,
                        src_packages)
            elif ',' in i:
                # Copy the a file from the package using a new name
                src_file, dest_file = i.split(',')
                dest_file = dest_file.strip()
                full_src_file_path = "%s/%s" % (tmp, src_file.strip())
                stream_path = "%s/%s" % (path, dest_file)
                full_dest_file_path = "%s/%s" % (dest, dest_file)
                shutil.copyfile(full_src_file_path, full_dest_file_path)
                items[dest_file] = make_item(
                    'bootloader', src_file, full_dest_file_path, stream_path,
                    src_packages)
            else:
                # Straight copy
                basename = os.path.basename(i)
                src_file = "%s/%s" % (tmp,  i)
                dest_file = "%s/%s" % (dest, basename)
                stream_path = "%s/%s" % (path, basename)
                shutil.copyfile(src_file, dest_file)
                items[basename] = make_item(
                    'bootloader', i, dest_file, stream_path, src_packages)
    else:
        dest = os.path.join(dest, grub_output)
        # You can only tell grub to use modules from one directory
        modules_path = "%s/%s" % (tmp, files[0])
        modules = []
        for module_path in glob.glob("%s/*.mod" % modules_path):
            module_filename = os.path.basename(module_path)
            module_name, _ = os.path.splitext(module_filename)
            modules.append(module_name)
        if grub_config is not None:
            grub_config_path = os.path.join(tmp, 'grub.cfg')
            with open(grub_config_path, 'w') as f:
                f.writelines(grub_config)
            subprocess.check_output(
                ['grub-mkimage',
                 '-o', dest,
                 '-O', grub_format,
                 '-d', modules_path,
                 '-c', grub_config_path,
                 ] + modules)
        else:
            subprocess.check_output(
                ['grub-mkimage',
                 '-o', dest,
                 '-O', grub_format,
                 '-d', modules_path,
                 ] + modules)
        basename = os.path.basename(dest)
        stream_path = "%s/%s" % (path, basename)
        items[basename] = make_item(
            'bootloader', files[0], dest, stream_path, src_packages)
    shutil.rmtree(tmp)
    return archive_files(items, target)
