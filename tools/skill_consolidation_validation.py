"""Non-destructive validation of Stardust Curator Skill consolidation.

Runs the same source-derived, read-only checks on the original and merged
packages before archival, and rechecks successful merges before the run ends.
This protects capability coverage and files; it does not execute arbitrary
external workflows or claim to prove model-level quality improvements.
"""
from __future__ import annotations

import contextvars
import hashlib
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

MAX_FILES = 128
MAX_BYTES = 1_048_576
MAX_CASES = 100
HEADING = re.compile(r"(?m)^#{2,4}\s+(.+?)\s*$")
PROCEDURE = re.compile(
    r"(?i)^\s*(?:[-*]\s+|\d+[.)]\s+|step\s+\d+\s*[:.)]\s*|步骤\s*[一二三四五\d]+\s*[:：.]?\s*)(.+)$"
)
RULE_SIGNAL = re.compile(r"\b(?:must|never|always|ensure|verify|check|avoid|before|after)\b|必须|禁止|确保|核对|先做", re.I)
WORDS = re.compile(r"[a-z][a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", re.I)
LINK = re.compile(r"\]\((?:\./)?((?:references|templates|scripts|assets)/[^)\s#]+)")
IGNORED = {"overview", "notes", "examples", "introduction", "references",
           "resources", "about", "contents", "usage"}
STOP = {"skill", "skills", "with", "using", "when", "then", "into",
        "this", "that", "from", "only", "should", "the", "and", "for"}
TEXT = {".md", ".txt", ".yaml", ".yml"}


@dataclass(frozen=True)
class Package:
    files: dict[str, bytes]
    signature: str
    error: str = ""


def capture(root: Path | None) -> Package:
    """Complete bounded snapshot, rejecting unreadable files and symlinks."""
    if root is None or root.is_symlink() or not root.is_dir():
        return Package({}, "", "package directory missing or unsafe")
    files: dict[str, bytes] = {}
    size = 0
    try:
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                return Package({}, "", "symlink cannot be verified")
            if not path.is_file():
                continue
            if len(files) >= MAX_FILES:
                return Package({}, "", "too many files to verify")
            blob = path.read_bytes()
            size += len(blob)
            if size > MAX_BYTES:
                return Package({}, "", "package too large to verify")
            files[path.relative_to(root).as_posix()] = blob
    except OSError:
        return Package({}, "", "package read failed")
    if "SKILL.md" not in files:
        return Package({}, "", "SKILL.md missing")
    digest = hashlib.sha256()
    for path, blob in files.items():
        digest.update(path.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(blob).digest())
    return Package(files, digest.hexdigest())


def _terms(text: str) -> set[str]:
    result: set[str] = set()
    for word in WORDS.findall(text.lower()):
        if word in STOP:
            continue
        if "\u4e00" <= word[0] <= "\u9fff":
            result.update(word[i:i + 2] for i in range(len(word) - 1))
        else:
            result.add(word)
    return result


def _all_text(pkg: Package) -> str:
    return "\n".join(blob.decode("utf-8-sig", errors="replace")
                     for path, blob in pkg.files.items()
                     if Path(path).suffix.lower() in TEXT)


def _cases(source: Package) -> tuple[list[tuple[str, Any]], str]:
    if source.error:
        return [], source.error
    main = source.files["SKILL.md"].decode("utf-8-sig", errors="replace")
    cases: list[tuple[str, Any]] = []
    description = re.search(r"(?m)^description:\s*(.+)$", main)
    if description and len(_terms(description.group(1))) > 1:
        cases.append(("description", _terms(description.group(1))))
    for heading in HEADING.findall(main):
        if heading.strip().lower() not in IGNORED and (keys := _terms(heading)):
            cases.append(("heading", keys))
    def _rules(text: str) -> None:
        # Preserve representative ordered steps and high-value negative rules,
        # not merely umbrella headings that could mask a hollow rewrite.
        seen = 0
        for line in text.splitlines():
            match = PROCEDURE.match(line)
            candidate = match.group(1) if match else line.strip()
            if (match or RULE_SIGNAL.search(candidate)) and len(candidate) >= 12:
                words = _terms(candidate)
                if len(words) >= 2:
                    cases.append(("rule", words))
                    seen += 1
                    if seen >= 48:
                        break

    _rules(main)
    # If the source referenced a support asset, the umbrella must still point
    # to the *relocated* asset by its real path, not merely copy unused bytes.
    for path in LINK.findall(main):
        if path in source.files:
            cases.append(("linked_asset", hashlib.sha256(source.files[path]).hexdigest()))
    for path, blob in source.files.items():
        if path == "SKILL.md":
            continue
        # Never execute self-authored scripts in an unattended background pass.
        # Exact-byte preservation is required for scripts, binaries and templates.
        if Path(path).suffix.lower() not in TEXT or path.startswith(("scripts/", "templates/", "assets/")):
            cases.append(("asset", hashlib.sha256(blob).hexdigest()))
        else:
            reference = blob.decode("utf-8-sig", errors="replace")
            for heading in HEADING.findall(reference):
                if heading.strip().lower() not in IGNORED and (keys := _terms(heading)):
                    cases.append(("heading", keys))
            _rules(reference)
            unique_terms = _terms(reference)
            if len(unique_terms) >= 5:
                cases.append(("reference", set(sorted(unique_terms)[:40])))
    if len(cases) > MAX_CASES:
        return [], "source has too many validation cases"
    if not cases:
        return [], "source has no checkable capability cases"
    return cases, ""


