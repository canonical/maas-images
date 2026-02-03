# Image creation overview

This document explains the overall process of how an Ubuntu cloud image is
converted into an image that's suitable for MAAS to use. It includes the
extraction of binary assets such as the kernel and initramfs for various flavors
(GA, HWE, low-latency, etc.).

If you want to see exactly which commands and steps are involved in creating an
image using MAAS Images, you can refer to the [image
creation](../reference/image-creation.md) document.

## Overview

When we create an image for MAAS, we typically don't create a new image from
scratch. Instead, we take an existing Ubuntu cloud image and use it as the base
image.

In addition to the image itself, MAAS also needs to have a kernel and initramfs
in order to boot a machine over the network. For each Ubuntu cloud image, we get
a couple of different variants of the kernel and their corresponding initramfs.
For example:

  - GA
  - HWE
  - low-latency

We use simplestreams to create a new product for each image and its variants.

## Creating images for images.maas.io

Creating the images and related assets for images.maas.io is the simplest case.
Here, we take an existing Ubuntu cloud image and don't modify it at all. We
use an overlayfs mount to install kernel and initramfs packages, then extract
the kernel binary and the generated initramfs.

```{mermaid}
sequenceDiagram
    autonumber
    participant cloudimg as Ubuntu cloud image
    participant Kernel/Initramfs@{ "type" : "collections" }
    participant stream as Simplestreams products

    cloudimg->>Kernel/Initramfs: Overlayfs
    par
      Kernel/Initramfs->>Kernel/Initramfs: GA
    and 
      Kernel/Initramfs->>Kernel/Initramfs: HWE
    end
    par binaries and manifests
      Kernel/Initramfs->>stream: kernel and initramfs
    and
      cloudimg->>stream: image
    end
```

## Creating custom images

Sometimes you might want to test MAAS, using newer version of some packages,
than those that are in the official Ubuntu cloud images. For example, you might
want to test how MAAS works with the latest version of cloud-init.

In this case, it works more or less like how we create the images for
images.maas.io, but with an extra step to create a new image, upgrading some of
the packages.

The upgraded packages can come either from a PPA or from `$release-proposed`.

```{mermaid}
sequenceDiagram
    autonumber
    participant cloudimg as Ubuntu cloud image
    box Image customization
      participant upgradedimg as Upgraded cloud image
    end
    participant Kernel/Initramfs@{ "type" : "collections" }
    participant stream as Simplestreams products

    cloudimg->>upgradedimg: Upgrade packages
    upgradedimg->>Kernel/Initramfs: Overlayfs
    par
      Kernel/Initramfs->>Kernel/Initramfs: GA
    and 
      Kernel/Initramfs->>Kernel/Initramfs: HWE
    end
    par binaries and manifests
      Kernel/Initramfs->>stream: kernel and initramfs
    and
      upgradedimg->>stream: image
    end
```
