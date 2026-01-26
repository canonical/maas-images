# Copy a product version in a stream

In the case that a bad image is published it may be quicker to copy a working version to a new version than wait for a fixed version to appear at cloud-images.ubuntu.com. This process is safer than just removing a broken version as it mimics a new version being added to the stream. This guarantees all clients, not just MAAS, will get the fixed image.

## Before you begin

It is also suggested to create a backup of the stream metadata in `maas-v3-images/streams/v1` so you can use diff to verify only versions you wish to remove were removed.

## Basic usage

```bash
meph2-util copy-version data_d from_version to_version
```

**Parameters:**
- `data_d` - The path to the directory containing the stream you wish to modify.
- `from_version` - The version you wish to copy from
- `to_version` - The version you wish to copy to

**Example:** Copy 20191004 to 20191022 on all products

```bash
meph2-util copy-version /path/to/stream 20191004 20191022
```

## Filters

You may also add filters to the copy-version command. Only products matching those filters will have the specified version copied. Filters may be any field described in the product.

**Example:** Copy the version 20191004 to 20191022 on all AMD64 Bionic and Xenial products.

```bash
meph2-util copy-version /path/to/stream 20191004 20191022 \
    'release~(bionic|xenial)' arch=amd64
```

## Optional arguments

- `-n, --dry-run` - Only show what will be copied, do not modify the stream.
- `-u, --no-sign` - Do not sign the stream when done. A stream can be signed later with the meph2-util sign command.
- `--keyring` - Specify the keyring to use when verifying the stream. Defaults to `/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg`