def _broken_links(pkg: Package) -> int:
    broken = 0
    for path, blob in pkg.files.items():
        if Path(path).suffix.lower() not in TEXT:
            continue
        parent = Path(path).parent
        for match in LINK.finditer(blob.decode("utf-8-sig", errors="replace")):
            parts: list[str] = []
            for part in (parent / match.group(1)).as_posix().split("/"):
                if part == "..":
                    if not parts:
                        broken += 1
                        break
                    parts.pop()
                elif part != ".":
                    parts.append(part)
            else:
                if "/".join(parts) not in pkg.files:
                    broken += 1
    return broken


def compare(source: Package, candidate: Package) -> dict[str, Any]:
    """Run the exact same original capability cases against both packages."""
    if source.error or candidate.error:
        return {"passed": False, "reason": source.error or candidate.error}
    cases, err = _cases(source)
    if err:
        return {"passed": False, "reason": err}
    def score(pkg: Package) -> int:
        tokens = _terms(_all_text(pkg))
        hashes = {hashlib.sha256(b).hexdigest() for b in pkg.files.values()}
        main = pkg.files["SKILL.md"].decode("utf-8-sig", errors="replace")
        reachable = {
            hashlib.sha256(blob).hexdigest()
            for path, blob in pkg.files.items()
            if path != "SKILL.md" and path in LINK.findall(main)
        }
        correct = 0
        for kind, expected in cases:
            if kind == "asset":
                correct += expected in hashes
            elif kind == "linked_asset":
                correct += expected in reachable
            else:
                recall = len(expected & tokens)
                strength = 6 if kind in {"description", "reference"} else (8 if kind == "rule" else 7)
                threshold = max(1, (len(expected) * strength + 9) // 10)
                correct += recall >= threshold
        return correct
    before, after = score(source), score(candidate)
    broken = _broken_links(candidate)
    ok = before == len(cases) and after == before and not broken
    return {"passed": ok, "baseline_passed": before,
            "candidate_passed": after, "total": len(cases),
            "broken_links": broken, "reason": ("preserved" if ok else
            f"capability check {after}/{before}, broken links {broken}")}


@dataclass
class ValidationRun:
    originals: dict[str, Package]
    accepted: list[tuple[str, str]] = field(default_factory=list)
    denied: list[dict[str, str]] = field(default_factory=list)
    rollback: list[dict[str, str]] = field(default_factory=list)

    def assess(self, name: str, into: str, source: Path, target: Path) -> dict[str, Any]:
        baseline = self.originals.get(name)
        if baseline is None or baseline.error:
            return self._deny(name, into, "original package snapshot unavailable")
        current = capture(source)
        if current.error or current.signature != baseline.signature:
            return self._deny(name, into, "original skill changed during consolidation")
        destination = capture(target)
        previous = [old for old, dest in self.accepted if dest == into]
        total = 0
        for skill in [name] + [old for old in previous if old != name]:
            result = compare(self.originals[skill], destination)
            if not result["passed"]:
                return self._deny(name, into, result["reason"])
            total += result["total"]
        return {"passed": True, "reason": "source cases preserved", "cases": total}

    def _deny(self, name: str, into: str, reason: str) -> dict[str, Any]:
        self.denied.append({"source": name, "into": into, "reason": reason})
        return {"passed": False, "reason": reason}

    def archived(self, name: str, into: str) -> None:
        self.accepted.append((name, into))

    def finalize(self) -> None:
        """Recheck after later edits; restore archived originals on regression."""
        from tools import skill_manager_tool, skill_usage
        for name, into in list(self.accepted):
            target = skill_manager_tool._find_skill(into)
            result = compare(self.originals[name],
                             capture(Path(target["path"]) if target else None))
            if result["passed"]:
                continue
            ok, message = skill_usage.restore_skill(name)
            self.rollback.append({"source": name, "into": into,
                                  "status": "restored" if ok else "failed",
                                  "reason": result["reason"] if ok else message})

    def summary(self) -> dict[str, Any]:
        return {"accepted": len(self.accepted), "blocked": len(self.denied),
                "restored": sum(x["status"] == "restored" for x in self.rollback),
                "restore_failed": sum(x["status"] != "restored" for x in self.rollback),
                "denied": self.denied[:20], "rollback": self.rollback[:20]}


_ACTIVE: contextvars.ContextVar[ValidationRun | None] = contextvars.ContextVar(
    "stardust_curator_validation", default=None)


def active_validation() -> ValidationRun | None:
    return _ACTIVE.get()


@contextmanager
def curator_validation_pass(candidates: list[dict[str, Any]]) -> Iterator[ValidationRun]:
    """Immutable baseline before the model modifies any candidate package."""
    from tools.skill_manager_tool import _find_skill
    originals: dict[str, Package] = {}
    for row in candidates:
        name = row.get("name")
        if isinstance(name, str) and name and name not in originals:
            found = _find_skill(name)
            originals[name] = capture(Path(found["path"]) if found else None)
    validation = ValidationRun(originals)
    token = _ACTIVE.set(validation)
    try:
        yield validation
    finally:
        _ACTIVE.reset(token)
