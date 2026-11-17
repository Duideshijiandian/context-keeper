import json
from pathlib import Path
import re
import shutil
from unittest import mock

import jsonschema
import yaml

from support import ProjectCase, SOURCE, record
from sanitizer import KeeperError, digest
from sync_deploy import FILES, deploy


class StaticDeploymentTests(ProjectCase):
    def test_frontmatter_and_references(self):
        metadata = yaml.safe_load((SOURCE / "SKILL.md").read_text("utf-8").split("---", 2)[1])
        self.assertEqual(metadata["name"], SOURCE.name)
        self.assertRegex(metadata["name"], r"^[a-z][a-z0-9-]{0,63}$")
        self.assertNotIn("--", metadata["name"])
        self.assertGreater(len(metadata["description"]), 0)
        self.assertLessEqual(len(metadata["description"]), 1024)
        for name in ("SKILL.md", *(entry for entry in FILES if entry.endswith(".md"))):
            path = SOURCE / name
            for link in re.findall(r"\]\(([^)]+)\)", path.read_text("utf-8")):
                self.assertTrue((path.parent / link).is_file(), (path, link))

    def test_standard_schema_accepts_canonical_objects(self):
        schema = json.loads((SOURCE / "references/context.schema.json").read_text("utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        manifest = self.ok("init")["manifest"]
        self.assertEqual(self.capture([record()])[0], 0)
        payload = json.loads((self.workspace / "payload.json").read_text("utf-8"))
        validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
        validator.validate(payload)
        for name, data in (
            ("manifest", manifest),
            ("snapshot", json.loads((self.project / ".context-keeper" / "snapshots" /
                                    f"{manifest['snapshot']}.json").read_text("utf-8"))),
            ("record", json.loads(next((self.project / ".context-keeper" / "records").glob("*.json"))
                                  .read_text("utf-8"))),
        ):
            validator.evolve(schema={**schema, "$ref": f"#/$defs/{name}"}).validate(data)
        self.assertFalse(validator.is_valid({**payload, "extra": "invalid"}))

    def test_cli_usage_errors_are_machine_readable(self):
        code, output = self.cli("restore", "--budget", "unlimited")
        self.assertEqual((code, output["error"]["code"]), (2, "USAGE"))

    def test_scenario_catalog(self):
        data = json.loads((SOURCE / "tests/scenarios.json").read_text("utf-8"))
        self.assertGreaterEqual(len(data), 12)
        self.assertEqual(len(data), len({row["id"] for row in data}))
        self.assertTrue(all(row["checks"] and row["prompt"] for row in data))

    def test_deployment_checks_inventory_backup_and_idempotence(self):
        source = self.workspace / "context-keeper"
        skills = self.workspace / "skills"
        other = skills / "unrelated"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("untouched", encoding="utf-8")
        for name in FILES:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / name, path)
        (source / "test-results.txt").write_text("not a runtime file", encoding="utf-8")
        first = deploy(source, skills, False)
        self.assertTrue(first["changed"])
        self.assertIsNone(first["backup"])
        target = Path(first["target"])
        self.assertFalse((target / "test-results.txt").exists())
        self.assertFalse(deploy(source, skills, True)["changed"])
        original = (target / "SKILL.md").read_bytes()
        (source / "SKILL.md").write_bytes(original + b"\n")
        with self.assertRaises(KeeperError) as error:
            deploy(source, skills, True)
        self.assertEqual(error.exception.code, "HASH_MISMATCH")
        second = deploy(source, skills, False)
        self.assertEqual((Path(second["backup"]) / "SKILL.md").read_bytes(), original)
        self.assertEqual(second["sha256"]["SKILL.md"], digest(original + b"\n"))
        self.assertFalse(deploy(source, skills, False)["changed"])
        self.assertEqual((other / "SKILL.md").read_text("utf-8"), "untouched")
