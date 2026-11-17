"""Structural checks from the bundled schema, plus evidence and consistency checks."""

from datetime import datetime, timezone
from pathlib import Path
import re

from git_inspector import OID
from sanitizer import (
    KeeperError, bounded_read, digest, excluded, fail, inside, json_bytes, parse_json,
    relative_path, screen,
)


DEFINITIONS = parse_json(
    (Path(__file__).resolve().parents[1] / "references" / "context.schema.json").read_bytes()
)["$defs"]
TYPES = {"object": dict, "array": list, "string": str, "integer": int,
         "boolean": bool, "null": type(None)}
TEMPORAL = re.compile(r"^\d{4}-\d\d-\d\d[Tt]\d\d:\d\d:\d\d(?:\.\d+)?(?:[Zz]|[+-]\d\d:\d\d)$")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def matches(value, rule):
    # Implements the small, tested subset used by the bundled Draft 2020-12 schema.
    if "$ref" in rule:
        return matches(value, DEFINITIONS[rule["$ref"].split("/")[-1]])
    if "anyOf" in rule:
        return any(matches(value, option) for option in rule["anyOf"])
    if "enum" in rule:
        return value in rule["enum"]
    if "const" in rule:
        return type(value) is type(rule["const"]) and value == rule["const"]
    if type(value) is not TYPES[rule["type"]]:
        return False
    if isinstance(value, dict):
        props = rule["properties"]
        return set(rule["required"]) <= value.keys() <= props.keys() and all(
            matches(item, props[key]) for key, item in value.items()
        )
    if isinstance(value, list):
        return rule["minItems"] <= len(value) <= rule["maxItems"] and all(
            matches(item, rule["items"]) for item in value
        )
    if isinstance(value, str):
        if not rule["minLength"] <= len(value) <= rule["maxLength"] or not value.strip():
            return False
        if any(ord(char) < 32 and char not in "\n\t" or 0xD800 <= ord(char) <= 0xDFFF
               for char in value):
            return False
        if "pattern" in rule and not re.fullmatch(rule["pattern"], value):
            return False
        if rule.get("format") == "date-time":
            try:
                return bool(TEMPORAL.fullmatch(value) and
                            datetime.fromisoformat(value.upper()).utcoffset() is not None)
            except ValueError:
                return False
    if type(value) is int:
        return rule["minimum"] <= value <= rule["maximum"]
    return True


def require(value, schema):
    if not matches(value, DEFINITIONS[schema]):
        fail("INVALID_SCHEMA", f"Invalid {schema} shape or field value. See the bundled schema.")
    screen(value)


def git_identity(git):
    if git["working_tree_status"] == "unavailable":
        return None
    if not git["fingerprint"]:
        return None
    return digest(json_bytes([git["git_head"], git["fingerprint"]]))


def evidence_shape(item, exclusions):
    kind = item["kind"]
    file_fields = {"path", "sha256", "line"}
    if kind in {"file", "test"}:
        if "path" not in item:
            fail("INVALID_EVIDENCE", "File and test sources require a project-relative path.")
        relative_path(item["path"])
        if excluded(item["path"], exclusions):
            fail("SENSITIVE_PATH", "Evidence points at an excluded path.")
    elif file_fields & item.keys():
        fail("INVALID_EVIDENCE", "File metadata is limited to file and test sources.")
    if ("commit" in item) != (kind == "git_commit"):
        fail("INVALID_EVIDENCE", "Only Git sources require a commit field.")
    if ("exit_code" in item) != (kind == "test"):
        fail("INVALID_EVIDENCE", "Only test sources require an observed exit code.")


def record_shape(record, exclusions):
    if "task_status" in record and record["type"] not in {"task", "issue"}:
        fail("INVALID_RECORD", "Only task and issue records may have task_status.")
    if "next_steps" in record and record["type"] not in {"task", "issue"}:
        fail("INVALID_RECORD", "Only task and issue records may carry next_steps.")
    if ("claim_key" in record) != ("position" in record):
        fail("INVALID_RECORD", "Supply claim_key and position together.")
    supersedes = record.get("supersedes", [])
    if (record["id"] in supersedes or len(set(supersedes)) != len(supersedes)
            or supersedes and record["type"] != "decision"):
        fail("INVALID_RECORD", "Only decisions may supersede distinct previous decision IDs.")
    for item in record["evidence"]:
        evidence_shape(item, exclusions)
    if record["status"] == "REPORTED" and not record["evidence"]:
        fail("INVALID_EVIDENCE", "REPORTED records require an attributed source.")
    if record["status"] == "VERIFIED":
        eligible = (
            item["kind"] in {"file", "test"} and "sha256" in item
            or item["kind"] == "git_commit"
            for item in record["evidence"]
        )
        if not any(eligible):
            fail("INVALID_EVIDENCE", "VERIFIED records require a checkable evidence anchor.")
        if record.get("task_status") == "DONE" and not any(
            item["kind"] == "test" and item.get("exit_code") == 0
            and "sha256" in item and "observed_at" in item
            for item in record["evidence"]
        ):
            fail("INVALID_EVIDENCE", "A VERIFIED DONE claim requires a dated successful test log.")


