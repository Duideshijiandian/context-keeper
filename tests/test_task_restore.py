import json

from support import ProjectCase, record


def task(id, title, content, steps=None, **fields):
    entry = {
        "id": id, "type": "task", "title": title, "content": content,
        "status": "REPORTED", "task_status": "IN_PROGRESS",
        "evidence": [{"kind": "user", "locator": "User described this task in the session."}],
    }
    if steps is not None:
        entry["next_steps"] = steps
    entry.update(fields)
    return entry


class TaskRestoreTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.ok("init")

    def test_resumable_tasks_use_status_and_keep_evidence_confidence_separate(self):
        entries = [
            task("todo", "Pending work", "No steps yet.", task_status="TODO"),
            task("working", "Active work", "Continue.", status="CONFLICTED"),
            task("blocked", "Blocked work", "Waiting.", task_status="BLOCKED",
                 evidence=[{"kind": "file", "path": "missing.txt", "locator": "Missing anchor."}]),
            task("done", "Completed work", "Finished.", ["Historical follow-up."], task_status="DONE"),
            task("cancelled", "Cancelled work", "Cancelled.", task_status="CANCELLED"),
            task("issue", "Open issue", "Investigate.", type="issue"),
            record("state", title="Project state"),
        ]
        self.assertEqual(self.capture(entries)[0], 0)
        result = self.ok("restore")
        self.assertEqual([item["id"] for item in result["resumable_tasks"]],
                         ["working", "todo", "blocked"])
        self.assertEqual([item["effective_status"] for item in result["resumable_tasks"]],
                         ["CONFLICTED", "REPORTED", "STALE"])
        self.assertEqual(result["resumable_tasks"][2]["task_status"], "BLOCKED")
        self.assertEqual(result["tasks_needing_status"], [])
        self.assertEqual(result["records_with_steps"], ["done"])

    def test_legacy_task_without_status_requires_confirmation(self):
        legacy = task("legacy", "Legacy task", "Old plan.", ["Continue old work."])
        del legacy["task_status"]
        self.assertEqual(self.capture([legacy])[0], 0)
        result = self.ok("restore")
        self.assertEqual(result["resumable_tasks"], [])
        self.assertEqual(result["tasks_needing_status"], [{
            "id": "legacy", "title": "Legacy task", "task_status": None,
            "effective_status": "REPORTED",
        }])

    def test_current_snapshot_updates_task_order_and_completion(self):
        first_code, first = self.capture([
            task("old", "Older task", "Initial progress."),
            task("todo", "Pending task", "Pending.", task_status="TODO"),
        ])
        self.assertEqual(first_code, 0, first)
        first_id = first["manifest"]["last_capture_id"]
        old_record = self.ok("restore", "--record", "old")["record"]
        second_code, second = self.capture([
            task("new", "Newer task", "New progress."),
        ], first_id)
        self.assertEqual(second_code, 0, second)
        self.assertEqual([row["id"] for row in self.ok("restore")["resumable_tasks"]],
                         ["new", "old", "todo"])
        third_code, third = self.capture([
            task("old", "Older task", "Completed.", task_status="DONE",
                 expected_updated_at=old_record["updated_at"]),
        ], second["manifest"]["last_capture_id"])
        self.assertEqual(third_code, 0, third)
        self.assertEqual([row["id"] for row in self.ok("restore")["resumable_tasks"]], ["new", "todo"])
        self.assertEqual(self.ok("restore", "--task", "old")["task"]["task_status"], "DONE")
        self.assertEqual(self.ok("restore", "--task", "old", "--at", first_id)["task"]["task_status"],
                         "IN_PROGRESS")

    def test_five_completed_tasks_with_steps_have_no_resumable_tasks(self):
        self.assertEqual(self.capture([
            task(f"done-{index}", f"Completed task {index}", f"Delivered {index}.",
                 ["Historical next step."], task_status="DONE") for index in range(5)
        ])[0], 0)
        store = self.project / ".context-keeper"
        before = {path.relative_to(store): path.read_bytes() for path in store.rglob("*") if path.is_file()}
        result = self.ok("restore")
        self.assertEqual(result["resumable_tasks"], [])
        self.assertEqual(result["records_with_steps_count"], 5)
        after = {path.relative_to(store): path.read_bytes() for path in store.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_task_indexes_remain_complete_when_section_budget_is_exhausted(self):
        entries = [task(f"task-{index}", f"Task {index}", f"{index} " + "x" * 650)
                   for index in range(30)]
        self.assertEqual(self.capture(entries)[0], 0)
        for budget in ("short", "normal", "detailed"):
            with self.subTest(budget=budget):
                result = self.ok("restore", "--budget", budget)
                self.assertEqual(len(result["resumable_tasks"]), 30)
                self.assertEqual({row["id"] for row in result["resumable_tasks"]},
                                 {entry["id"] for entry in entries})
        selected = self.ok("restore", "--task", "task-0")
        self.assertEqual(selected["task"]["content"], entries[0]["content"])
        self.assertNotIn("resumable_tasks", selected)

    def test_per_task_next_steps_survive_a_second_task(self):
        first = self.capture([task("task-a", "CSV import dedupe", "Import dedupe is implemented.",
                                   ["A only: add a duplicate-import test."])],
                             next_steps=["Project level: ship the importer."])
        self.assertEqual(first[0], 0, first[1])
        second = self.capture([task("task-b", "Monthly report export", "Export runs manually.",
                                    ["B only: add thousands separators."])],
                              first[1]["manifest"]["last_capture_id"],
                              next_steps=["Project level: ship the exporter."])
        self.assertEqual(second[0], 0, second[1])

        a = self.ok("restore", "--task", "task-a")
        b = self.ok("restore", "--task", "task-b")
        self.assertEqual(a["next_steps"], ["A only: add a duplicate-import test."])
        self.assertEqual(b["next_steps"], ["B only: add thousands separators."])
        self.assertEqual(a["next_steps_source"], "record")
        self.assertEqual(a["task"]["content"], "Import dedupe is implemented.")
        self.assertNotIn("B only", json.dumps(a, ensure_ascii=False))
        self.assertNotIn("report export", json.dumps(a, ensure_ascii=False).casefold())
        self.assertNotIn("project_note", a)

        normal = self.ok("restore")
        self.assertEqual(normal["project_note"]["next_steps"], ["Project level: ship the exporter."])
        self.assertEqual(normal["omitted_project_steps"], 0)
        self.assertEqual({row["id"] for row in normal["sections"]["current_state"]},
                         {"task-a", "task-b"})

    def test_exact_id_wins_and_title_substring_resolves(self):
        saved = self.capture([task("task-import", "CSV 导入去重", "导入已实现。"),
                              task("task-report", "月度报表导出", "导出已实现。")])
        self.assertEqual(saved[0], 0, saved[1])
        by_id = self.ok("restore", "--task", "task-import")
        self.assertEqual(by_id["task"]["id"], "task-import")
        by_title = self.ok("restore", "--task", "报表")
        self.assertEqual(by_title["task"]["id"], "task-report")
        self.assertEqual(self.ok("restore", "--task", "  TASK-IMPORT  ")["task"]["id"], "task-import")

    def test_ambiguous_and_missing_selectors_list_candidates(self):
        saved = self.capture([task("task-a", "Import rows", "A."), task("task-b", "Import report", "B.")])
        self.assertEqual(saved[0], 0, saved[1])
        code, output = self.cli("restore", "--task", "import")
        self.assertEqual(code, 4)
        self.assertEqual(output["error"]["code"], "TASK_AMBIGUOUS")
        self.assertEqual({item["id"] for item in output["error"]["candidates"]}, {"task-a", "task-b"})
        self.assertIn("task-a", output["error"]["message"])
        missing_code, missing = self.cli("restore", "--task", "nothing like this")
        self.assertEqual((missing_code, missing["error"]["code"]), (4, "TASK_NOT_FOUND"))
        both_code, both = self.cli("restore", "--task", "task-a", "--record", "task-a")
        self.assertEqual((both_code, both["error"]["code"]), (2, "USAGE"))
        at_code, at = self.cli("restore", "--at", saved[1]["manifest"]["last_capture_id"])
        self.assertEqual((at_code, at["error"]["code"]), (2, "USAGE"))

    def test_legacy_record_reports_missing_steps(self):
        first = self.capture([task("task-old", "Legacy task",
                                   "Saved before per-task steps. Next: write the test.")])
        self.assertEqual(first[0], 0, first[1])
        second = self.capture([task("task-new", "Newer task", "Fresh.", ["Newer only step."])],
                              first[1]["manifest"]["last_capture_id"])
        self.assertEqual(second[0], 0, second[1])
        old = self.ok("restore", "--task", "task-old")
        self.assertEqual(old["next_steps"], [])
        self.assertEqual(old["next_steps_source"], "missing")
        self.assertIn("no structured next_steps", old["next_steps_note"])
        self.assertIn("write the test", old["task"]["content"])
        self.assertNotIn("Newer only step.", json.dumps(old, ensure_ascii=False))
        self.assertEqual(self.ok("restore", "--task", "task-new")["next_steps_source"], "record")
        rejected = self.capture([record("choice", type="decision", claim_key="cache", position="local",
                                        next_steps=["Only tasks may carry steps."])],
                                second[1]["manifest"]["last_capture_id"])
        self.assertEqual((rejected[0], rejected[1]["error"]["code"]), (4, "INVALID_RECORD"))

    def test_project_view_indexes_records_that_carry_steps(self):
        first = self.capture([task("task-a", "CSV import dedupe", "A.", ["A only step."]),
                             task("task-plain", "Plain task", "No steps recorded.")])
        self.assertEqual(first[0], 0, first[1])
        second = self.capture([task("task-b", "Monthly report export", "B.", ["B only step."])],
                              first[1]["manifest"]["last_capture_id"],
                              summary="Project note after both tasks.")
        self.assertEqual(second[0], 0, second[1])

        normal = self.ok("restore", "--budget", "normal")
        self.assertEqual(normal["records_with_steps"], ["task-a", "task-b"])
        self.assertEqual(normal["records_with_steps_count"], 2)
        self.assertEqual(normal["project_note"]["next_steps"], [])
        self.assertEqual(normal["omitted_project_steps"], 0)
        self.assertEqual(normal["project_note"]["summary"], "Project note after both tasks.")
        self.assertIn("scope", normal["project_note"])
        self.assertEqual(normal["project_note"]["scope"]["limits"], "Isolated fixture only.")
        self.assertNotIn("next_steps", normal, "project steps must not sit at the top level")
        self.assertTrue(any("--task" in line for line in normal["trust"]))
        sections = json.dumps(normal["sections"], ensure_ascii=False)
        self.assertNotIn("A only step.", sections)
        self.assertNotIn("B only step.", sections)
        self.assertEqual(self.ok("restore", "--task", "task-a")["next_steps"], ["A only step."])
