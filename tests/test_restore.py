from support import ProjectCase, record


class RestoreTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.ok("init", "--input", str(self.payload({"goal": "Recover important fixture facts."})))

    def test_restore_confidence_and_task_status_are_separate(self):
        claim = record(type="task", status="REPORTED", task_status="DONE",
                       evidence=[{"kind": "user", "locator": "User stated completion."}])
        self.assertEqual(self.capture([claim])[0], 0)
        normal = self.ok("restore")
        self.assertEqual(normal["project"]["goal"], "Recover important fixture facts.")
        self.assertEqual(normal["sections"]["verified_progress"], [])
        self.assertEqual(normal["sections"]["current_state"][0]["effective_status"], "REPORTED")
        self.assertEqual(normal["sections"]["current_state"][0]["task_status"], "DONE")
        code, result = self.cli("validate")
        self.assertEqual(code, 3)
        self.assertIn("COMPLETION_WITHOUT_CHECKED_TEST", {item["code"] for item in result["findings"]})

    def test_conflicting_decisions_and_deliberate_supersession(self):
        first = record("choice-a", type="decision", claim_key="cache-policy", position="local")
        other = record("choice-b", type="decision", claim_key="cache-policy", position="remote",
                       title="Competing option")
        _, saved = self.capture([first, other])
        old_id = saved["manifest"]["last_capture_id"]
        self.assertEqual(self.ok("restore", "--record", "choice-a")["effective_status"], "CONFLICTED")
        winner = record("choice-c", type="decision", claim_key="cache-policy", position="local",
                        title="Adopt local cache", supersedes=["choice-a", "choice-b"])
        code, output = self.capture([winner], old_id)
        self.assertEqual(code, 0, output)
        self.assertTrue(self.ok("restore", "--record", "choice-b")["superseded"])
        old = self.ok("restore", "--record", "choice-b", "--at", old_id)
        self.assertEqual(old["effective_status"], "CONFLICTED")
        self.assertFalse(old["superseded"])
        self.assertEqual([r["id"] for r in self.ok("restore")["sections"]["key_decisions"]], ["choice-c"])

    def test_missing_file_downgrades_without_modifying_record(self):
        claim = record(evidence=[{"kind": "file", "path": "missing.py", "locator": "Unavailable."}])
        self.assertEqual(self.capture([claim])[0], 0)
        result = self.ok("restore", "--record", "first")
        self.assertEqual(result["record"]["status"], "UNVERIFIED")
        self.assertEqual(result["effective_status"], "STALE")
        self.assertEqual(result["findings"][0]["code"], "EVIDENCE_MISSING")

    def test_short_uses_whole_records_and_detailed_has_evidence(self):
        claims = [record(f"row-{i}", title=f"Item {i}", content=f"{i} " + "示例" * 270,
                         priority=1 if i == 3 else 2,
                         evidence=[{"kind": "session", "locator": "Observed in the fixture."}])
                  for i in range(16)]
        self.assertEqual(self.capture(claims, next_steps=["First.", "Second.", "Third."])[0], 0)
        short = self.ok("restore", "--budget", "SHORT")
        detailed = self.ok("restore", "--budget", "detailed")
        self.assertEqual(short["sections"]["current_state"][0]["id"], "row-3")
        self.assertEqual(short["sections"]["current_state"][0]["content"], claims[3]["content"])
        self.assertGreater(short["omitted_records"], detailed["omitted_records"])
        self.assertEqual(short["project_note"]["next_steps"], ["First.", "Second."])
        self.assertEqual(short["project_note"]["summary"], None)
        self.assertEqual(detailed["project_note"]["next_steps"], ["First.", "Second.", "Third."])
        self.assertEqual(detailed["omitted_project_steps"], 0)
        self.assertIn("evidence", detailed["sections"]["current_state"][0])
