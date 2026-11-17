import os
from unittest import mock

from support import ProjectCase, record


class GitTests(ProjectCase):
    def test_clean_head_recent_commit_and_branch(self):
        manifest = self.repo()
        self.assertEqual(manifest["git_branch"], "main")
        self.assertEqual(manifest["working_tree_status"], "clean")
        self.assertEqual(self.ok("status")["git"]["recent_commits"][0], manifest["git_head"])

    def test_dirty_fingerprint_changes_when_same_untracked_file_changes(self):
        original = self.repo()
        path = self.project / "未跟踪.txt"
        path.write_text("aa", encoding="utf-8")
        first = self.ok("status")["git"]
        self.assertEqual(first["working_tree_status"], "dirty")
        self.assertIn({"status": "??", "path": "未跟踪.txt"}, first["changes"])
        path.write_text("bb", encoding="utf-8")
        second = self.ok("status")["git"]
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertNotEqual(original["fingerprint"], second["fingerprint"])
        code, result = self.cli("validate")
        self.assertEqual(code, 3)
        self.assertIn("WORKTREE_DIRTY", {item["code"] for item in result["findings"]})

    def test_head_drift_and_stale_record_survive_unrelated_capture(self):
        original = self.repo()
        sha = self.ok("fingerprint", "--path", "code.py")["sha256"]
        claim = record(status="VERIFIED", evidence=[{
            "kind": "file", "locator": "Inspect the constant.", "path": "code.py", "sha256": sha,
        }])
        _, saved = self.capture([claim])
        self.git("commit", "--allow-empty", "-qm", "another commit")
        code, result = self.cli("validate")
        self.assertEqual(code, 3)
        self.assertNotEqual(result["git"]["git_head"], original["git_head"])
        self.assertIn("GIT_HEAD_CHANGED", {item["code"] for item in result["findings"]})
        self.assertEqual(self.capture([], saved["manifest"]["last_capture_id"])[0], 0)
        restored = self.ok("restore", "--record", "first")
        self.assertEqual(restored["effective_status"], "STALE")
        self.assertEqual(restored["record"]["status"], "VERIFIED")

    def test_staged_change_and_unstaged_change_are_reported(self):
        self.repo()
        path = self.project / "code.py"
        path.write_text("VALUE = 2\n", encoding="utf-8")
        self.assertEqual(self.ok("status")["git"]["changes"], [{"status": " M", "path": "code.py"}])
        self.git("add", "code.py")
        self.assertEqual(self.ok("status")["git"]["changes"], [{"status": "M ", "path": "code.py"}])

    def test_excluded_paths_are_not_reported(self):
        self.repo()
        (self.project / ".env").write_text("fixture only", encoding="utf-8")
        private = self.project / "private"
        private.mkdir()
        (private / "foo.pem").write_text("fixture only", encoding="utf-8")
        git = self.ok("status")["git"]
        self.assertEqual(git["working_tree_status"], "clean")
        self.assertEqual(git["changes"], [])

    def test_rejected_external_git_include_is_unknown(self):
        self.repo()
        outside = self.workspace / "external.gitconfig"
        outside.write_text("[core]\n  bare = true\n", encoding="utf-8")
        self.git("config", "include.path", outside.as_posix())
        git = self.ok("status")["git"]
        self.assertEqual(git["working_tree_status"], "unknown")
        self.assertIn("GIT_BOUNDARY", git["warnings"])

    def test_command_failure_is_not_clean(self):
        self.repo()
        with mock.patch("git_inspector.subprocess.run", side_effect=OSError("simulated error")):
            code, result = self.cli("validate")
        self.assertEqual(code, 3)
        self.assertEqual(result["git"]["working_tree_status"], "unknown")

    def test_project_path_cannot_supply_the_git_executable(self):
        self.repo()
        candidate = self.project / ("git.exe" if os.name == "nt" else "git")
        candidate.write_bytes(b"synthetic executable candidate")
        candidate.chmod(0o700)
        with (mock.patch.dict("os.environ", {"PATH": str(self.project)}),
              mock.patch("git_inspector.subprocess.run") as launch):
            git = self.ok("status")["git"]
        launch.assert_not_called()
        self.assertEqual(git["working_tree_status"], "unknown")
        self.assertIn("GIT_UNAVAILABLE", git["warnings"])

    def test_alternate_objects_and_worktree_pointer_are_rejected(self):
        self.repo()
        alternates = self.project / ".git" / "objects" / "info" / "alternates"
        alternates.write_text(str(self.workspace), encoding="utf-8")
        self.assertEqual(self.ok("status")["git"]["working_tree_status"], "unknown")
        alternates.unlink()
        other = self.workspace / "linked"
        self.git("worktree", "add", "--detach", str(other))
        self.project = other
        self.assertEqual(self.ok("status")["git"]["working_tree_status"], "unknown")