def check_anchor(root, item, git, cache):
    kind = item["kind"]
    if kind == "git_commit":
        if item["commit"] not in git["recent_commits"]:
            return False, "GIT_EVIDENCE_UNCONFIRMED"
        return True, None
    if kind not in {"file", "test"}:
        return False, None
    path = item["path"]
    if path not in cache:
        try:
            data = bounded_read(inside(root, path))
            cache[path] = (digest(data), data.count(b"\n") + int(not data.endswith(b"\n")), None)
        except FileNotFoundError:
            cache[path] = (None, 0, "EVIDENCE_MISSING")
        except KeeperError as error:
            if error.code == "UNSAFE_PATH":
                raise
            cache[path] = (None, 0, "EVIDENCE_UNREADABLE")
        except OSError:
            cache[path] = (None, 0, "EVIDENCE_UNREADABLE")
    sha, lines, error = cache[path]
    if error:
        return False, error
    if item.get("line", 0) > lines:
        return False, "EVIDENCE_LINE_MISSING"
    if item.get("sha256") and item["sha256"] != sha:
        return False, "EVIDENCE_CHANGED"
    return "sha256" in item, None


def verify_new(root, record, git, cache):
    if record["status"] != "VERIFIED":
        return
    anchors = [check_anchor(root, item, git, cache) for item in record["evidence"]]
    if any(code for _, code in anchors) or not any(ok for ok, _ in anchors):
        fail("EVIDENCE_NOT_VERIFIED", "A VERIFIED claim has missing, changed, or unchecked evidence.")


def semantic_key(record):
    keys = ("type", "title", "content", "claim_key", "position", "supersedes")
    return digest(json_bytes({key: record.get(key) for key in keys}))


def superseded_ids(records):
    return {previous for record in records.values() for previous in record.get("supersedes", [])}


def check_links(records):
    for record in records.values():
        for previous in record.get("supersedes", []):
            old = records.get(previous)
            if (old is None or old["type"] != "decision" or old["created_at"] >= record["created_at"]
                    or old.get("claim_key") and old.get("claim_key") != record.get("claim_key")):
                fail("INVALID_SUPERSESSION", "A superseded decision must be an earlier decision on the same claim.")


def audit(root, manifest, snapshot, records, git):
    findings = []

    def issue(code, record_id=None, **fields):
        findings.append({"level": "WARNING", "code": code, **fields,
                         **({"record_id": record_id} if record_id is not None else {})})

    baseline = snapshot["git"]
    if git["git_head"] != baseline["git_head"]:
        issue("GIT_HEAD_CHANGED")
    if git["git_branch"] != baseline["git_branch"]:
        issue("GIT_BRANCH_CHANGED")
    if git["working_tree_status"] == "dirty":
        issue("WORKTREE_DIRTY", count=git["dirty_count"])
    if any(git[key] != baseline[key] for key in ("working_tree_status", "fingerprint")):
        issue("WORKTREE_BASELINE_CHANGED")
    for warning in git["warnings"]:
        issue(warning)

    retired = superseded_ids(records)
    active_claims = {}
    signatures = {}
    stale = set()
    conflicts = set()
    cache = {}
    current_identity = git_identity(git)
    for key, record in records.items():
        if key not in retired:
            signature = semantic_key(record)
            if signature in signatures:
                issue("DUPLICATE_RECORD", key, related=[signatures[signature]])
            else:
                signatures[signature] = key
            if "claim_key" in record:
                active_claims.setdefault(record["claim_key"], {}).setdefault(record["position"], []).append(key)
        evidence_ok = []
        for item in record["evidence"]:
            ok, code = check_anchor(root, item, git, cache)
            evidence_ok.append(ok)
            if code:
                issue(code, key)
                stale.add(key)
        if record["status"] == "STALE" or record["status"] == "VERIFIED" and (
            not any(evidence_ok) or record["baseline"] != current_identity
            or git["working_tree_status"] == "unknown"
        ):
            stale.add(key)
            issue("RECORD_NEEDS_RECHECK", key)
        if record["status"] == "CONFLICTED":
            conflicts.add(key)
            issue("RECORD_MARKED_CONFLICTED", key)
        if record.get("task_status") == "DONE" and not any(
            ok and item["kind"] == "test" and item.get("exit_code") == 0
            and "observed_at" in item
            for ok, item in zip(evidence_ok, record["evidence"])
        ):
            issue("COMPLETION_WITHOUT_CHECKED_TEST", key)

    for claim, positions in active_claims.items():
        if len(positions) > 1:
            ids = sorted(key for group in positions.values() for key in group)
            conflicts.update(ids)
            issue("CONFLICTING_CLAIMS", claim_key=claim, related=ids)
    return findings, stale, conflicts, retired
