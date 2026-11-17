# Context Keeper V1 data and inputs

Use UTF-8 JSON with no duplicate keys. [context.schema.json](context.schema.json) defines CAPTURE at its root and includes schemas for the profile, manifest, snapshot, and records in `$defs`. Runtime checks also enforce evidence-kind fields, boundary checks, update preconditions and links. Input files are explicitly selected and should contain only sanitized structured data.

## INIT profile (optional)

```json
{
  "project_name": "Inventory Importer",
  "goal": "Import supplier CSV rows without duplicating items.",
  "tech_stack": ["Python", "CSV"],
  "exclude_paths": ["fixtures/private", "config/customer.json"]
}
```

All four fields are optional. A missing goal is unknown. Use project-relative, forward-slash paths. Exclusions apply to evidence and Git inspection. Without a profile, INIT uses the directory name and recognizes only the presence of known top-level technology markers.

## CAPTURE delta

```json
{
  "expected_capture_id": null,
  "records": [
    {
      "id": "import-identity",
      "type": "decision",
      "title": "Key rows by supplier SKU",
      "content": "Use supplier SKU to make repeated imports idempotent.",
      "status": "REPORTED",
      "evidence": [{"kind": "user", "locator": "User approved the SKU key in the current session."}],
      "task_status": "IN_PROGRESS",
      "next_steps": ["Add a duplicate-import test."],
      "claim_key": "row-identity",
      "position": "supplier-sku",
      "priority": 1
    }
  ],
  "handoff": {
    "scope": {
      "sources": ["visible_session", "user_input"],
      "limits": "Current conversation only. Earlier conversations were unavailable."
    },
    "summary": "The user selected the row identity strategy.",
    "next_steps": ["Add a duplicate-import test."]
  }
}
```

## Saving several tasks

A project can hold many tasks at once, but a snapshot carries exactly one project-level `handoff`, so the handoff cannot represent several tasks: each capture replaces it whole. Save per task instead. Put the task's own summary in that record's `content` and that task's own next actions in its optional `next_steps` (task and issue records only, at most 12 entries of at most 240 characters). Records accumulate by stable ID, so two tasks never overwrite each other's steps, and `restore --task SELECTOR` can return either one alone. Keep the handoff for the project-level view — the goal, the shared thread, and any step that genuinely spans tasks. A record captured before per-task steps existed simply has no `next_steps`; restore then reports `next_steps_source: "missing"` and the `content` text still carries the stated next action.

Use `null` for `expected_capture_id` only before the first capture. For changes to an existing record, supply its current `updated_at` as `expected_updated_at`. IDs are stable lowercase slugs. The script sets `created_at`, `updated_at` and the per-record evidence baseline. Priority 1 is highest. Task and issue records can carry `TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`, or `CANCELLED`. Use a `goal` record if the original profile goal is unknown or a goal needs updating.

Use an explicit `claim_key` for a shared factual proposition or decision dimension, and `position` for the asserted value. Distinct active positions raise a conflict. Deliberate decision replacement uses a new decision ID with `supersedes: ["old-id"]`. An earlier same-claim decision remains accessible. Identical active assertions under different IDs are rejected on capture. Wording differences and implicit contradictions require human or agent judgment.

## Project-local storage

- `manifest.json` stores the stable UUID, profile, timestamps, schema version, latest capture ID, and Git baseline. Keep this UUID across moves and renames. Copies retain the same identity, so use INIT on a fresh store for a separate project.
- `snapshots/<sha256>.json` holds each handoff, Git baseline, record index and link to its predecessor. The current snapshot supplies the latest handoff and state.
- `records/<sha256>.json` holds immutable versions of goals, state, decisions, issues and tasks. Untouched records are reused by the next snapshot.
- `write.lock` is the writer lock. The OS releases it when the process exits.

RESTORE is the supported presentation layer. Hand-edited Markdown copies are not authoritative. Timestamps are timezone-aware ISO 8601 in UTC. Immutable object names hash the UTF-8 JSON bytes. Older published captures remain reachable through the manifest and parent chain.

## Concurrent writers

Every mutating command holds the exclusive, non-blocking lock on `.context-keeper/write.lock` for the whole write (`scripts/storage.py:84-98`; `msvcrt.locking` on Windows, `fcntl.flock` on POSIX). The operating system releases it when the process exits, including after an interruption. A second writer that cannot take the lock fails at once with `WRITE_BUSY` (exit 5) and never enters the critical section, so it stages and publishes nothing.

Inside the lock, CAPTURE re-reads the manifest (`scripts/storage.py:250`) and compares the payload's `expected_capture_id` with `last_capture_id` (`scripts/storage.py:255-256`). A stale ID fails with `CAPTURE_CONFLICT` (exit 5) before any record or snapshot is written. Re-sending a byte-identical payload under the same Git identity is not a conflict: it is treated as a duplicate and returns `changed: false` with the current manifest (`scripts/storage.py:251-259`).

Publication order is immutable objects first, manifest last. Before replacing the manifest the writer re-reads it and compares it with the copy taken under the lock (`scripts/storage.py:340-341`); a mismatch - a writer that bypassed the lock or a manual edit - fails again with `CAPTURE_CONFLICT`, so no snapshot is left half-published. A failed or interrupted write leaves at most unreachable immutable objects in `records/` or `snapshots/`; the next snapshot chains from the last published manifest, and `restore --at <capture_id>` keeps every earlier one readable.

Neither failure retries automatically: run `status` for the current `last_capture_id` and prepare a fresh payload. Measured 2026-11-17: 12 rounds of two real processes left the loser with `WRITE_BUSY` 12/12; the automated races (10 rounds, plus one 4-process round) each committed exactly once, kept `validate` at `PASS`, left every `records/` and `snapshots/` file hashing to its own name, and produced no `.pending-*` residue (`tests/test_concurrent_capture.py`).
