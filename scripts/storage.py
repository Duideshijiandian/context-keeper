"""Append immutable records and snapshots, then atomically publish one manifest."""

from contextlib import contextmanager
from datetime import datetime, timedelta
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from git_inspector import inspect
from sanitizer import (
    bounded_read, checked_node, digest, fail, inside, json_bytes, parse_json, relative_path,
)
from validator import (
    audit, check_links, git_identity, now, record_shape, require, semantic_key, superseded_ids,
    verify_new,
)


STORE = ".context-keeper"
GIT_KEYS = ("git_available", "git_branch", "git_head", "working_tree_status", "fingerprint")
MARKERS = {
    "pyproject.toml": "Python", "requirements.txt": "Python", "package.json": "Node.js",
    "Cargo.toml": "Rust", "go.mod": "Go", "pom.xml": "Java", "build.gradle": "Gradle",
    "CMakeLists.txt": "CMake", "composer.json": "PHP", "Gemfile": "Ruby",
    "Package.swift": "Swift", "global.json": ".NET",
}


def markers(root):
    from sanitizer import KeeperError
    result = {}
    for name, stack in MARKERS.items():
        try:
            if checked_node(root / name, directory=False, missing=True) is not None:
                result[name] = stack
        except KeeperError:
            continue
    return result


def read_json(path):
    return parse_json(bounded_read(path))


@contextmanager
def staged_dir(parent, prefix):
    # Windows tempfile directories can get private ACLs tied to the sandbox identity.
    # A directory that will be published must inherit ACLs from its ordinary parent.
    stage = parent / (prefix + uuid.uuid4().hex)
    stage.mkdir()
    try:
        yield stage
    finally:
        if (checked_node(stage, directory=True, missing=True) is not None
                and stage.resolve().parent == parent.resolve()):
            shutil.rmtree(stage, ignore_errors=True)


