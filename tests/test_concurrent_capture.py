"""Concurrency stress: real capture processes against one project store.

The acceptance question is not "do both writers commit?" but "when two writers
race, is the loser told, and is the store left exactly as one of them wrote it?".

A racing capture has two safe outcomes:
  * WRITE_BUSY       -- the other process held ``.context-keeper/write.lock``.
  * CAPTURE_CONFLICT -- the loser read the manifest after the winner published,
                        so its ``expected_capture_id`` is stale.
Both are exit code 5. A lost update, a half-written snapshot, or a record that
only exists in the loser's payload would be a failure.
"""

import json
import subprocess
import sys

from support import ProjectCase, SOURCE, record

from sanitizer import digest
from storage import history, open_store

ROUNDS = 10
SAFE_FAILURES = {"WRITE_BUSY", "CAPTURE_CONFLICT"}
WORKER_NAMES = "abcdefgh"
DEFAULT = object()


def race_task(round_name, letter):
    ident = f"race-{round_name}-{letter}"
    return {
        "id": ident,
        "type": "task",
        "title": f"Race {round_name} worker {letter}",
        "content": f"Worker {letter} of round {round_name} claims the store.",
        "status": "REPORTED",
        "task_status": "IN_PROGRESS",
        "evidence": [{"kind": "user", "locator": "Racing writer fixture."}],
        "next_steps": [f"Round {round_name} worker {letter} next step."],
    }


class ConcurrentCaptureTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.ok("init")

    # -- fixtures -----------------------------------------------------------

    def payload_for(self, expected, entry, name, omit=False):
        data = {
            "records": [entry],
            "handoff": {
                "scope": {"sources": ["visible_session"], "limits": "Concurrency fixture."},
                "summary": None,
                "next_steps": [],
            },
        }
        if not omit:
            data["expected_capture_id"] = expected
        return self.payload(data, name=name)

    def start(self, payload):
        return subprocess.Popen(
            [sys.executable, "-X", "utf8", "-B",
             str(SOURCE / "scripts" / "context_cli.py"), "capture",
             "--project", str(self.project), "--input", str(payload)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )

    def race(self, round_name, expected=DEFAULT, workers=2, omit=False):
        """Launch `workers` real capture processes, then collect their results."""
        if expected is DEFAULT:
            raise AssertionError("pass an explicit expected_capture_id")
        started = []
        for index in range(workers):
            letter = WORKER_NAMES[index]
            name = f"{round_name}-{letter}.json"
            path = self.payload_for(expected, race_task(round_name, letter), name, omit=omit)
            started.append((letter, self.start(path)))
        results = {}
        for letter, handle in started:
            stdout, stderr = handle.communicate(timeout=120)
            try:
                output = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else None
            finally:
                if stderr.strip() and not stdout.strip():
                    self.fail(f"worker {letter} crashed without JSON: {stderr.strip()}")
            self.assertIsNotNone(output, f"worker {letter} printed no JSON: {stdout!r}")
            results[letter] = (handle.returncode, output)
        return results

    # -- assertions ---------------------------------------------------------

    def one_commit(self, results):
        """Assert exactly one writer committed and every loser failed safely."""
        winners = [letter for letter, (_, output) in results.items() if output.get("ok")]
        self.assertEqual(len(winners), 1, f"expected one commit, got {results}")
        winner = winners[0]
        code, output = results[winner]
        self.assertEqual(code, 0, output)
        self.assertTrue(output["changed"], output)
        for letter, (code, output) in results.items():
            if letter == winner:
                continue
            self.assertFalse(output.get("ok"), f"worker {letter} must not commit: {output}")
            self.assertEqual(code, 5, f"worker {letter} must fail with exit 5: {output}")
            self.assertIn(output["error"]["code"], SAFE_FAILURES, output)
        return winner

    def store(self):
        return open_store(self.project)

    def chain_length(self):
        _, manifest, snapshot, _ = self.store()
        return sum(1 for _ in history(self.store()[0], manifest, snapshot, verify_records=True))

    def assert_store_is_intact(self):
        store = self.project / ".context-keeper"
        for folder in ("snapshots", "records"):
            for path in sorted((store / folder).glob("*.json")):
                self.assertEqual(digest(path.read_bytes()), path.stem, f"corrupt {path.name}")
        self.assertEqual(list(store.rglob(".pending-*")), [], "a staged file survived")
        self.assertEqual(list(self.project.glob(".context-keeper-init-*")), [],
                         "an initialization staging directory survived")

    def validate(self):
        code, report = self.cli("validate")
        self.assertEqual(code, 0, report)
        self.assertEqual(report["level"], "PASS", report)
        return report

    # -- tests --------------------------------------------------------------

    def test_racing_writers_never_both_commit(self):
        results = self.race("single", None)
        winner = self.one_commit(results)
        _, manifest, snapshot, records = self.store()
        self.assertEqual(manifest["last_capture_id"], snapshot["capture_id"])
        self.assertIn(f"race-single-{winner}", records)
        loser = next(letter for letter in results if letter != winner)
        self.assertNotIn(f"race-single-{loser}", records, "the loser's record entered the store")

        missing_code, missing = self.cli("restore", "--task", f"race-single-{loser}")
        self.assertEqual((missing_code, missing["error"]["code"]), (4, "TASK_NOT_FOUND"))
        restored = self.ok("restore", "--task", f"race-single-{winner}")
        self.assertEqual(restored["next_steps"], [f"Round single worker {winner} next step."])
        self.validate()
        self.assert_store_is_intact()

    def test_stale_expected_capture_id_is_capture_conflict(self):
        first = self.capture([record("first-claim", title="First claim", content="First statement.")])
        self.assertEqual(first[0], 0, first[1])
        stale_id = "00000000-0000-4000-8000-000000000000"
        code, output = self.capture(
            [record("second-claim", title="Second claim", content="Second statement.")], stale_id
        )
        self.assertEqual((code, output["error"]["code"]), (5, "CAPTURE_CONFLICT"))
        _, manifest, _, records = self.store()
        self.assertNotIn("second-claim", records)
        self.assertEqual(manifest["last_capture_id"], first[1]["manifest"]["last_capture_id"])
        self.assert_store_is_intact()

    def test_ten_racing_rounds_keep_the_store_consistent(self):
        expected = None
        committed = []
        for index in range(ROUNDS):
            label = f"r{index:02d}"
            results = self.race(label, expected)
            winner = self.one_commit(results)
            committed.append(f"race-{label}-{winner}")
            _, manifest, snapshot, records = self.store()
            self.assertEqual(manifest["last_capture_id"], snapshot["capture_id"])
            self.assertEqual(len(records), index + 1, f"round {label} lost a record")
            self.assertEqual(self.chain_length(), index + 1, f"round {label} chain length")
            expected = manifest["last_capture_id"]
        self.validate()
        self.assertEqual(len(list((self.project / ".context-keeper" / "snapshots").glob("*.json"))),
                         ROUNDS + 1, "exactly one snapshot per round may be published")
        for ident in committed:
            self.assertEqual(self.ok("restore", "--task", ident)["task"]["id"], ident)
        self.assert_store_is_intact()

    def test_more_writers_still_yield_one_commit(self):
        results = self.race("four", None, workers=4)
        winner = self.one_commit(results)
        _, _, _, records = self.store()
        self.assertEqual(set(records), {f"race-four-{winner}"})
        self.validate()
        self.assert_store_is_intact()

    def test_missing_expected_capture_id_fails_both_without_side_effects(self):
        before = self.store()
        snapshot_files = {path.name for path in
                          (self.project / ".context-keeper" / "snapshots").glob("*.json")}
        results = self.race("omit", None, omit=True)
        for letter, (code, output) in results.items():
            self.assertEqual(code, 4, f"worker {letter}: {output}")
            self.assertEqual(output["error"]["code"], "INVALID_SCHEMA", output)
        after = self.store()
        self.assertEqual(after[1]["last_capture_id"], before[1]["last_capture_id"])
        self.assertEqual(after[2]["capture_id"], before[2]["capture_id"])
        self.assertEqual({path.name for path in
                          (self.project / ".context-keeper" / "snapshots").glob("*.json")},
                         snapshot_files)
        self.assertEqual(after[3], before[3])
        self.assert_store_is_intact()
