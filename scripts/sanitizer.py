"""Conservative path boundaries and secret screening. Never echo rejected input."""

import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import stat


MAX_JSON = 8 * 1024 * 1024
EXCLUDED = (
    ".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "*.ppk", "*.jks",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*", ".ssh", ".aws", ".gnupg",
    ".git-credentials", ".netrc", "_netrc", ".npmrc", ".pypirc", ".docker",
    "*credential*", "*password*", "*token*", "*secret*", ".context-keeper*",
    "node_modules", ".venv", ".tox", ".pytest_cache", "__pycache__",
)
RESERVED = re.compile(r"(?i)^(?:con|conin\$|conout\$|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(?:\.|$)")
SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?:\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{12,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}))"
    r"|\b(?:Bearer|Basic)[ \t]+[A-Za-z0-9+/_.=-]{12,}"
    r"|(?:[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"
    r"|(?<![a-z0-9])(?:[a-z0-9_-]*(?:password|passwd|secret|token|api[_-]?key|authorization))"
    r"[\"']?\s*[:=]\s*[\"']?(?!<redacted>|\$\{|\$env:)[^\s,\"'\]}]{4,}",
    re.IGNORECASE,
)


class KeeperError(Exception):
    def __init__(self, code, message, candidates=None):
        super().__init__(message)
        self.code = code
        self.candidates = candidates


def fail(code, message, candidates=None):
    raise KeeperError(code, message, candidates)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            + "\n").encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def excluded(relative, extra=()):
    parts = relative.casefold().split("/")
    return (
        ".git" in parts
        or any(fnmatch.fnmatchcase(part, pattern) for part in parts for pattern in EXCLUDED)
        or any(relative.casefold() == path.casefold()
               or relative.casefold().startswith(path.casefold() + "/") for path in extra)
    )


def relative_path(value):
    if not isinstance(value, str):
        fail("UNSAFE_PATH", "A path must be a relative string.")
    parts = value.split("/")
    if any(
        not part or part in (".", "..") or part.endswith((".", " "))
        or re.search(r'[\x00-\x1f\x7f\\:<>"|?*]|~\d+(?:\.|$)', part) or RESERVED.match(part)
        for part in parts
    ):
        fail("UNSAFE_PATH", "Use a portable project-relative path with forward slashes.")
    return parts


def checked_node(path, *, directory=None, missing=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return None
        raise
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        fail("UNSAFE_PATH", "A symbolic link or reparse point is outside the supported boundary.")
    is_directory = stat.S_ISDIR(info.st_mode)
    is_file = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    if (directory is True and not is_directory or directory is False and not is_file
            or not (is_directory or is_file)):
        fail("UNSAFE_PATH", "Unexpected file type or hard link.")
    return info


def absolute_dir(value):
    path = Path(os.path.abspath(value))
    if path.anchor.startswith("\\\\"):
        fail("UNSAFE_PATH", "Network and device paths are unsupported.")
    for component in (*reversed(path.parents), path):
        checked_node(component, directory=True)
    return path


def inside(root, relative, *, directory=False, missing=False):
    parts = relative_path(relative)
    path = root
    for part in parts[:-1]:
        path /= part
        if checked_node(path, directory=True, missing=missing) is None:
            return root.joinpath(*parts)
    path /= parts[-1]
    checked_node(path, directory=directory, missing=missing)
    return path


def bounded_read(path, limit=MAX_JSON):
    before = checked_node(path, directory=False)
    if before.st_size > limit:
        fail("SIZE_LIMIT", "A file exceeds the supported size limit.")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            fail("FILE_CHANGED", "A file changed while it was opened.")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(data) > limit or (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        fail("FILE_CHANGED", "A file changed while it was read.")
    return data


def has_secret(value):
    if isinstance(value, str):
        return bool(SECRET.search(value))
    if isinstance(value, list):
        return any(map(has_secret, value))
    if isinstance(value, dict):
        return any(has_secret(key) or has_secret(item) for key, item in value.items())
    return False


def screen(value):
    if has_secret(value):
        fail("SENSITIVE_DATA", "Possible secret detected. Remove it from the input and retry.")


def parse_json(data):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            fail("INVALID_JSON", "Duplicate JSON keys.")
        return result

    def invalid_constant(_):
        fail("INVALID_JSON", "Nonfinite JSON values are unsupported.")

    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique,
                           parse_constant=invalid_constant)
        screen(value)
        return value
    except (UnicodeError, ValueError, RecursionError):
        fail("INVALID_JSON", "Expected valid UTF-8 JSON within the nesting limit.")
