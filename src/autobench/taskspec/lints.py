"""Static lints: the gates that need only the task directory, no container.

Three of these encode the failure modes that turn grading bugs into "the model
got worse":

``state_based``
    Walks the test AST and flags assertions made against captured agent output.
    Tests that read the agent's stdout grade *prose*. This is the single most
    common authoring mistake and it is why a 42% score became 95% after a
    grading fix on CORE-Bench.

``tolerance``
    Every float comparison must declare a tolerance. Penalising ``96.12``
    because the grader wanted ``96.124991`` is a lint error here, not a model
    result.

``leak``
    n-gram overlap between ``instruction.md`` and ``tests/``. If the instruction
    quotes the test, the task measures reading comprehension of the grader.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Variable / attribute names that hold agent-produced text rather than world state.
AGENT_OUTPUT_NAMES = frozenset(
    {
        "agent_stdout",
        "agent_output",
        "agent_stderr",
        "agent_response",
        "agent_log",
        "agent_transcript",
        "model_output",
        "model_response",
        "completion",
        "transcript",
        "trajectory",
        "stdout_capture",
        "console_output",
        "last_message",
    }
)

#: Helpers that make a float comparison safe.
TOLERANT_CALLS = frozenset(
    {"isclose", "approx", "assertAlmostEqual", "allclose", "assert_allclose", "almost_equal"}
)

_FLOAT_LITERAL = re.compile(r"^\d+\.\d+$")


@dataclass(slots=True)
class LintFinding:
    """One actionable complaint, anchored to a file and line."""

    rule: str
    file: str
    line: int
    message: str
    remediation: str
    severity: str = "error"  # error | warning

    def render(self) -> str:
        return f"{self.file}:{self.line}: [{self.rule}] {self.message}"


@dataclass(slots=True)
class LintResult:
    findings: list[LintFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[LintFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: LintResult) -> None:
        self.findings.extend(other.findings)


# ---------------------------------------------------------------------------
# state-based grading
# ---------------------------------------------------------------------------


class _StateBasedVisitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.findings: list[LintFinding] = []

    def visit_Assert(self, node: ast.Assert) -> None:
        for name in self._names_in(node.test):
            if name in AGENT_OUTPUT_NAMES:
                self.findings.append(
                    LintFinding(
                        rule="state-based",
                        file=self.filename,
                        line=node.lineno,
                        message=(
                            f"assertion reads {name!r}, which is agent-produced text. "
                            "This grades what the agent said, not what it did."
                        ),
                        remediation=(
                            "Assert against the final container state instead: read the "
                            "file it was supposed to write, query the database it was "
                            "supposed to update, or check the mock SMTP log."
                        ),
                    )
                )
                break
        self.generic_visit(node)

    def _names_in(self, node: ast.AST) -> set[str]:
        found: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                found.add(child.id)
            elif isinstance(child, ast.Attribute):
                found.add(child.attr)
        return found


def lint_state_based(tests: dict[str, str]) -> LintResult:
    result = LintResult()
    for filename, source in tests.items():
        if not filename.endswith(".py"):
            result.extend(_lint_shell_state_based(filename, source))
            continue
        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError as exc:
            result.findings.append(
                LintFinding(
                    rule="state-based",
                    file=filename,
                    line=exc.lineno or 1,
                    message=f"test file does not parse: {exc.msg}",
                    remediation="Fix the syntax error; an unparseable test cannot grade anything.",
                )
            )
            continue
        visitor = _StateBasedVisitor(filename)
        visitor.visit(tree)
        result.findings.extend(visitor.findings)
    return result


_SHELL_OUTPUT_GREP = re.compile(
    r"grep\s+(?:-\w+\s+)*[\"'][^\"']+[\"']\s+.*(?:agent[_\-]?(?:out|output|stdout|log)|transcript)",
    re.IGNORECASE,
)


def _lint_shell_state_based(filename: str, source: str) -> LintResult:
    result = LintResult()
    for lineno, line in enumerate(source.splitlines(), start=1):
        if _SHELL_OUTPUT_GREP.search(line):
            result.findings.append(
                LintFinding(
                    rule="state-based",
                    file=filename,
                    line=lineno,
                    message="test greps the agent's captured output rather than the filesystem.",
                    remediation="Check the artefact the agent was asked to produce instead.",
                )
            )
    return result


# ---------------------------------------------------------------------------
# tolerance
# ---------------------------------------------------------------------------


class _ToleranceVisitor(ast.NodeVisitor):
    """Flag bare float equality. ``==`` on floats is a coin toss with extra steps."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.findings: list[LintFinding] = []

    def visit_Compare(self, node: ast.Compare) -> None:
        if any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops) and self._has_float(node):
            if not self._inside_tolerant_call(node):
                self.findings.append(
                    LintFinding(
                        rule="tolerance",
                        file=self.filename,
                        line=node.lineno,
                        message=(
                            "float compared with == / != and no declared tolerance. "
                            "A correctly-rounded answer would be graded wrong."
                        ),
                        remediation=(
                            "Use math.isclose(actual, expected, rel_tol=1e-6) or "
                            "pytest.approx(expected, rel=1e-6)."
                        ),
                    )
                )
        self.generic_visit(node)

    def _has_float(self, node: ast.Compare) -> bool:
        operands = [node.left, *node.comparators]
        for operand in operands:
            if isinstance(operand, ast.Constant) and isinstance(operand.value, float):
                return True
            if isinstance(operand, ast.Call) and _call_name(operand) == "float":
                return True
        return False

    def _inside_tolerant_call(self, node: ast.Compare) -> bool:
        for operand in [node.left, *node.comparators]:
            if isinstance(operand, ast.Call) and _call_name(operand) in TOLERANT_CALLS:
                return True
        return False


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def lint_tolerance(tests: dict[str, str]) -> LintResult:
    result = LintResult()
    for filename, source in tests.items():
        if not filename.endswith(".py"):
            continue
        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError:
            continue  # already reported by lint_state_based
        visitor = _ToleranceVisitor(filename)
        visitor.visit(tree)
        result.findings.extend(visitor.findings)
    return result


