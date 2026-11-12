#!/usr/bin/env python3
"""Rewrite every task's base image to a digest pin.

A floating tag means the environment drifts between runs, and the score moves
for reasons that have nothing to do with the agent — which is control #2 in the
determinism ladder and the reason the ``image`` gate refuses an unpinned FROM.

Digests cannot be invented, so this asks the local Docker daemon what a tag
currently resolves to and writes that down:

    python scripts/pin_base_images.py tasks tests/fixtures/tasks
    python scripts/pin_base_images.py tasks --check     # CI: fail if unpinned

Pinning is a deliberate act. Re-running this bumps every task's content hash,
which creates a new task version — exactly as it should, because the
environment really did change.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

FROM_LINE = re.compile(r"^(\s*FROM\s+)(\S+?)(?:@(sha256:[0-9a-f]+))?(\s+AS\s+\S+)?\s*$", re.I)


def resolve_digest(reference: str) -> str | None:
    """Ask the daemon for a tag's digest, pulling it if necessary."""
    for attempt in ("inspect", "pull-then-inspect"):
        if attempt == "pull-then-inspect":
            print(f"    pulling {reference}…", file=sys.stderr)
            if subprocess.run(["docker", "pull", "--quiet", reference],  # noqa: S603, S607
                              capture_output=True).returncode != 0:
                return None
        probe = subprocess.run(  # noqa: S603, S607
            ["docker", "image", "inspect", reference, "--format", "{{json .RepoDigests}}"],
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            continue
        try:
            digests = json.loads(probe.stdout.strip() or "[]")
        except json.JSONDecodeError:
            continue
        for entry in digests:
            if "@" in entry:
                return str(entry).split("@", 1)[1]
    return None


def pin_dockerfile(path: Path, *, check: bool) -> tuple[bool, list[str]]:
    """Return ``(changed, notes)``."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    notes: list[str] = []
    changed = False

    for index, line in enumerate(lines):
        match = FROM_LINE.match(line.rstrip("\n"))
        if not match:
            continue
        prefix, reference, existing, alias = match.groups()
        if reference.lower() == "scratch" or reference.startswith("$"):
            continue

        if check:
            if not existing:
                notes.append(f"{path}:{index + 1}: {reference} is not pinned")
            continue

        digest = resolve_digest(reference)
        if digest is None:
            notes.append(f"{path}:{index + 1}: could not resolve {reference}")
            continue
        if digest == existing:
            continue

        lines[index] = f"{prefix}{reference}@{digest}{alias or ''}\n"
        notes.append(f"{path}:{index + 1}: {reference} -> {digest[:19]}…")
        changed = True

    if changed:
        path.write_text("".join(lines), encoding="utf-8", newline="\n")
    return changed, notes


def pin_task_toml(path: Path, digest: str) -> None:
    """Mirror the digest into ``determinism.pinned_base_digest``."""
    text = path.read_text(encoding="utf-8")
    if "pinned_base_digest" in text:
        text = re.sub(
            r'pinned_base_digest\s*=\s*"[^"]*"',
            f'pinned_base_digest      = "{digest}"',
            text,
        )
        path.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report unpinned bases and exit non-zero; write nothing.",
    )
    args = parser.parse_args()

    if not args.check and subprocess.run(  # noqa: S603, S607
        ["docker", "info"], capture_output=True
    ).returncode != 0:
        print("a running Docker daemon is required to resolve digests", file=sys.stderr)
        return 2

    all_notes: list[str] = []
    touched = 0

    for root in args.roots:
        for task_toml in sorted(root.rglob("task.toml")):
            task_dir = task_toml.parent
            dockerfile = task_dir / "environment" / "Dockerfile"
            if not dockerfile.is_file():
                continue

            print(f"  {task_dir.name}")
            changed, notes = pin_dockerfile(dockerfile, check=args.check)
            all_notes.extend(notes)
            if changed:
                touched += 1
                match = FROM_LINE.match(
                    next(
                        line
                        for line in dockerfile.read_text(encoding="utf-8").splitlines()
                        if FROM_LINE.match(line)
                    )
                )
                if match and match.group(3):
                    pin_task_toml(task_toml, match.group(3))

    for note in all_notes:
        print(note, file=sys.stderr)

    if args.check:
        if all_notes:
            print(f"\n{len(all_notes)} unpinned base image(s)", file=sys.stderr)
            return 1
        print("every base image is pinned by digest")
        return 0

    print(f"\npinned {touched} task image(s)")
    if touched:
        print(
            "Each pinned task now has a new content hash, and therefore a new task "
            "version. That is correct: the environment changed."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
