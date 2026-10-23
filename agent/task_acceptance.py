"""Opt-in task contracts and evidence, stored in the existing verification ledger.

The small CLI is available through the terminal tool; no permanent model tool or
system-prompt mutation is needed. Evidence is an attributed observation, never an
upgrade of a unit test into a successful user flow.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

TIERS = ("test", "build", "runtime", "user-flow", "review")
_ACTIVATE = re.compile(r"(?im)(?:^\s*(?:\[complex\]|complex task\b|复杂任务)|^\s*(?:acceptance criteria|done criteria|验收条件|验收标准)\s*[:：])")
_HEADING = re.compile(r"(?im)^\s*(?:acceptance criteria|done criteria|验收条件|验收标准)\s*[:：]\s*")


def _schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS task_acceptance (session_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")


def load(session_id):
    if not isinstance(session_id, str) or not session_id:
        return None
    from agent.verification_evidence import _db_path, _transaction
    if not _db_path().is_file():
        return None
    with _transaction() as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_acceptance'").fetchone():
            return None
        row = conn.execute("SELECT payload FROM task_acceptance WHERE session_id=?", (session_id,)).fetchone()
    return json.loads(row[0]) if row else None


def save(contract):
    from agent.verification_evidence import _transaction
    with _transaction() as conn:
        _schema(conn)
        conn.execute("INSERT OR REPLACE INTO task_acceptance VALUES (?, ?)",
                     (contract["session_id"], json.dumps(contract, ensure_ascii=False)))


def _tier(text):
    explicit = re.match(r"\[(test|build|runtime|user-flow|review)\]\s*", text, re.I)
    if explicit:
        return explicit[1].lower()
    if re.match(r"(?i)\s*(review|read|inspect|审阅|检查文档)\b.*(?:markdown|\.md\b|文档)", text):
        return "review"
    if re.search(r"(?i)\b(ui|button|click|browser|screen)\b|按钮|点击|界面|浏览器", text):
        return "user-flow"
    if re.search(r"(?i)markdown|\.md\b|文档", text):
        return "review"
    if re.search(r"(?i)\b(build|compile)\b|构建|编译", text):
        return "build"
    if re.search(r"(?i)\b(unit test|pytest|test)\b|单测|测试", text):
        return "test"
    return "runtime"


def _acceptance_specs(request):
    heading = _HEADING.search(request)
    tail = request[heading.end():] if heading else ""
    criteria = []
    for line in tail.splitlines():
        item = re.match(r"\s*(?:[-*]|\d+[.)])\s+(.+)", line)
        if item:
            criteria.append(item[1].strip())
        elif line.strip() and criteria:
            break
    return criteria or [tail.strip() or request.strip()]


def begin(session_id, request, root):
    """Only current, explicit user input activates/replaces a contract.

    A continuation keeps the old goal; an unrelated new ask suspends it. Historical
    summaries and tool output are never parsed as instructions here.
    """
    if not isinstance(session_id, str) or not session_id or not isinstance(request, str):
        return None
    previous = load(session_id)
    if not _ACTIVATE.search(request):
        if previous and re.match(r"(?i)^\s*(continue\b|resume\b|继续|接着)", request):
            previous["active"] = True
            save(previous)
            return previous
        if previous and previous.get("active", True):
            previous["active"] = False
            save(previous)
        return None
    criteria = _acceptance_specs(request)
    from agent.verification_evidence import ledger_timestamp
    contract = {
        "session_id": session_id, "active": True, "goal": request[:8000], "root": str(Path(root).resolve()),
        "created_at": ledger_timestamp(), "plan": None, "blockers": [], "todos": [], "cleanup": [],
        "criteria": [{"id": f"C{i+1}", "text": text[:2000], "tier": _tier(text)}
                     for i, text in enumerate(criteria[:20])], "evidence": [],
    }
    save(contract)
    return contract


def carry(from_session_id, to_session_id):
    if not from_session_id or not to_session_id or from_session_id == to_session_id:
        return
    contract = load(from_session_id)
    if contract and not load(to_session_id):
        contract["session_id"] = to_session_id
        save(contract)


def change_identity(root):
    from agent.workspace_fingerprint import workspace_content_fingerprint
    identity = workspace_content_fingerprint(root) if (Path(root) / ".git").exists() else None
    if identity:
        return identity
    # Non-Git user workspaces also need a bounded, content-based change identity.
    digest = hashlib.sha256()
    total = 0
    import os
    paths = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__"}
                         and not (Path(directory) / d).is_symlink())
        paths.extend(Path(directory) / name for name in sorted(files))
        if len(paths) > 2000:
            return None
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        if any(p.startswith(".") or p in {"node_modules", "__pycache__"} for p in path.relative_to(root).parts):
            continue
        total += path.stat().st_size
        if total > 64 * 1024 * 1024:
            return None
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _expected_steps(contract, criterion):
    return [(i, step) for i, step in enumerate(contract.get("plan") or [])
            if isinstance(step, dict) and step.get("criterion") == criterion
            and "expected_result" in step]


def _observe(step, artifact):
    """Compare only deterministic artifact values; never interpret prose as success."""
    result = {"step": step, "comparison": "unknown"}
    reference = step.get("evidence_ref")
    if not reference or Path(reference).resolve() != artifact:
        return result
    try:
        text = artifact.read_text(encoding="utf-8")
        if step.get("observer_type") == "text":
            observed = text
        elif step.get("observer_type") == "json":
            envelope = json.loads(text)
            if not isinstance(envelope, dict) or "observed" not in envelope or envelope.get("interrupted") is True:
                return result
            observed = envelope["observed"]
        else:
            return result
        # JSON canonicalization keeps booleans distinct from numbers, including nested values.
        expected_json = json.dumps(step["expected_result"], sort_keys=True, allow_nan=False)
        observed_json = json.dumps(observed, sort_keys=True, allow_nan=False)
        result.update(observed=observed, comparison="matched" if expected_json == observed_json else "mismatched")
    except (OSError, UnicodeError, ValueError, TypeError):
        return result
    return result


def _expectations_met(contract, criterion, evidence):
    for index, step in _expected_steps(contract, criterion):
        candidates = [(e, o) for e in contract["evidence"]
                      if e["criterion"] == criterion and e["tier"] == evidence["tier"]
                      and e["change"] == evidence["change"]
                      for o in e.get("observations", [])
                      if o.get("index") == index
                      and json.dumps(o.get("step"), sort_keys=True) == json.dumps(step, sort_keys=True)]
        if not candidates:
            return False
        receipt, observation = candidates[-1]
        artifact = Path(receipt["artifact"])
        if (receipt["status"] != "passed" or observation.get("comparison") != "matched"
                or not artifact.is_file()
                or hashlib.sha256(artifact.read_bytes()).hexdigest() != receipt.get("artifact_sha256")):
            return False
    return True


def unmet(contract):
    if not contract:
        return []
    missing = []
    if not contract.get("plan"):
        missing.append("Plan: record the goal, steps, todos and cleanup before execution")
    current = change_identity(contract["root"])
    for criterion in contract["criteria"]:
        observations = [e for e in contract["evidence"] if e["criterion"] == criterion["id"]
                        and e["tier"] == criterion["tier"] and current and e["change"] == current]
        latest = observations[-1] if observations else None
        if latest and latest.get("artifact"):
            artifact = Path(latest["artifact"])
            if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != latest.get("artifact_sha256"):
                latest = None
        expectations_met = bool(latest) and _expectations_met(contract, criterion["id"], latest)
        if not latest or latest["status"] != "passed" or not expectations_met:
            verdict = latest["status"] if latest else "no current evidence"
            if latest and not expectations_met:
                verdict += "; expected observation mismatched or unknown"
            missing.append(f'{criterion["id"]}: {criterion["text"]} [requires {criterion["tier"]}; {verdict}]')
    missing.extend(f"Blocker: {v}" for v in contract["blockers"])
    missing.extend(f"Todo: {v}" for v in contract["todos"])
    missing.extend(f"Cleanup: {v}" for v in contract["cleanup"])
    return missing


def guidance(contract):
    if not contract:
        return ""
    import sys
    command = f'"{sys.executable}" "{Path(__file__).resolve()}"'
    return ("\nTask acceptance contract (current user goal; historical plans are reference only):\n"
            + json.dumps(contract, ensure_ascii=False) + "\n"
            f"Before execution, record a small plan via the terminal: {command} "
            "--session SESSION plan --data JSON. JSON fields: steps (nonempty list), blockers, todos, cleanup. "
            "Step objects may optionally include criterion (C1), action_category, expected_result, "
            "observer_type (text or json), evidence_ref (absolute artifact path). Declaring expected_result "
            "opts that criterion into strict comparison: text compares UTF-8 content; json reads the "
            "observed field of a JSON object. Missing/unsupported/interrupted observations are unknown; "
            "only matched observations can satisfy the criterion. JSON interrupted:true is unknown. "
            "Use the actual Python executable and session_id above. After each actual observation, record: "
            f"{command} --session SESSION evidence --criterion C1 --tier "
            "test|build|runtime|user-flow|review --status passed|failed --source TOOL_CALL_OR_COMMAND "
            "--artifact ABSOLUTE_RESULT_FILE. Save the real result/screenshot first. Record each tier separately. "
            "A unit test/build/runtime launch cannot satisfy a user-flow criterion: launch the actual app, "
            "click in the real UI, read the resulting state and record failure if it fails. "
            "For review criteria inspect the document; no browser is required. "
            "Update plan todos/blockers/cleanup as they are resolved. Never claim done with unmet criteria.\n")


def prepare(agent, request):
    from agent.runtime_cwd import resolve_agent_cwd
    contract = begin(getattr(agent, "session_id", None), request, resolve_agent_cwd())
    agent._task_acceptance_active = bool(contract)
    agent._task_acceptance_request = request
    return guidance(contract)


def active(agent):
    if getattr(agent, "_task_acceptance_active", False) is not True:
        return None
    return load(getattr(agent, "session_id", None))


def refresh_current_goal(agent, messages):
    """Consume only genuine mid-turn user rows, never tool/summary text."""
    if not isinstance(getattr(agent, "_task_acceptance_request", None), str):
        return
    if getattr(agent, "_task_acceptance_active", False) is not True:
        return
    from agent.prompt_builder import STEER_DISPLAY_KIND, STEER_MARKER_OPEN, STEER_MARKER_CLOSE
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        if message.get("display_kind") != STEER_DISPLAY_KIND:
            if message.get("content") == getattr(agent, "_task_acceptance_request", None):
                break
            continue
        request = message.get("content")
        if isinstance(request, str) and request.startswith(STEER_MARKER_OPEN):
            request = request[len(STEER_MARKER_OPEN):].removesuffix(STEER_MARKER_CLOSE).strip()
        if isinstance(request, str) and request != getattr(agent, "_task_acceptance_request", None):
            prepare(agent, request)
        break


def nudge(agent):
    contract = active(agent)
    missing = unmet(contract)
    if not missing or getattr(agent, "_task_acceptance_nudges", 0) >= 2:
        return None
    agent._task_acceptance_nudges = getattr(agent, "_task_acceptance_nudges", 0) + 1
    return "[Task acceptance: completion is not established.\n" + "\n".join(missing) + "\n" + guidance(contract) + "]"


def finalize(agent, response):
    missing = unmet(active(agent))
    if not missing:
        return response
    receipt = "Task acceptance: NOT COMPLETE / 未验收\n" + "\n".join("- " + item for item in missing)
    candidate = (response or "").rstrip()
    return receipt + ("\n\nAgent progress (not an acceptance verdict):\n" + candidate if candidate else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    subs = parser.add_subparsers(dest="action", required=True)
    plan = subs.add_parser("plan")
    plan.add_argument("--data", required=True)
    evidence = subs.add_parser("evidence")
    evidence.add_argument("--criterion", required=True)
    evidence.add_argument("--tier", choices=TIERS, required=True)
    evidence.add_argument("--status", choices=("passed", "failed"), required=True)
    evidence.add_argument("--source", required=True)
    evidence.add_argument("--artifact", required=True)
    subs.add_parser("status")
    args = parser.parse_args()
    contract = load(args.session)
    if not contract:
        parser.error("No explicitly enabled task contract for this session")
    if args.action == "plan":
        data = json.loads(args.data)
        if not isinstance(data.get("steps"), list) or not data["steps"]:
            parser.error("plan needs a nonempty steps list")
        for step in data["steps"]:
            if not isinstance(step, dict):
                continue  # Legacy step shapes retain their existing behavior.
            if "expected_result" in step and step.get("criterion") not in {c["id"] for c in contract["criteria"]}:
                parser.error("expected_result needs a known criterion")
            for field in ("action_category", "observer_type", "evidence_ref"):
                if field in step and not isinstance(step[field], str):
                    parser.error(f"step {field} must be a string")
            if step.get("evidence_ref") and not Path(step["evidence_ref"]).is_absolute():
                parser.error("step evidence_ref must be an absolute artifact path")
        contract["plan"] = data["steps"]
        for field in ("blockers", "todos", "cleanup"):
            values = data.get(field, [])
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                parser.error(f"{field} must be a list of strings")
            contract[field] = values
    elif args.action == "evidence":
        if args.criterion not in {c["id"] for c in contract["criteria"]}:
            parser.error("unknown criterion")
        if not contract.get("plan"):
            parser.error("record the plan before evidence")
        artifact = Path(args.artifact).resolve(strict=True)
        if not artifact.is_file():
            parser.error("artifact must be a real result file")
        from agent.verification_evidence import ledger_timestamp
        contract["evidence"].append({"criterion": args.criterion, "tier": args.tier,
            "status": args.status, "source": args.source, "artifact": str(artifact),
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "created_at": ledger_timestamp(), "change": change_identity(contract["root"])})
        from agent import acceptance_observations
        if acceptance_observations.enabled():
            contract["evidence"][-1]["provenance"] = acceptance_observations.resolve(
                contract, args.source, artifact, args.tier)
        steps = _expected_steps(contract, args.criterion)
        if steps:
            linked = [(index, step) for index, step in steps
                      if step.get("evidence_ref") and Path(step["evidence_ref"]).resolve() == artifact]
            contract["evidence"][-1]["observations"] = [
                dict(_observe(step, artifact), index=index) for index, step in (linked or steps)]
    if args.action != "status":
        save(contract)
    print(json.dumps({"contract": contract, "unmet": unmet(contract)}, ensure_ascii=False))


if __name__ == "__main__":
    # Direct absolute script invocation also works in scrubbed terminal environments.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    main()
