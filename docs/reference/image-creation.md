# Image creation

This document provides a detailed description of how an Ubuntu cloud image is 
converted into a MAAS ephemeral image, including the extraction of binary assets 
such as kernels and initramfs for various flavors (GA, HWE, low-latency).

It gives you a picture on what steps are involved and what commands are being
executed.

For an overview on the overall process, you can refer to the [image
creation overview](../explanation/image-creation-overview.md) document.


## Starting point

There are two ways of starting the image creation process:

1. `bin/meph2-build`
2. `bin/meph2-cloudimg-sync`

The difference between the two is that `bin/meph2-build` builds simplestreams
products for a single image that you give as an argument, while
`bin/meph2-cloudimg-sync` can build simplestreams products for multiple images.
`bin/meph2-cloudimg-sync` takes an existing stream as an argument and
automatically fetches the latest images from the Ubuntu cloud image repository.

## `meph2.stream.create_version`

Both `meph2-build` and `meph2-cloudimg-sync` call `meph2.stream.create_version`.
This function builds simplestreams products for a single image.

```{mermaid}
sequenceDiagram
    autonumber
    participant cloudimg as Ubuntu cloud image
    participant diskimg as Disk image
    participant ephimg as Ephemeral image
    participant Kernels@{ "type" : "collections" }
    participant stream as Simplestreams products

    cloudimg->>diskimg: maas-cloudimg2eph2
    diskimg->>ephimg: maas-cloudimg2ephemeral
    ephimg->>Kernels: kpack-from-image
    par GA
      Kernels->>Kernels:
    and  HWE
      Kernels->>Kernels:
    end
    par kernel and initramfs
      Kernels->>stream: meph2.stream.create_version
    and image and manifest
      cloudimg->>stream: meph2.stream.create_version
    end
```


### Step 1: maas-cloudimg2eph2

The `maas-cloudimg2eph2` script converts the squashfs cloud image to an ext4
filesystem image.

### Step 2: maas-cloudimg2ephemeral

If any PPAs or packages from proposed are specified, the
`maas-cloudimg2ephemeral` script mounts the ext4 disk image in a chroot 
environment and upgrades the specified packages.

If no PPAs or proposed packages are specified, this step still mounts the disk
image but performs package modifications.

### Step 3-5: kpack-from-image

For each kernel variant specified (for example: GA, HWE, low-latency),
`kpack-from-image` is called. This script:

- Mounts the ext4 disk image read-only using overlayfs
- Creates a chroot environment
- Installs the required kernel package based on the specified kernel flavor
- Installs initramfs packages
- Calls `update-initramfs` to generate the initramfs
- Extracts the installed kernel and initramfs binaries

### Steps 6-7: Creating simplestreams products

Finally, `meph2.stream.create_version` takes the cloud image, kernels, and
initramfs files and creates simplestreams products. This includes placing the
binary files in the appropriate directory structure and creating JSON metadata
files referencing them.

Note: If a PPA or proposed packages were specified, a new cloud image squashfs
is created from the modified ext4 disk image. Otherwise, the original cloud 
image squashfs is used unmodified.