# ---------------------------------------------------------------------------
# instruction / test leakage
# ---------------------------------------------------------------------------


def _ngrams(text: str, n: int = 8) -> set[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    if len(words) < n:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def ngram_overlap(instruction: str, tests: dict[str, str], n: int = 8) -> float:
    """Fraction of the instruction's n-grams that also appear in the tests."""
    instruction_grams = _ngrams(instruction, n)
    if not instruction_grams:
        return 0.0
    test_grams: set[str] = set()
    for source in tests.values():
        test_grams |= _ngrams(source, n)
    return len(instruction_grams & test_grams) / len(instruction_grams)


def lint_leak(instruction: str, tests: dict[str, str], threshold: float = 0.15) -> LintResult:
    result = LintResult()
    overlap = ngram_overlap(instruction, tests)
    if overlap >= threshold:
        result.findings.append(
            LintFinding(
                rule="leak",
                file="instruction.md",
                line=1,
                message=(
                    f"instruction.md shares {overlap:.0%} of its 8-grams with tests/ "
                    f"(limit {threshold:.0%}). The task leaks its own grader."
                ),
                remediation=(
                    "Describe the goal in the author's own words. If the agent can read "
                    "the assertion out of the prompt, the task measures transcription."
                ),
            )
        )
    return result


# ---------------------------------------------------------------------------
# Dockerfile / image hygiene
# ---------------------------------------------------------------------------

_FROM = re.compile(r"^\s*FROM\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_UNPINNED_INSTALL = re.compile(
    r"(pip\s+install(?!.*(?:--no-index|-r\s|\brequirements))|apt-get\s+install|npm\s+install\b"
    r"|go\s+get\b|cargo\s+install\b)",
    re.IGNORECASE,
)
_NETWORK_FETCH = re.compile(r"\b(curl|wget|git\s+clone)\b", re.IGNORECASE)


def lint_dockerfile(dockerfile: Path, *, require_digest: bool = True) -> LintResult:
    """Base images pinned by digest; no unpinned package pulls at build time.

    Both are control #2 and #4 from PRD §10: an apt mirror that moves under you
    is indistinguishable, from the outside, from a model that got worse.
    """
    result = LintResult()
    if not dockerfile.is_file():
        result.findings.append(
            LintFinding(
                rule="image",
                file=str(dockerfile),
                line=1,
                message="Dockerfile not found",
                remediation="Every task needs environment/Dockerfile.",
            )
        )
        return result

    source = dockerfile.read_text(encoding="utf-8", errors="replace")
    name = dockerfile.name

    bases = _FROM.findall(source)
    if not bases:
        result.findings.append(
            LintFinding(
                rule="image",
                file=name,
                line=1,
                message="no FROM instruction",
                remediation="Start from a digest-pinned base image.",
            )
        )
    for base in bases:
        if base.lower() == "scratch" or base.startswith("$"):
            continue
        if "@sha256:" not in base:
            result.findings.append(
                LintFinding(
                    rule="image",
                    file=name,
                    line=_line_of(source, f"FROM {base}"),
                    message=f"base image {base!r} is not pinned by digest.",
                    remediation=(
                        "Pin it: FROM python:3.12-slim@sha256:<digest>. A floating tag "
                        "means the environment drifts between runs and your score moves "
                        "for reasons that have nothing to do with the agent."
                    ),
                    severity="error" if require_digest else "warning",
                )
            )

    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if _UNPINNED_INSTALL.search(stripped):
            result.findings.append(
                LintFinding(
                    rule="image",
                    file=name,
                    line=lineno,
                    message="package install without a pinned manifest.",
                    remediation=(
                        "Vendor the dependency: COPY a requirements.txt with hashes, or "
                        "bake wheels at build time and pip install --no-index at runtime."
                    ),
                    severity="warning",
                )
            )
        if _NETWORK_FETCH.search(stripped):
            result.findings.append(
                LintFinding(
                    rule="image",
                    file=name,
                    line=lineno,
                    message="build-time network fetch; the upstream can disappear.",
                    remediation="Copy the payload into environment/seed/ and COPY it in.",
                    severity="warning",
                )
            )
    return result


def _line_of(source: str, needle: str) -> int:
    for lineno, line in enumerate(source.splitlines(), start=1):
        if needle in line:
            return lineno
    return 1


# ---------------------------------------------------------------------------
# sandbox policy (PRD §12.1) — compose files and Dockerfiles
# ---------------------------------------------------------------------------

PROHIBITIONS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"docker\.sock", re.IGNORECASE),
        "mounts the Docker socket",
        "That is a root shell on the host. There is no justification that survives review.",
    ),
    (
        re.compile(r"privileged\s*:\s*true", re.IGNORECASE),
        "requests privileged mode",
        "privileged: true disables every isolation guarantee the sandbox provides.",
    ),
    (
        re.compile(r"network_mode\s*:\s*[\"']?host", re.IGNORECASE),
        "requests host networking",
        "Host networking puts the agent on your network. Use the egress proxy.",
    ),
    (
        re.compile(r"pid\s*:\s*[\"']?host", re.IGNORECASE),
        "requests the host PID namespace",
        "The agent could signal host processes.",
    ),
    (
        re.compile(r"CAP_(SYS_ADMIN|SYS_PTRACE|NET_ADMIN|SYS_MODULE|SYS_RAWIO)", re.IGNORECASE),
        "requests a prohibited capability",
        "These are container-escape primitives; cap_drop: ALL is the default for a reason.",
    ),
    (
        re.compile(r"^\s*-\s*/(?!app\b|tmp\b)[^:\s]*:", re.MULTILINE),
        "bind-mounts a host path outside the task scratch directory",
        "Mount only the task's own scratch directory.",
    ),
]


def lint_security(paths: dict[str, str]) -> LintResult:
    """Scan compose files and Dockerfiles for the §12.1 absolute prohibitions."""
    result = LintResult()
    for filename, source in paths.items():
        for lineno, line in enumerate(source.splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            for pattern, message, remediation in PROHIBITIONS:
                if pattern.search(line):
                    result.findings.append(
                        LintFinding(
                            rule="security",
                            file=filename,
                            line=lineno,
                            message=f"task {message}.",
                            remediation=remediation,
                        )
                    )
    return result
