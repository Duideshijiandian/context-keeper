import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "scripts"))
import context_cli
from sanitizer import json_bytes


class ProjectCase(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory(prefix="context-keeper-test-")
        self.addCleanup(workspace.cleanup)
        self.workspace = Path(workspace.name)
        self.project = self.workspace / "测试 project"
        self.project.mkdir()

    def cli(self, command, *arguments):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = context_cli.main([command, "--project", str(self.project), *arguments])
        return code, json.loads(stdout.getvalue())

    def ok(self, command, *arguments):
        code, output = self.cli(command, *arguments)
        self.assertEqual(code, 0, output)
        return output

    def payload(self, data, name="payload.json"):
        path = self.workspace / name
        path.write_bytes(json_bytes(data))
        return path

    def capture(self, entries=(), expected=None, **handoff):
        data = {
            "expected_capture_id": expected,
            "records": list(entries),
            "handoff": {
                "scope": {"sources": ["visible_session"], "limits": "Isolated fixture only."},
                "summary": None, "next_steps": [], **handoff,
            },
        }
        return self.cli("capture", "--input", str(self.payload(data)))

    def git(self, *args):
        return subprocess.run(
            [shutil.which("git"), "-C", str(self.project), "-c", "user.name=Test",
             "-c", "user.email=test@example.invalid", "-c", "core.hooksPath=" + __import__("os").devnull,
             *args], check=True, capture_output=True, timeout=15,
        )

    def repo(self):
        if not shutil.which("git"):
            self.skipTest("Git executable unavailable")
        self.git("init", "-q", "--initial-branch=main")
        (self.project / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.git("add", "code.py")
        self.git("commit", "-qm", "fixture")
        return self.ok("init")["manifest"]


def record(id="first", **fields):
    return {
        "id": id, "type": "state", "title": "First claim", "content": "A bounded statement.",
        "status": "UNVERIFIED", "evidence": [], **fields,
    }
