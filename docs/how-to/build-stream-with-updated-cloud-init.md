# Build a stream with updated `cloud-init` package

## Goal

Create a MAAS image stream with a newer version of the `cloud-init` package from Ubuntu's proposed pocket, useful when you need cloud-init features or fixes not yet available in the stable release.

## How-to

When building a stream with `tools/build-stream`, you can specify packages to download from the `-proposed` pocket using the `--proposed-packages` flag. This allows you to include newer versions of packages that haven't yet been promoted to the stable release.

Run the following command to build an Ubuntu Noble stream with an updated `cloud-init` package. Note that this step will take around 10-20 minutes.

```bash
./tools/build-stream noble --proposed-packages cloud-init
```

This command creates an image stream for Ubuntu Noble that includes the `cloud-init` package from the `noble-proposed` pocket instead of the stable one.

### Verify the package version

To confirm that the newer `cloud-init` package was included in your stream, check the package version in the squashfs manifest:

```bash
cat build/images/noble/amd64/20260217/squashfs.manifest | grep cloud-init
```

You should see output similar to:

```
cloud-init      25.3-0ubuntu1~24.04.1
```

To see the difference between the proposed and stable versions, you can build a stream without the `--proposed-packages` flag and compare:

```bash
./tools/build-stream noble
cat build/images/noble/amd64/YYYYMMDD/squashfs.manifest | grep cloud-init
```

The stable version would show:

```
cloud-init      25.2-0ubuntu1~24.04.1
```

Notice the version difference: `25.3` (proposed) vs `25.2` (stable).

**Note:** The version numbers in this example are from when this guide was written. Your actual versions will differ based on what's currently available in the stable and proposed pockets at the time you run the command.
