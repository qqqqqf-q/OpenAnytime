import os
import unittest
from unittest import mock

from openanytime.config import ConfigurationError, load_runtime_config


class ConfigurationTests(unittest.TestCase):
    def test_key_and_device_name_are_required(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError):
                load_runtime_config()

            with self.assertRaises(ConfigurationError):
                load_runtime_config(key=121)

    def test_non_finite_scan_values_are_rejected(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError):
                load_runtime_config(
                    key=121,
                    device_name_fragment="Anytime-test",
                    scan_timeout=float("nan"),
                )
            with self.assertRaises(ConfigurationError):
                load_runtime_config(
                    key=121,
                    device_name_fragment="Anytime-test",
                    scan_interval=float("inf"),
                )


if __name__ == "__main__":
    unittest.main()
