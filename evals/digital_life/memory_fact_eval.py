"""Updated-fact eval (DL-P2.9): run the 30-case conversation-pair set through
the REAL pipeline decision + store + recall path.

The model extraction seam is mocked to the case's stated facts (exactly what a
correct extraction returns); everything downstream — decide_fact_ops,
add/update/close with bi-temporal supersede, recall_facts gating — is the
production code. Scores:

- updated_fact_correctness: current value recalled AND no old value served
- duplicates: repeated identical facts create 0 duplicate open records
- no_physical_delete: every superseded/closed fact still in history

Usage:
  python -m evals.digital_life.memory_fact_eval --home HOME --output OUT.json

With --qa, additionally asks each case's recall question against the recalled
context through the default configured model (magpie), 30 calls.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from evals.digital_life.memory_fact_cases import load_cases
from evals.digital_life.memory_retrieval import HOME_VARS, assert_home


def _old_served(old_value: str, content: str, current_value: str = "") -> bool:
    """True when a recalled line presents an OLD value as current. A line that
    contains the CURRENT value cannot be serving the old one, even when the
    current value contains the old token as a substring ("美式不加糖" contains
    "加糖"): old-in-content only counts when current is absent."""
    if old_value not in content:
        return False
    return current_value not in content or not current_value


def run_cases() -> dict:
    from tools import fact_store as fs
    from tools.fact_pipeline import apply_fact_ops, decide_fact_ops

    cases = load_cases()
    rows = []
    for case in cases:
        ops_log = []
        for turn in case["turns"]:
            candidates = [{
                "subject": case["subject"],
                "predicate": case.get("predicate", ""),
                "content": turn["fact"],
                "confidence": 0.9,
                "retracted": bool(turn.get("retracted", False)),
            }]
            ops = decide_fact_ops(candidates, source_session_id=f"eval-{case['id']}")
            results = apply_fact_ops(
                ops, source="fact-eval", source_session_id=f"eval-{case['id']}",
                observed_at=turn.get("observed_at"),
            )
            ops_log.append({
                "step": turn["step"],
                "ops": [{k: op.get(k) for k in ("action", "subject", "content")} for op in ops],
                "results": [{k: r.get(k) for k in ("success", "action")} for r in results],
            })
        recalled = fs.recall_facts(case["recall_query"], limit=3)
        contents = [str(r.get("content") or "") for r in recalled]
        old_values = case.get("old_values") or []
        if case.get("retracted"):
            ok = not any(_old_served(ov, c) for c in contents for ov in old_values)
        else:
            ok = any(case["current_value"] in c for c in contents) and not any(
                _old_served(ov, c, case["current_value"]) for c in contents for ov in old_values)
        history = fs.fact_history(case["subject"])
        adds = sum(1 for entry in ops_log for op in entry["ops"] if op.get("action") == "ADD")
        rows.append({
            "id": case["id"], "category": case["category"], "ok": ok,
            "recalled": contents, "ops_log": ops_log,
            "history_rows": len(history),
            # Every ADD leaves a durable row (open or later closed by
            # UPDATE/DELETE): fewer rows than ADDs would mean a physical delete.
            "physical_delete": len(history) < adds,
            "context_block": "\n".join(contents),
            "recall_query": case["recall_query"],
        })

    def rate(field):
        return round(sum(1 for r in rows if r[field]) / len(rows), 4) if rows else 0.0

    per_category = {}
    for row in rows:
        bucket = per_category.setdefault(row["category"], {"cases": 0, "ok": 0})
        bucket["cases"] += 1
        bucket["ok"] += int(row["ok"])

    # Duplicate guard: replay every FINAL current value once more; identical
    # facts must be NOOPs (no new open rows).
    duplicates_created = 0
    for case in cases:
        if case.get("retracted"):
            continue
        final = case["turns"][-1]["fact"]
        ops = decide_fact_ops([{"subject": case["subject"], "content": final, "confidence": 0.9}])
        results = apply_fact_ops(ops, source="fact-eval-dup")
        for res in results:
            if res.get("success") and res.get("action") == "ADD":
                duplicates_created += 1

    return {
        "meta": {
            "cases": len(cases),
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "updated_fact_correctness": rate("ok"),
        "per_category": per_category,
        "duplicates_created": duplicates_created,
        "physical_deletes": sum(1 for r in rows if r["physical_delete"]),
        "per_case": rows,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--qa', action='store_true',
                        help='ask each case question against the recalled context via magpie (30 calls)')
    args = parser.parse_args(argv)

    home = assert_home(args.home)
    for key in HOME_VARS:
        os.environ[key] = str(home)

    from tools import fact_store as fs

    if fs.fact_store_path().exists():
        raise SystemExit(f'refusing to overwrite populated home: {fs.fact_store_path()}')

    result = run_cases()

    if args.qa:
        from evals.digital_life.memory_retrieval import qa_answer

        answered = correct = 0
        for row in result["per_case"]:
            if not row["context_block"]:
                continue
            answered += 1
            try:
                answer = qa_answer(row["recall_query"], row["context_block"])
            except Exception as exc:
                row["qa_answer"] = f"<error: {exc}>"
                continue
            row["qa_answer"] = answer
            cases = {c["id"]: c for c in load_cases()}
            case = cases[row["id"]]
            norm = lambda s: "".join(ch.lower() for ch in str(s or "") if ch.isalnum())
            if case.get("retracted"):
                ok = not any(norm(ov) in norm(answer) for ov in case.get("old_values") or []) and (
                    "不" in answer or "没" in answer or "no" in answer.lower())
            else:
                ok = norm(case["current_value"]) in norm(answer) and not any(
                    norm(ov) in norm(answer) for ov in case.get("old_values") or [])
            row["qa_ok"] = bool(ok)
            correct += int(ok)
        result["qa"] = {"answered": answered, "correct": correct,
                        "accuracy": round(correct / answered, 4) if answered else 0.0}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "updated_fact_correctness": result["updated_fact_correctness"],
        "duplicates_created": result["duplicates_created"],
        "physical_deletes": result["physical_deletes"],
        "qa": result.get("qa"),
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
