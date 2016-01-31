from unittest import TestCase
import json
import os

from meph2 import netinst


# results contains responses from get_file_item_data
# for all existing data that came into it (some filtered for brevity)
def load_json_data(fname):
    fpath = os.path.join(os.path.dirname(__file__), fname)
    with open(fpath, "r") as fp:
        return json.load(fp)


class TestFilePathData(TestCase):
    gfdata = None

    @classmethod
    def setUpClass(cls):
        cls.gfdata = load_json_data('file_item_data.json')

    def test_file_item_data(self):
        release = "trusty"
        for fpath in sorted(self.gfdata):
            if self.gfdata[fpath]:
                expected = self.gfdata[fpath].copy()
                if expected['kernel-release'] == "BASE":
                    expected['kernel-release'] = release
            else:
                expected = None
            found = netinst.get_file_item_data(fpath, release=release)
            self.assertEqual((fpath, expected), (fpath, found))

    def test_file_item_data_single_path(self):
        paths = ["generic/kernel.ubuntu", "generic/initrd.ubuntu"]
        release = "trusty"
        for fpath in paths:
            if self.gfdata[fpath]:
                expected = self.gfdata[fpath].copy()
                if expected['kernel-release'] == "BASE":
                    expected['kernel-release'] = release
            else:
                expected = None
            found = netinst.get_file_item_data(fpath, release=release)
            self.assertEqual(expected, found)
