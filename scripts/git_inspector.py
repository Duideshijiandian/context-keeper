"""Read a conventional local Git checkout without resolving external Git metadata."""

import os
from pathlib import Path
import re
import subprocess

from sanitizer import (
    EXCLUDED, KeeperError, bounded_read, checked_node, digest, excluded, fail, has_secret, inside,
    json_bytes, relative_path,
)


OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
GIT_SCOPE = [
    "Explicit project root only",
    "Installed Git executable from absolute PATH entries outside the project",
    "Local .git/config, HEAD, index, refs and objects",
    "Working tree status with Context Keeper and sensitive paths excluded",
    "Submodule pointers only, with submodule worktrees ignored",
]
CORE_KEYS = {
    "repositoryformatversion", "filemode", "bare", "logallrefupdates", "symlinks",
    "ignorecase", "precomposeunicode", "autocrlf", "eol", "safecrlf",
    "protectntfs", "protecthfs", "quotepath", "longpaths", "abbrev",
    "checkstat", "trustctime", "sharedrepository",
}


def git_executable(root):
    filename = "git.exe" if os.name == "nt" else "git"
    for directory in os.get_exec_path():
        entry = Path(directory.strip('"'))
        if not entry.is_absolute() or entry.is_relative_to(root):
            continue
        candidate = entry / filename
        if candidate.is_file() and os.access(candidate, os.X_OK):
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root):
                return str(resolved)
    fail("GIT_UNAVAILABLE", "No installed Git executable was found outside the project.")


def metadata_tree(path):
    count = 0
    pending = [path]
    while pending:
        parent = pending.pop()
        for child in parent.iterdir():
            # Git does not need these directories for the commands below.
            if parent == path and child.name.casefold() in {"modules", "hooks", "lfs", "logs"}:
                continue
            count += 1
            if count > 100_000:
                fail("GIT_LIMIT", "Git metadata exceeds the inspection limit.")
            if child.name.casefold() in {"alternates", "http-alternates", "commondir",
                                         "gitdir", "config.worktree", "worktrees"}:
                fail("GIT_BOUNDARY", "Git metadata may redirect access outside the checkout.")
            checked_node(child)
            if child.is_dir():
                pending.append(child)


def worktree_boundary(root, exclusions):
    # Inspect names and node types only. Git sees none of the excluded paths in its pathspec.
    pending = [root]
    count = 0
    while pending:
        parent = pending.pop()
        with os.scandir(parent) as entries:
            for entry in entries:
                count += 1
                if count > 150_000:
                    fail("GIT_LIMIT", "Worktree exceeds the boundary inspection limit.")
                path = Path(entry.path)
                rel = path.relative_to(root).as_posix()
                if excluded(rel, exclusions):
                    continue
                checked_node(path)
                if entry.is_dir(follow_symlinks=False):
                    if checked_node(path / ".git", missing=True) is not None:
                        # A nested checkout can contain its own configuration or redirects.
                        fail("GIT_BOUNDARY", "Nested Git checkouts require separate inspection.")
                    pending.append(path)


