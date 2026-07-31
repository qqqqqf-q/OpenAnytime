import tempfile
import unittest
from pathlib import Path
from unittest import mock

import server


class ServerBoundaryTests(unittest.TestCase):
    def test_port_is_bounded(self):
        self.assertEqual(server.parse_port("8520"), 8520)
        for value in ("0", "65536", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.parse_port(value)

    def test_limit_is_bounded(self):
        self.assertEqual(server.parse_limit(None), 500)
        self.assertEqual(server.parse_limit("1"), 1)
        self.assertEqual(server.parse_limit("2000"), 2000)
        for value in ("0", "-1", "2001", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                server.parse_limit(value)

    def test_since_must_be_iso_timestamp(self):
        self.assertEqual(
            server.validate_since("2026-07-31T12:00:00+08:00"),
            "2026-07-31T12:00:00+08:00",
        )
        with self.assertRaises(ValueError):
            server.validate_since("not-a-time")

    def test_static_path_cannot_escape_web_root(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(server, "WEB_ROOT", Path(directory)):
                with self.assertRaises(ValueError):
                    server.resolve_static_path("/../private.txt")


if __name__ == "__main__":
    unittest.main()
