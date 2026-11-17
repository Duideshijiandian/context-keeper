from pathlib import Path
from unittest import mock

from support import ProjectCase, record
from sanitizer import digest
import storage


class StorageTests(ProjectCase):
    def test_initialization_and_duplicate_in_unicode_directory(self):
        original = self.ok("init")["manifest"]
        self.assertFalse(original["git_available"])
        self.assertIn("+00:00", original["created_at"])
        code, result = self.cli("init")
        self.assertEqual((code, result["error"]["code"]), (5, "ALREADY_INITIALIZED"))
        self.assertEqual(self.ok("status")["manifest"], original)
        moved = self.project.with_name("项目重命名")
        self.project.rename(moved)
        self.project = moved
        self.assertEqual(self.ok("status")["manifest"]["project_id"], original["project_id"])

    def test_capture_deduplicates_retries_and_reuses_objects(self):
        self.ok("init")
        code, saved = self.capture([record()])
        self.assertEqual(code, 0, saved)
        capture_id = saved["manifest"]["last_capture_id"]
        code, same = self.capture([record()])
        self.assertEqual(code, 0, same)
        self.assertFalse(same["changed"])
        self.assertEqual(same["manifest"]["last_capture_id"], capture_id)
        code, changed = self.capture([record()], expected=capture_id, next_steps=["Review code."])
        self.assertEqual(code, 0, changed)
        self.assertTrue(changed["changed"])
        self.assertEqual(len(list((self.project / storage.STORE / "records").glob("*.json"))), 1)
        self.assertEqual(self.ok("validate")["history_checked"], 2)

    def test_compare_and_swap_prevents_lost_updates(self):
        self.ok("init")
        _, saved = self.capture([record()])
        code, stale = self.capture([record("other", title="Another")])
        self.assertEqual((code, stale["error"]["code"]), (5, "CAPTURE_CONFLICT"))
        code, unsafe = self.capture([record(content="Different")], saved["manifest"]["last_capture_id"])
        self.assertEqual((code, unsafe["error"]["code"]), (5, "RECORD_CONFLICT"))

    def test_concurrent_writer_lock_leaves_state_unchanged(self):
        original = self.ok("init")["manifest"]
        with storage.writer_lock(self.project / storage.STORE):
            code, result = self.capture([record()])
            self.assertEqual((code, result["error"]["code"]), (5, "WRITE_BUSY"))
        self.assertEqual(self.ok("status")["manifest"], original)
        self.assertEqual(self.capture([record()])[0], 0)

    def test_failed_publication_leaves_previous_snapshot_readable(self):
        original = self.ok("init")["manifest"]
        replace = storage.os.replace

        def fail_manifest(source, dest):
            if Path(dest).name == "manifest.json":
                raise PermissionError("simulated Windows file sharing lock")
            return replace(source, dest)

        with mock.patch("storage.os.replace", side_effect=fail_manifest):
            code, result = self.capture([record()])
        self.assertEqual((code, result["error"]["code"]), (4, "FILE_ACCESS"))
        self.assertEqual(self.ok("status")["manifest"], original)
        self.assertFalse(list((self.project / storage.STORE).glob(".pending-*")))
        self.assertEqual(self.ok("validate")["history_checked"], 0)
        self.assertEqual(self.capture([record()])[0], 0)

    def test_interrupted_publication_ignores_unreachable_objects(self):
        original = self.ok("init")["manifest"]
        store = self.project / storage.STORE
        stray = store / "snapshots" / "interrupted.tmp"
        stray.write_text("partial", encoding="utf-8")
        self.assertEqual(self.ok("status")["manifest"], original)
        self.assertEqual(self.ok("validate")["history_checked"], 0)

    def test_missing_manifest_and_corrupt_json(self):
        self.assertEqual(self.cli("restore")[0], 4)
        self.ok("init")
        path = self.project / storage.STORE / "manifest.json"
        path.write_bytes(b"{broken")
        code, result = self.cli("validate")
        self.assertEqual((code, result["error"]["code"]), (4, "INVALID_JSON"))
        self.assertEqual(path.read_bytes(), b"{broken")

    def test_missing_empty_records_directory_is_detected(self):
        self.ok("init")
        (self.project / storage.STORE / "records").rmdir()
        code, result = self.cli("validate")
        self.assertEqual((code, result["error"]["code"]), (4, "MISSING_FILE"))

    def test_hash_mismatch_fails_and_leaves_tampered_bytes_for_review(self):
        self.ok("init")
        self.assertEqual(self.capture([record()])[0], 0)
        path = next((self.project / storage.STORE / "records").glob("*.json"))
        data = path.read_bytes() + b" "
        path.write_bytes(data)
        code, result = self.cli("validate")
        self.assertEqual((code, result["error"]["code"]), (4, "CORRUPT_STORE"))
        self.assertEqual(path.read_bytes(), data)

    def test_verified_completion_requires_successful_current_test(self):
        self.ok("init")
        claim = record(type="task", task_status="DONE", status="VERIFIED",
                       evidence=[{"kind": "user", "locator": "User reported success."}])
        self.assertEqual(self.capture([claim])[1]["error"]["code"], "INVALID_EVIDENCE")
        log = self.project / "check.log"
        log.write_bytes(b"check passed\n")
        claim["evidence"] = [{"kind": "test", "path": "check.log",
                              "sha256": digest(log.read_bytes()), "line": 1,
                              "locator": "Observed a passing targeted check.", "exit_code": 0,
                              "observed_at": "2026-01-01T00:00:00+00:00"}]
        self.assertEqual(self.capture([claim])[0], 0)
        self.assertEqual(self.ok("restore")["sections"]["verified_progress"][0]["id"], "first")
        log.write_bytes(b"check failed\n")
        restored = self.ok("restore", "--record", "first")
        self.assertEqual(restored["effective_status"], "STALE")

    def test_exact_duplicate_and_repeated_id_are_rejected(self):
        self.ok("init")
        self.assertEqual(self.capture([record(), record()])[1]["error"]["code"], "DUPLICATE_ID")
        self.assertEqual(self.capture([record(), record("other")])[1]["error"]["code"], "DUPLICATE_RECORD")
