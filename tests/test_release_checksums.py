import unittest

from support import SOURCE
from sanitizer import bounded_read, digest
from sync_deploy import FILES
import release_checksums


class ReleaseChecksumTests(unittest.TestCase):
    def test_checksums_cover_every_runtime_file(self):
        hashes = release_checksums.checksums(SOURCE)
        self.assertEqual(set(hashes), set(FILES))
        for name in FILES:
            self.assertEqual(hashes[name], digest(bounded_read(SOURCE / name)), name)

    def test_report_carries_every_hash_and_the_rollback_steps(self):
        hashes = release_checksums.checksums(SOURCE)
        text = release_checksums.render("1.1.3", "1.1.2", "abc1234", hashes,
                                        "2026-11-17T00:00:00+00:00")
        self.assertIn("Context Keeper v1.1.3 runtime checksums", text)
        self.assertIn("commit: abc1234", text)
        self.assertIn("files: 13", text)
        for name in FILES:
            self.assertIn(f"{hashes[name]}  {name}", text)
        self.assertIn("sha256sum -c SHA256SUMS-v1.1.3.txt", text)
        self.assertIn("checkout v1.1.2", text)
        self.assertIn("--skills-dir", text)
