# Commands

There are several commands available for managing MAAS Images in the `bin` directory.


## `img2squashfs`
```{literalinclude} ../../bin/img2squashfs
:language: text
:start-at: Usage: img2squashfs
:end-before: EOF
```


## `meph2-build`
```{argparse}
:module: meph2.commands.build_image
:func: create_parser
:prog: bin/meph2-build
```


## `meph2-cloudimg-sync`
```{argparse}
:module: meph2.commands.cloudimg_sync
:func: create_parser
:prog: bin/meph2-cloudimg-sync
```


## `kpack-from-image`
```{literalinclude} ../../bin/kpack-from-image
:language: text
:start-at: Usage: kpack-from-image
:end-before: EOF
```