def inspect(root, exclusions=()):
    result = dict(git_available=False, git_branch=None, git_head=None,
                  working_tree_status="unavailable", fingerprint=None, changes=[],
                  recent_commits=[], dirty_count=0, read_scope=["Explicit project root only"],
                  warnings=[])
    try:
        metadata = root / ".git"
        if checked_node(metadata, missing=True) is None:
            return result
        result["read_scope"] = GIT_SCOPE.copy()
        checked_node(metadata, directory=True)
        executable = git_executable(root)
        metadata_tree(metadata)
        worktree_boundary(root, exclusions)
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1",
            GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1",
            GIT_NO_LAZY_FETCH="1", GIT_ALLOW_PROTOCOL="", LC_ALL="C",
        )
        checkout_defaults = []

        def run(args, ok=(0,), data=None, config=False):
            if config:
                prefix = ["--no-pager"]
                cwd = Path(executable).parent
            else:
                prefix = [
                    "--no-pager", "--git-dir", str(metadata), "--work-tree", str(root),
                    "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
                    "-c", "core.attributesFile=" + os.devnull, "-c", "core.excludesFile=" + os.devnull,
                    "-c", "core.hooksPath=" + os.devnull, "-c", "core.splitIndex=false",
                    "-c", "core.ignoreStat=false",
                    *checkout_defaults,
                ]
                cwd = root
            proc = subprocess.run(
                [executable, *prefix, *args], cwd=cwd, env=env, input=data,
                capture_output=True, timeout=15, check=False,
            )
            if proc.returncode not in ok or len(proc.stdout) > 16 * 1024 * 1024:
                fail("GIT_COMMAND_FAILED", "Git read failed or exceeded its output limit.")
            return proc

        config = bounded_read(inside(metadata, "config"), 1024 * 1024)
        entries = run(["config", "--file", "-", "--no-includes", "--null", "--list"],
                      data=config, config=True).stdout.rstrip(b"\0").split(b"\0")
        local_keys = set()
        for entry in filter(None, entries):
            key, _, value = entry.partition(b"\n")
            key = key.decode("utf-8", "strict").lower()
            local_keys.add(key)
            section, _, option = key.partition(".")
            allowed = (
                (section == "core" and option in CORE_KEYS)
                or section in {"user", "remote", "branch", "pull", "push", "init", "submodule",
                               "commit", "fetch", "tag"}
                or key == "extensions.objectformat" and value in {b"sha1", b"sha256"}
            )
            if not allowed or key == "core.bare" and value.lower() != b"false":
                fail("GIT_BOUNDARY", "Local Git configuration contains an unsupported option.")
        if os.name == "nt" and "core.autocrlf" not in local_keys:
            # Git for Windows commonly normalizes checkout CRLF through its system config.
            checkout_defaults.extend(["-c", "core.autocrlf=true"])

        branch = run(["symbolic-ref", "--quiet", "--short", "HEAD"], ok=(0, 1))
        name = branch.stdout.decode("utf-8", "strict").strip() or None
        if name and has_secret(name):
            name = "[REDACTED]"
        head = run(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], ok=(0, 1))
        oid = head.stdout.decode("ascii").strip() if head.returncode == 0 else None
        if oid and not OID.fullmatch(oid):
            fail("GIT_COMMAND_FAILED", "Git returned an invalid commit identifier.")
        if not oid:
            # Distinguish an unborn branch from a corrupt detached or dangling HEAD.
            sym = run(["symbolic-ref", "--quiet", "HEAD"], ok=(0, 1))
            if sym.returncode or not sym.stdout.startswith(b"refs/heads/"):
                fail("GIT_COMMAND_FAILED", "Git HEAD cannot be resolved.")
            ref = sym.stdout.decode("utf-8", "strict").strip()
            if run(["show-ref", "--verify", "--quiet", ref], ok=(0, 1)).returncode == 0:
                fail("GIT_COMMAND_FAILED", "Git branch points to an unreadable commit.")

        pathspec = [":(top)**"]
        for pattern in (*EXCLUDED, ".git"):
            for glob in (f"**/{pattern}", f"**/{pattern}/**"):
                pathspec.append(":(top,exclude,glob,icase)" + glob)
        pathspec.extend(":(top,exclude,literal,icase)" + item for item in exclusions)
        status = run(["status", "--porcelain=v1", "-z", "--untracked-files=all",
                      "--ignore-submodules=all", "--no-renames", "--", *pathspec]).stdout
        index = run(["ls-files", "--stage", "-z", "--", *pathspec]).stdout
        entries = []
        dirty = []
        byte_budget = 64 * 1024 * 1024
        incomplete = False
        for item in filter(None, status.split(b"\0")):
            code = item[:2].decode("ascii")
            rel = item[3:].decode("utf-8", "strict")
            relative_path(rel)
            if excluded(rel, exclusions):
                continue
            identity = None
            try:
                path = inside(root, rel)
                file_bytes = bounded_read(path, min(byte_budget, 8 * 1024 * 1024))
                identity = digest(file_bytes)
                byte_budget -= len(file_bytes)
            except FileNotFoundError:
                pass  # Deleted or staged-only path.
            except (KeeperError, OSError):
                incomplete = True
            entries.append((code, rel, identity))
            dirty.append({"status": code, "path": "[REDACTED]" if has_secret(rel) else rel})
        history = run(["rev-list", "--max-count=20", oid]).stdout.decode("ascii").splitlines() if oid else []
        if any(not OID.fullmatch(item) for item in history):
            fail("GIT_COMMAND_FAILED", "Git returned invalid history.")
        result.update(
            git_available=True, git_branch=name, git_head=oid,
            working_tree_status="dirty" if dirty else "clean",
            fingerprint=None if incomplete else digest(json_bytes([digest(index), sorted(entries)])),
            changes=dirty[:50], dirty_count=len(dirty), recent_commits=history,
        )
        if incomplete:
            result["warnings"].append("GIT_FINGERPRINT_INCOMPLETE")
        return result
    except (KeeperError, OSError, UnicodeError, subprocess.SubprocessError) as error:
        result.update(working_tree_status="unknown", warnings=[
            error.code if isinstance(error, KeeperError) else "GIT_READ_FAILED"
        ])
        return result
