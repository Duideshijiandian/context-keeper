"""Stage and verify only the runtime files in one explicitly selected skills directory."""

import argparse
import json
import os
from pathlib import Path
import re
import sys
from uuid import uuid4

from sanitizer import KeeperError, absolute_dir, bounded_read, checked_node, digest, fail, inside
from storage import staged_dir, writer_lock


PACKAGE = "context-keeper"
FILES = (
    "SKILL.md",
    "scripts/context_cli.py",
    "scripts/git_inspector.py",
    "scripts/sanitizer.py",
    "scripts/storage.py",
    "scripts/sync_deploy.py",
    "scripts/validator.py",
    "references/context.schema.json",
    "references/data-schema.md",
    "references/capture-policy.md",
    "references/restore-policy.md",
    "references/evidence-policy.md",
    "references/security-policy.md",
)


def inventory(root):
    result = {}
    pending = [root]
    while pending:
        for path in pending.pop().iterdir():
            if path.name == "__pycache__":
                checked_node(path, directory=True)
                continue
            checked_node(path)
            if path.is_dir():
                pending.append(path)
            else:
                result[path.relative_to(root).as_posix()] = digest(bounded_read(path))
    return result


def source_files(source):
    content = {name: bounded_read(inside(source, name)) for name in FILES}
    front = content["SKILL.md"].decode("utf-8").split("---", 2)
    if len(front) != 3 or front[0] or not re.search(r"(?m)^name: context-keeper\s*$", front[1]):
        fail("INVALID_SOURCE", "The source is missing valid Context Keeper frontmatter.")
    return content


def deploy(source, skills, check):
    if source.name != PACKAGE:
        fail("INVALID_SOURCE", "The skill source directory must be named context-keeper.")
    target = inside(skills, PACKAGE, directory=True, missing=True)
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        fail("INVALID_TARGET", "Source and target must be separate directories.")
    content = source_files(source)
    hashes = {name: digest(data) for name, data in content.items()}
    result = {"source": str(source), "target": str(target), "sha256": hashes,
              "backup": None, "changed": False}
    if check:
        if not target.is_dir() or inventory(target) != hashes:
            fail("HASH_MISMATCH", "Installed files differ from the validated source.")
        return result
    backups = inside(skills.parent, ".context-keeper-deploy", directory=True, missing=True)
    backups.mkdir(exist_ok=True)
    checked_node(backups, directory=True)
    with writer_lock(backups):
        exists = checked_node(target, directory=True, missing=True) is not None
        if exists and inventory(target) == hashes:
            return result
        if exists:
            source_files(target)  # Protect an unrelated directory with a coincidentally identical name.
        with staged_dir(backups, "stage-") as stage:
            for name, data in content.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            if inventory(stage) != hashes:
                fail("HASH_MISMATCH", "Staged files differ from the validated source.")
            backup = backups / f"{PACKAGE}-{uuid4()}"
            if exists:
                os.rename(target, backup)
            try:
                os.rename(stage, target)
                if inventory(target) != hashes:
                    fail("HASH_MISMATCH", "Installed files differ from the validated source.")
            except (KeeperError, OSError):
                if target.is_dir():
                    os.rename(target, stage)
                if exists:
                    os.rename(backup, target)
                raise
            result.update(changed=True, backup=str(backup) if exists else None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--skills-dir", required=True, help="Existing skills directory verified for the host")
    parser.add_argument("--check", action="store_true", help="Only verify the installed file inventory and hashes")
    args = parser.parse_args()
    try:
        result = deploy(absolute_dir(args.source), absolute_dir(args.skills_dir), args.check)
        print(json.dumps({"ok": True, **result}, ensure_ascii=True))
        return 0
    except KeeperError as error:
        print(json.dumps({"ok": False, "code": error.code, "message": str(error)}))
    except (OSError, UnicodeError):
        print(json.dumps({"ok": False, "code": "DEPLOY_IO_ERROR",
                          "message": "Deployment could not complete. Inspect any existing deployment backup."}))
    return 4


if __name__ == "__main__":
    sys.exit(main())