def atomic_write(path, data):
    checked_node(path, directory=False, missing=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            # On filesystems supporting directory fsync, persist the rename as well.
            try:
                directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                pass
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def writer_lock(store):
    path = inside(store, "write.lock", missing=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags, 0o600), "r+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fail("WRITE_BUSY", "Another writer holds the context lock. Retry after it finishes.")
        yield  # Closing the handle releases the operating system lock, even after interruption.


def immutable(store, folder, value):
    data = json_bytes(value)
    sha = digest(data)
    path = inside(store, f"{folder}/{sha}.json", missing=True)
    if checked_node(path, directory=False, missing=True) is None:
        atomic_write(path, data)
    elif digest(bounded_read(path)) != sha:
        fail("CORRUPT_STORE", "An immutable object does not match its digest.")
    return sha


def read_object(store, folder, sha, schema):
    data = bounded_read(inside(store, f"{folder}/{sha}.json"))
    if digest(data) != sha:
        fail("CORRUPT_STORE", "An immutable object does not match its digest.")
    value = parse_json(data)
    require(value, schema)
    return value


def snapshot_at(store, sha, project_id):
    snapshot = read_object(store, "snapshots", sha, "snapshot")
    if snapshot["project_id"] != project_id:
        fail("CORRUPT_STORE", "Snapshot belongs to a different project.")
    return snapshot


def load_records(store, manifest, snapshot, cache=None):
    if cache is None:
        cache = {}
    records = {}
    for link in snapshot["record_index"]:
        sha = link["sha256"]
        if sha not in cache:
            record = read_object(store, "records", sha, "record")
            record_shape(record, manifest["exclude_paths"])
            if record["updated_at"] < record["created_at"]:
                fail("CORRUPT_STORE", "Record timestamps are out of order.")
            cache[sha] = record
        record = cache[sha]
        if link["id"] in records or link["id"] != record["id"]:
            fail("CORRUPT_STORE", "Record index has a duplicate or mismatched ID.")
        if record["updated_at"] > snapshot["created_at"]:
            fail("CORRUPT_STORE", "Record timestamp is newer than its snapshot.")
        records[link["id"]] = record
    check_links(records)
    return records


def open_store(root):
    store = inside(root, STORE, directory=True)
    manifest = read_json(inside(store, "manifest.json"))
    inside(store, "snapshots", directory=True)
    inside(store, "records", directory=True)
    inside(store, "write.lock", missing=True)
    if isinstance(manifest, dict) and type(manifest.get("schema_version")) is int:
        if manifest["schema_version"] != 1:
            fail("UNSUPPORTED_VERSION", "This version supports schema_version 1.")
    require(manifest, "manifest")
    for name in manifest["exclude_paths"]:
        relative_path(name)
    snapshot = snapshot_at(store, manifest["snapshot"], manifest["project_id"])
    if (snapshot["created_at"] != manifest["updated_at"]
            or manifest["updated_at"] < manifest["created_at"]
            or any(manifest[key] != snapshot["git"][key] for key in GIT_KEYS)
            or (snapshot["capture_id"] if snapshot["parent"] else None) != manifest["last_capture_id"]):
        fail("CORRUPT_STORE", "Manifest and snapshot disagree.")
    records = load_records(store, manifest, snapshot)
    return store, manifest, snapshot, records


def history(store, manifest, snapshot, limit=None, verify_records=False):
    count = 0
    visited = {manifest["snapshot"]}
    captures = {snapshot["capture_id"]}
    cache = {}
    child = snapshot
    while child["parent"] is not None:
        parent_sha = child["parent"]
        if parent_sha in visited or len(visited) > 10_000:
            fail("CORRUPT_STORE", "Invalid or excessively long snapshot chain.")
        if limit is not None and count >= limit:
            break
        visited.add(parent_sha)
        parent = snapshot_at(store, parent_sha, manifest["project_id"])
        if parent["created_at"] >= child["created_at"] or parent["capture_id"] in captures:
            fail("CORRUPT_STORE", "Invalid capture order or duplicate capture ID.")
        captures.add(parent["capture_id"])
        if verify_records:
            cache = {link["sha256"]: cache[link["sha256"]] for link in parent["record_index"]
                     if link["sha256"] in cache}
            load_records(store, manifest, parent, cache)
        child = parent
        count += 1
        yield parent
    if child["parent"] is None and (child["created_at"] != manifest["created_at"]
                                    or child["record_index"] or child["request_sha256"] is not None):
        fail("CORRUPT_STORE", "Invalid initial snapshot.")


def initialize(root, profile):
    target = inside(root, STORE, directory=True, missing=True)
    if checked_node(target, directory=True, missing=True) is not None:
        fail("ALREADY_INITIALIZED", "A context directory already exists. Inspect it with validate.")
    require(profile, "profile")
    exclusions = profile.get("exclude_paths", [])
    for path in exclusions:
        relative_path(path)
    initial_markers = markers(root)
    git = inspect(root, exclusions)
    created = now()
    project_id = str(uuid.uuid4())
    snapshot = dict(
        schema_version=1, project_id=project_id, capture_id=str(uuid.uuid4()), parent=None,
        created_at=created, request_sha256=None, record_index=[], git=git,
        handoff={"scope": {"sources": ["user_input"] if profile else ["project_files"],
                          "limits": "Initialization only. No prior session was imported."},
                 "summary": None, "next_steps": []},
    )
    require(snapshot, "snapshot")
    with staged_dir(root, ".context-keeper-init-") as store:
        (store / "snapshots").mkdir()
        (store / "records").mkdir()
        sha = immutable(store, "snapshots", snapshot)
        manifest = dict(
            schema_version=1, project_id=project_id,
            project_name=profile.get("project_name", root.name or "Project"),
            goal=profile.get("goal"), tech_stack=profile.get("tech_stack", sorted(set(initial_markers.values()))),
            exclude_paths=exclusions, created_at=created, updated_at=created,
            last_capture_id=None, snapshot=sha, **{key: git[key] for key in GIT_KEYS},
        )
        require(manifest, "manifest")
        atomic_write(store / "manifest.json", json_bytes(manifest))
        if checked_node(target, directory=True, missing=True) is not None:
            fail("ALREADY_INITIALIZED", "A context directory appeared during initialization.")
        os.rename(store, target)
    _, actual, current, records = open_store(root)
    findings, *_ = audit(root, actual, current, records, git)
    return {"manifest": actual, "git": git, "markers": initial_markers, "findings": findings}


def identity(git):
    return tuple(git[key] for key in GIT_KEYS) + (git["dirty_count"], git["warnings"])


def capture(root, payload):
    require(payload, "capture")
    store = inside(root, STORE, directory=True)
    with writer_lock(store):
        _, manifest, previous, existing = open_store(root)
        request_sha = digest(json_bytes(payload))
        git = inspect(root, manifest["exclude_paths"])
        same_git = identity(git) == identity(previous["git"])
        duplicate = request_sha == previous["request_sha256"] and same_git
        if payload["expected_capture_id"] != manifest["last_capture_id"] and not duplicate:
            fail("CAPTURE_CONFLICT", "The context changed. Read status and prepare a fresh payload.")
        if duplicate:
            findings, *_ = audit(root, manifest, previous, existing, git)
            return {"changed": False, "manifest": manifest, "git": git, "findings": findings}
        previous_time = datetime.fromisoformat(previous["created_at"])
        created = max(datetime.fromisoformat(now()), previous_time + timedelta(microseconds=1)).isoformat(
            timespec="microseconds"
        )
        current = existing.copy()
        requested = set()
        cache = {}
        updated = set()
        for entry in payload["records"]:
            key = entry["id"]
            if key in requested:
                fail("DUPLICATE_ID", "A capture contains the same record ID more than once.")
            requested.add(key)
            candidate = {name: value for name, value in entry.items() if name != "expected_updated_at"}
            record_shape(candidate, manifest["exclude_paths"])
            old = existing.get(key)
            if old:
                old_fields = {name: value for name, value in old.items()
                              if name not in {"created_at", "updated_at", "baseline"}}
                baseline = git_identity(git)
                if old_fields == candidate and (
                    old["status"] != "VERIFIED" or old["baseline"] == baseline
                    or "expected_updated_at" not in entry
                ):
                    if entry.get("expected_updated_at") == old["updated_at"]:
                        verify_new(root, candidate, git, cache)
                    continue
                if entry.get("expected_updated_at") != old["updated_at"]:
                    fail("RECORD_CONFLICT", "Updating an existing ID requires its current updated_at.")
                if old["type"] != candidate["type"] or old.get("supersedes", []) != candidate.get("supersedes", []):
                    fail("RECORD_CONFLICT", "Record type and supersession are immutable.")
                if old["type"] == "decision" and any(
                    old.get(field) != candidate.get(field)
                    for field in ("title", "content", "claim_key", "position")
                ):
                    fail("RECORD_CONFLICT", "Use a new decision ID to preserve the previous decision.")
            elif "expected_updated_at" in entry:
                fail("RECORD_CONFLICT", "A new record cannot have expected_updated_at.")
            if not set(candidate.get("supersedes", [])) <= existing.keys():
                fail("INVALID_SUPERSESSION", "A decision may supersede only previously stored decisions.")
            verify_new(root, candidate, git, cache)
            candidate.update(
                created_at=old["created_at"] if old else created, updated_at=created,
                baseline=git_identity(git) if candidate["status"] == "VERIFIED" else None,
            )
            require(candidate, "record")
            current[key] = candidate
            updated.add(key)

        check_links(current)
        retired = superseded_ids(current)
        signatures = {}
        for key, value in current.items():
            if key in retired:
                continue
            signature = semantic_key(value)
            other = signatures.get(signature)
            if other is not None and (key in updated or other in updated):
                fail("DUPLICATE_RECORD", "A record duplicates an active record under a different ID.")
            signatures[signature] = key

        findings, *_ = audit(root, manifest, previous, current, git)
        if not updated and same_git and payload["handoff"] == previous["handoff"]:
            return {"changed": False, "manifest": manifest, "git": git, "findings": findings}

        # Validate before publication. Failed writes leave only unreachable immutable objects.
        links = {link["id"]: link["sha256"] for link in previous["record_index"]}
        for key in sorted(updated):
            links[key] = immutable(store, "records", current[key])
        snapshot = dict(
            schema_version=1, project_id=manifest["project_id"], capture_id=str(uuid.uuid4()),
            parent=manifest["snapshot"], created_at=created, request_sha256=request_sha,
            record_index=[{"id": key, "sha256": sha} for key, sha in sorted(links.items())],
            handoff=payload["handoff"], git=git,
        )
        require(snapshot, "snapshot")
        next_manifest = dict(manifest, updated_at=created, last_capture_id=snapshot["capture_id"],
                             **{key: git[key] for key in GIT_KEYS})
        next_manifest["snapshot"] = immutable(store, "snapshots", snapshot)
        require(next_manifest, "manifest")
        if read_json(inside(store, "manifest.json")) != manifest:
            fail("CAPTURE_CONFLICT", "Manifest was changed by another writer.")
        atomic_write(inside(store, "manifest.json"), json_bytes(next_manifest))
        _, actual, committed, committed_records = open_store(root)
        findings, *_ = audit(root, actual, committed, committed_records, git)
        return {"changed": True, "manifest": actual, "git": git, "findings": findings}
