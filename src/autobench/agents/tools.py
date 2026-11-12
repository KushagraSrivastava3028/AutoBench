"""The tools a Mode A agent is given.

Three, deliberately. ``bash`` alone is sufficient — everything else is
expressible as a shell command — but ``file_edit`` and ``file_read`` exist
because forcing a model to write files through heredocs burns tokens, produces
quoting bugs that look like reasoning failures, and makes the trajectory
unreadable. Giving structured file access removes a confound rather than adding
a capability.
"""

from __future__ import annotations

from typing import Any

from autobench.executor.base import ExecResult, Sandbox
from autobench.models.types import ToolSpec

MAX_FILE_BYTES = 256 * 1024

BASH = ToolSpec(
    name="bash",
    description=(
        "Run a shell command inside the task container and return its stdout, "
        "stderr and exit code. The working directory persists between calls. "
        "There is no network."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run."},
            "timeout_sec": {
                "type": "integer",
                "description": "Per-command timeout in seconds (default 120, max 600).",
            },
        },
        "required": ["command"],
    },
)

FILE_READ = ToolSpec(
    name="file_read",
    description="Read a UTF-8 text file from the container. Prefer this over `cat`.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute path, e.g. /app/retry.py"},
        },
        "required": ["path"],
    },
)

FILE_EDIT = ToolSpec(
    name="file_edit",
    description=(
        "Write a file in the container. Use mode='replace' to overwrite the whole "
        "file, or mode='substitute' with old_text/new_text for a targeted change. "
        "A substitution whose old_text is absent or ambiguous fails without "
        "modifying anything."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "mode": {"type": "string", "enum": ["replace", "substitute"]},
            "content": {"type": "string", "description": "Full contents, for mode='replace'."},
            "old_text": {"type": "string", "description": "Exact text to find."},
            "new_text": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "mode"],
    },
)

DEFAULT_TOOLS = [BASH, FILE_READ, FILE_EDIT]


async def dispatch(sandbox: Sandbox, name: str, arguments: dict[str, Any]) -> str:
    """Execute one tool call and return the string the model sees."""
    if name == "bash":
        return await _bash(sandbox, arguments)
    if name == "file_read":
        return await _file_read(sandbox, arguments)
    if name == "file_edit":
        return await _file_edit(sandbox, arguments)
    return f"error: unknown tool {name!r}"


async def _bash(sandbox: Sandbox, arguments: dict[str, Any]) -> str:
    command = str(arguments.get("command", "")).strip()
    if not command:
        return "error: 'command' is required"
    timeout = min(max(int(arguments.get("timeout_sec") or 120), 1), 600)
    result: ExecResult = await sandbox.exec(command, timeout=timeout)
    if result.timed_out:
        return (
            f"command timed out after {timeout}s (exit 124)\n"
            f"{result.combined()}\n"
            "Hint: background long-running processes or raise timeout_sec."
        )
    return f"exit {result.exit_code}\n{result.combined()}"


async def _file_read(sandbox: Sandbox, arguments: dict[str, Any]) -> str:
    path = str(arguments.get("path", "")).strip()
    if not path:
        return "error: 'path' is required"
    try:
        data = await sandbox.read_file(path)
    except Exception as exc:
        return f"error: {exc}"
    if len(data) > MAX_FILE_BYTES:
        return (
            f"error: {path} is {len(data)} bytes, over the {MAX_FILE_BYTES} limit. "
            "Read a slice with sed -n instead."
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"error: {path} is not UTF-8 text ({len(data)} bytes)"
    # Line numbers make a follow-up substitution far more likely to be exact.
    return "\n".join(f"{i:5d}\t{line}" for i, line in enumerate(text.splitlines(), 1))


async def _file_edit(sandbox: Sandbox, arguments: dict[str, Any]) -> str:
    path = str(arguments.get("path", "")).strip()
    mode = str(arguments.get("mode", "replace"))
    if not path:
        return "error: 'path' is required"

    if mode == "replace":
        content = arguments.get("content")
        if content is None:
            return "error: mode='replace' requires 'content'"
        await sandbox.write_file(path, str(content))
        return f"wrote {len(str(content))} bytes to {path}"

    if mode == "substitute":
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if old_text is None or new_text is None:
            return "error: mode='substitute' requires 'old_text' and 'new_text'"
        try:
            current = (await sandbox.read_file(path)).decode("utf-8")
        except Exception as exc:
            return f"error: {exc}"

        occurrences = current.count(str(old_text))
        if occurrences == 0:
            return f"error: old_text not found in {path}; nothing was modified"
        if occurrences > 1:
            # Ambiguity is the caller's to resolve. Guessing which of four
            # identical strings was meant is how a targeted edit corrupts a file.
            return (
                f"error: old_text appears {occurrences} times in {path}; nothing was "
                "modified. Include more surrounding context to make it unique."
            )
        updated = current.replace(str(old_text), str(new_text), 1)
        await sandbox.write_file(path, updated)
        return f"substituted 1 occurrence in {path}"

    return f"error: unknown mode {mode!r}; use 'replace' or 'substitute'"
