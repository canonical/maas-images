import os

DEF_MEPH2_CONFIG = os.environ.get(
    "MEPH2_CONFIG_YAML",
    os.path.abspath(os.path.join(os.path.dirname(__file__),
                                 "..", "conf", "meph-v2.yaml")))

# vi: ts=4 expandtab syntax=python
