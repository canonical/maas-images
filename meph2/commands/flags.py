DEF_KEYRING = "/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg"

LABELS = ('alpha1', 'alpha2', 'alpha3',
          'beta1', 'beta2', 'beta3',
          'rc', 'release')

COMMON_ARGS = []
COMMON_FLAGS = {
    'dry-run': (('-n', '--dry-run'),
                {'help': 'only report what would be done',
                 'action': 'store_true', 'default': False}),
    'no-sign': (('-u', '--no-sign'),
                {'help': 'do not re-sign files',
                 'action': 'store_true', 'default': False}),
    'max': (('--max',),
            {'help': 'keep at most N versions per product',
             'default': 2, 'type': int}),
    'orphan-data': (('orphan_data',), {'help': 'the orphan data file'}),
    'src': (('src',), {'help': 'the source streams directory'}),
    'target': (('target',), {'help': 'the target streams directory'}),
    'data_d': (('data_d',),
               {'help': ('the base data directory'
                         '("path"s are relative to this)')}),
    'keyring': (('--keyring',),
                {'help': 'gpg keyring to check sjson',
                 'default': DEF_KEYRING}),
    'filters': ('filters', {'nargs': '*', 'default': []}),
    'version': ('version', {'help': 'the version_id to promote.'}),
}

SUBCOMMANDS = {
    'insert': {
        'help': 'add new items from one stream into another',
        'opts': [
            COMMON_FLAGS['dry-run'], COMMON_FLAGS['no-sign'],
            COMMON_FLAGS['keyring'],
            COMMON_FLAGS['src'], COMMON_FLAGS['target'],
            COMMON_FLAGS['filters'],
        ]
    },
    'import': {
        'help': 'import an image from the specified config into a stream',
        'opts': [
            COMMON_FLAGS['no-sign'], COMMON_FLAGS['keyring'],
            (('--proposed',),
             {'help': 'Pull bootloaders from proposed', 'action': 'store_true',
              'default': False}),
            ('import_cfg', {'help':
                            'The config file for the image to import.'}),
            COMMON_FLAGS['target'],
            COMMON_FLAGS['max'],
            ]
    },
    'merge': {
        'help': 'merge two product streams together',
        'opts': [
            COMMON_FLAGS['no-sign'],
            COMMON_FLAGS['src'], COMMON_FLAGS['target'],
            ]
    },
    'promote': {
        'help': 'promote a product/version from candidate to stable',
        'opts': [
            COMMON_FLAGS['dry-run'], COMMON_FLAGS['no-sign'],
            COMMON_FLAGS['keyring'],
            (('-l', '--label'),
             {'default': 'release', 'choices': LABELS,
              'help': 'the label to use'}),
            (('--skip-file-copy',),
             {'help': 'do not copy files, only metadata [TEST_ONLY]',
              'action': 'store_true', 'default': False}),
            COMMON_FLAGS['src'], COMMON_FLAGS['target'],
            COMMON_FLAGS['version'], COMMON_FLAGS['filters'],
        ]
    },
    'diff': {
        'help': (
            'Creates a diff, represented by a YAML file, which how two '
            'streams differ. This assumes each stream uses a separate label '
            'consistently.'
        ),
        'opts': [
            (
                ('-o', '--output'),
                {'help': 'Specify file to output to, defaults to STDOUT'}
            ),
            (
                ('--new-versions-only'),
                {
                    'help': (
                        'Only show new versions from the source in diff. '
                        'Do not include old versions from the target.'
                    ),
                    'action': 'store_true',
                    'default': False,
                },
            ),
            (
                ('--latest-only'),
                {
                    'help': 'Only include the latest missing version in diff.',
                    'action': 'store_true',
                    'default': False,
                },
            ),
            (
                ('--promote'),
                {
                    'help': 'Generate diff which promotes missing items.',
                    'action': 'store_true',
                    'default': False,
                },
            ),
            COMMON_FLAGS['src'], COMMON_FLAGS['target'],
        ],
    },
    'patch': {
        'help': (
            'Apply a patch, represented by a YAML file generated with diff, '
            'which describes how a stream should be edited.'
        ),
        'opts': [
            (
                ('-i', '--input'),
                {'help': 'Specify the patch YAML to apply, defaults to STDIN'}
            ), COMMON_FLAGS['dry-run'], COMMON_FLAGS['no-sign'],
            (
                ('streams', ), {
                    'action': 'append',
                    'nargs': '+',
                    'help': (
                        'The stream to apply the patch YAML to. Multiple '
                        'streams must be given to insert new versions'
                    )
                }),
        ],
    },
    'clean-md': {
        'help': 'clean streams metadata only to keep "max" items',
        'opts': [
            COMMON_FLAGS['dry-run'], COMMON_FLAGS['no-sign'],
            COMMON_FLAGS['keyring'],
            ('max', {'type': int}), ('target', {}),
            COMMON_FLAGS['filters'],
        ]
    },
    'find-orphans': {
        'help': 'find files in data_d not referenced in a "path"',
        'opts': [
            COMMON_FLAGS['orphan-data'], COMMON_FLAGS['data_d'],
            COMMON_FLAGS['keyring'],
            ('streams_dirs', {'nargs': '*', 'default': []}),
        ],
    },
    'reap-orphans': {
        'help': 'reap orphans listed in orphan-data from data_d',
        'opts': [
            COMMON_FLAGS['orphan-data'], COMMON_FLAGS['dry-run'],
            COMMON_FLAGS['data_d'],
            ('--older', {'default': '3d',
                         'help': ('only remove files orphaned longer than'
                                  'this. if no unit given, default is days.')
                         }),
            ('--now', {'default': False,
                       'help': 'reap orphans now',
                       'action': 'store_true',
                       }),
        ],
    },
    'sign': {
        'help': 'Regenerate index.json and sign the stream',
        'opts': [
            COMMON_FLAGS['data_d'], COMMON_FLAGS['no-sign'],
        ],
    },
    'remove-version': {
        'help': 'Remove a version from a product',
        'opts': [
            COMMON_FLAGS['dry-run'], COMMON_FLAGS['no-sign'],
            COMMON_FLAGS['keyring'], COMMON_FLAGS['data_d'],
            COMMON_FLAGS['version'], COMMON_FLAGS['filters'],
        ],
    },
    'copy-version': {
        'help': 'Copy a version of a product to a new version',
        'opts': [
            COMMON_FLAGS['dry-run'], COMMON_FLAGS['no-sign'],
            COMMON_FLAGS['keyring'], COMMON_FLAGS['data_d'],
            ('from_version', {'help': 'the version_id to copy from.'}),
            ('to_version', {'help': 'the version_id to copy to.'}),
            COMMON_FLAGS['filters']
        ],
    },
}

# vi: ts=4 expandtab syntax=python
