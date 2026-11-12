"""Content-addressed task versions.

``task_version.content_hash`` is a digest over the *canonicalised* task
directory. Canonical means: sorted relative POSIX paths, newline-normalised
text, both path and bytes length-prefixed into the hash. Two checkouts of the
same task on Windows and Linux must produce the same hash, or every Windows
author would create a phantom new version on first push.

What is deliberately excluded: ``README.md`` and author notes (they change the
prose, not the task), plus the usual editor and VCS noise. What is deliberately
*included*: ``tests/`` and ``solution/``. Changing the grader changes the
meaning of the score, so it has to change the version.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

#: Excluded from the hash: changing these cannot change a trial's outcome.
IGNORED_NAMES = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
        "README.md",
        ".gitignore",
        ".gitattributes",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".git",
        ".venv",
        "node_modules",
    }
)
IGNORED_SUFFIXES = frozenset({".pyc", ".pyo", ".log", ".swp", ".tmp", ".orig", ".rej"})

#: Files hashed as text (newline-normalised). Everything else is hashed as bytes.
TEXT_SUFFIXES = frozenset(
    {
        ".toml",
        ".md",
        ".txt",
        ".py",
        ".sh",
        ".bash",
        ".yaml",
        ".yml",
        ".json",
        ".cfg",
        ".ini",
        ".js",
        ".ts",
        ".go",
        ".rs",
        ".c",
        ".h",
        ".cpp",
        ".java",
        ".sql",
        ".csv",
        ".html",
        ".css",
        ".dockerfile",
        "",
    }
)


def _is_ignored(path: Path, root: Path) -> bool:
    if path.suffix.lower() in IGNORED_SUFFIXES:
        return True
    return any(part in IGNORED_NAMES for part in path.relative_to(root).parts)


def _canonical_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    name = path.name.lower()
    if path.suffix.lower() in TEXT_SUFFIXES or name in {"dockerfile", "makefile"}:
        # CRLF -> LF, and strip a UTF-8 BOM. Git's autocrlf and Windows editors
        # both introduce these, and neither changes what the task does.
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return raw
        return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return raw


def iter_task_files(root: Path) -> list[Path]:
    """Every hashed file, sorted by POSIX relative path."""
    files = [p for p in root.rglob("*") if p.is_file() and not _is_ignored(p, root)]
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def hash_task_directory(root: Path) -> str:
    """``sha256:<hex>`` over the canonicalised directory."""
    root = root.resolve()
    hasher = hashlib.sha256()
    hasher.update(b"autobench-task-v1\n")
    for path in iter_task_files(root):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        payload = _canonical_bytes(path)
        # Length-prefixed so ("ab","c") and ("a","bc") cannot collide.
        hasher.update(len(rel).to_bytes(8, "big"))
        hasher.update(rel)
        hasher.update(len(payload).to_bytes(8, "big"))
        hasher.update(payload)
    return f"sha256:{hasher.hexdigest()}"


def file_manifest(root: Path) -> dict[str, str]:
    """Per-file digests. Shown in the UI so an author can see *what* changed."""
    root = root.resolve()
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(_canonical_bytes(path)).hexdigest()[:16]
        for path in iter_task_files(root)
    }


def image_tag(task_id: str, content_hash: str, namespace: str = "autobench") -> str:
    """Deterministic image tag: the same task content always resolves to the same tag.

    This is optimisation O1 — build once per ``content_hash``, never rebuild.
    """
    short = content_hash.removeprefix("sha256:")[:16]
    return f"{namespace}/task-{task_id}:{short}"
