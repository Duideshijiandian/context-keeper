"""Emit the runtime checksum list and rollback steps attached to one release."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from sanitizer import KeeperError, absolute_dir, bounded_read, digest
from sync_deploy import FILES


def checksums(source):
    """SHA-256 of every runtime file, keyed by its repository-relative path."""
    return {name: digest(bounded_read(source / name)) for name in FILES}


def render(version, previous, commit, hashes, generated):
    """One plain-text release attachment: checksums, how to verify, how to roll back."""
    lines = [
        f"Context Keeper v{version} runtime checksums",
        f"commit: {commit or 'unknown'}",
        f"generated: {generated}",
        f"files: {len(FILES)}",
        "",
    ]
    lines += [f"{hashes[name]}  {name}" for name in FILES]
    lines += [
        "",
        f"Verify (run from the repository root of v{version}):",
        f"  sha256sum -c SHA256SUMS-v{version}.txt            # Git Bash",
        "  Get-FileHash -Algorithm SHA256 <file>              # PowerShell, one file at a time",
        "",
        f"Rollback to v{previous} (an installed skill is not touched by git alone):",
        f"  git -C <working-copy> checkout v{previous}",
        "  python -X utf8 -B scripts/sync_deploy.py --skills-dir \"$env:USERPROFILE/.dsh/skills\"",
        "  python -X utf8 -B scripts/sync_deploy.py --skills-dir \"$env:USERPROFILE/.dsh/skills\" --check",
        "  python -X utf8 -B scripts/sync_deploy.py --skills-dir \"$env:USERPROFILE/.agents/skills\"",
        "  python -X utf8 -B scripts/sync_deploy.py --skills-dir \"$env:USERPROFILE/.agents/skills\" --check",
        "A failed deploy leaves the previous install under <skills-parent>/.context-keeper-deploy/.",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--version", required=True, help="Release version without the leading v, e.g. 1.1.3")
    parser.add_argument("--previous", required=True, help="Previous tag to roll back to, e.g. 1.1.2")
    parser.add_argument("--commit", default="", help="Release commit, when not read from Git")
    parser.add_argument("--output", help="Also write the report to this path")
    args = parser.parse_args()
    try:
        hashes = checksums(absolute_dir(args.source))
        text = render(args.version.lstrip("v"), args.previous.lstrip("v"), args.commit, hashes,
                      datetime.now(timezone.utc).isoformat(timespec="seconds"))
        if args.output:
            target = Path(args.output).expanduser()
            target.write_text(text, encoding="utf-8")
            print(json.dumps({"ok": True, "output": str(target), "files": len(hashes)}, ensure_ascii=True))
            return 0
        sys.stdout.write(text)
        return 0
    except KeeperError as error:
        print(json.dumps({"ok": False, "code": error.code, "message": str(error)}, ensure_ascii=True))
    except (OSError, UnicodeError):
        print(json.dumps({"ok": False, "code": "CHECKSUM_IO_ERROR",
                          "message": "The release checksums could not be read or written."}, ensure_ascii=True))
    return 4


if __name__ == "__main__":
    sys.exit(main())
