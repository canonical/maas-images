# Build a custom image stream and add it to MAAS 

In this tutorial we are going through the steps of creating a new images stream and connect it to a MAAS instance.

In particular, we will create a stream with Ubuntu Noble 24.04 for the amd64 architecture and connnect it to a MAAS 3.7 instance

## Prerequisite

- Ubuntu 24.04 running on a machine or virtual machine.
- At least 8GB of free space on disk

## Step by step tutorial

### Stream setup

Clone the repository and `cd` into it:

```bash
git clone https://git.launchpad.net/maas-images && cd maas-images
```

Do the system setup:

```bash
./system-setup
```

Build the stream with the images/architectures you need. Here we are omitting the `--arch` parameter as it defaults to amd64. Note that this step will take around 10-20 minutes.

```bash
./tools/build-stream noble
```

Once it finishes, you can see the artifacts built inside the `build/images` directory. They should look similar to this:

```bash
tree -L 3 build/images
build/images
├── bootloaders
│   ├── bootloaders
│   │   ├── open-firmware
│   │   ├── pxe
│   │   └── uefi
│   ├── open-firmware
│   │   └── ppc64el
│   ├── pxe
│   │   └── amd64
│   ├── streams
│   │   └── v1
│   └── uefi
│       ├── amd64
│       └── arm64
├── gpg.key
├── noble
│   └── amd64
│       └── 20260217
└── streams
    └── v1
        ├── com.ubuntu.maas:candidate:1:bootloader-download.json
        ├── com.ubuntu.maas:candidate:1:bootloader-download.json.gpg
        ├── com.ubuntu.maas:candidate:1:bootloader-download.sjson
        ├── com.ubuntu.maas:candidate:v3:download.json
        ├── com.ubuntu.maas:candidate:v3:download.json.gpg
        ├── com.ubuntu.maas:candidate:v3:download.sjson
        ├── index.json
        ├── index.json.gpg
        └── index.sjson
```

Where:
- `bootloaders`: contains all the bootloaders. These will always be downloaded
- `gpg.key` is the key used to sign the stream
- `streams` is the [simplestreams](https://canonical-simplestreams.readthedocs-hosted.com/en/latest/) index and product files
- `noble`: contains the files related to Ubuntu noble. There will be one folder for each series specified in the command

Next, we are going to install `nginx` as we will use it to expose the stream:

```bash
sudo apt-get install -y nginx
```

Put the generated stream inside `/var/www/html/images` (by default the output is inside `build/images`, adjust this command if you provided a different `--images-dir`):

```bash
sudo rm -rf /var/www/html/images
sudo mv build/images /var/www/html/
```

Modify the `nginx` configuration to expose the images on port 8080:

```bash
sudo tee /etc/nginx/sites-available/default <<EOF
server {
    listen 8080;
    location / {
        root /var/www/html/images;
        autoindex on;
        try_files \$uri \$uri/ =404;
    }
}
EOF
```

Restart `nginx`:

```bash
sudo systemctl restart nginx
```

Verify that `nginx` is setup correctly:
```bash
curl http://localhost:8080/streams/v1/index.json
```

The output should look similar to this
```json
{
 "format": "index:1.0",
 "index": {
  "com.ubuntu.maas:candidate:1:bootloader-download": {
   "datatype": "image-downloads",
   "format": "products:1.0",
   "path": "streams/v1/com.ubuntu.maas:candidate:1:bootloader-download.json",
   "products": [
    "com.ubuntu.maas.candidate:1:grub-efi-signed:uefi:amd64",
    "com.ubuntu.maas.candidate:1:grub-efi:uefi:arm64",
    "com.ubuntu.maas.candidate:1:grub-ieee1275:open-firmware:ppc64el",
    "com.ubuntu.maas.candidate:1:pxelinux:pxe:amd64"
   ],
   "updated": "Fri, 20 Feb 2026 09:42:57 +0000"
  },
  "com.ubuntu.maas:candidate:v3:download": {
   "datatype": "image-downloads",
   "format": "products:1.0",
   "path": "streams/v1/com.ubuntu.maas:candidate:v3:download.json",
   "products": [
    "com.ubuntu.maas.candidate:v3:boot:24.04:amd64:ga-24.04",
    "com.ubuntu.maas.candidate:v3:boot:24.04:amd64:ga-24.04-lowlatency",
    "com.ubuntu.maas.candidate:v3:boot:24.04:amd64:hwe-24.04",
    "com.ubuntu.maas.candidate:v3:boot:24.04:amd64:hwe-24.04-edge",
    "com.ubuntu.maas.candidate:v3:boot:24.04:amd64:hwe-24.04-lowlatency",
    "com.ubuntu.maas.candidate:v3:boot:24.04:amd64:hwe-24.04-lowlatency-edge"
   ],
   "updated": "Fri, 20 Feb 2026 09:42:57 +0000"
  }
 },
 "updated": "Fri, 20 Feb 2026 09:43:20 +0000"
}
```

### MAAS Setup

Install MAAS:

```bash
sudo snap install maas --channel=3.7
```

For this tutorial we will use the `maas-test-db` snap as our MAAS database:
```bash
sudo snap install maas-test-db --channel=3.7
```


Initialize MAAS (use the default MAAS url when prompted):

```bash
sudo maas init region+rack --database-uri=maas-test-db:///
```

Create an admin in MAAS:

```bash
sudo maas createadmin --username admin --password admin --email maas@example.com
maas login admin http://localhost:5240/MAAS/api/2.0 $(sudo maas apikey --username admin)
```

Update the boot source url to point to the stream created before:

```bash
# Copy the key to a location reachable by the MAAS snap
sudo cp /var/www/html/images/gpg.key /var/snap/maas/common/gpg.key
# Get the list of all the boot-sources (in a fresh setup, only one should be present)
maas admin boot-sources read
# Update the boot-source with the new url and gpg key
maas admin boot-source update 1 url=http://localhost:8080/ keyring_filename=/var/snap/maas/common/gpg.key
maas admin boot-resources import
```

Check if the import process is still running and wait until it finishes:

```bash
maas admin boot-resources is-importing
```

Now if you go to the images page in the MAAS web UI (`http://:localhost:5240/MAAS/r/images`), you should see `Images synced from http://localhost:8080/` and in the upstream images list, you should only find Ubuntu 24.04 for the amd64 architecture.
