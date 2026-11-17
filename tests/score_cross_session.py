"""Apply the preregistered rubric to the synthetic Codex recovery experiment."""

import argparse
import json
from pathlib import Path
import re


RUBRIC = {
    "goal": (r"dispatch latency", r"offline.*drill|drill.*offline"),
    "delivered": (r"p50", r"p95", r"nearest.rank"),
    "decision": (r"(?:no|zero|without|standard.library.only).*(?:dependenc|package|third.party)",),
    "decision_reason": (r"air.gapped", r"(?:cannot|can.t|could not|no).*fetch|without.*(?:internet|network|download)"),
    "open_issue": (r"p99", r"open|pending|remain|not (?:yet |currently )?(?:implement|support)|unsupported"),
    "next_step": (r"p99", r"regression test"),
    "test_evidence": (r"latency-check\.log", r"pass|exit (?:code )?0"),
    "staleness": (r"head|baseline", r"changed|drift|stale|differ"),
    "confidence": (r"histor|previous|at capture|recorded", r"stale|re.?check|re.?validat|current"),
}


def evaluate(directory, arm, capture_id):
    reply = json.loads((directory / (arm + "-answer.txt")).read_text(encoding="utf-8-sig"))
    events = [
        json.loads(line) for line in (directory / (arm + ".jsonl")).read_text("utf-8-sig").splitlines()
        if line.strip()
    ]
    checks = {}
    for key, patterns in RUBRIC.items():
        answer = reply.get(key)
        checks[key] = "omitted" if not answer else (
            "matched" if all(re.search(pattern, answer, re.I | re.S) for pattern in patterns)
            else "nonmatching"
        )
    reported = reply.get("capture_id")
    checks["capture_id"] = "omitted" if not reported else "matched" if capture_id == reported else "nonmatching"
    items = [event["item"] for event in events if event.get("type") == "item.completed"]
    commands = [item for item in items if item.get("type") == "command_execution"]
    usage = [event.get("usage") for event in events if event.get("type") == "turn.completed"]
    return {
        "checks": checks,
        "matched": sum(value == "matched" for value in checks.values()),
        "omitted": sum(value == "omitted" for value in checks.values()),
        "nonmatching": sum(value == "nonmatching" for value in checks.values()),
        "total_fields": len(checks),
        "turns": len(usage),
        "tool_commands": len(commands),
        "usage": usage,
        "session_id": next(event["thread_id"] for event in events if event.get("type") == "thread.started"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_dir", type=Path)
    parser.add_argument("--capture-id", required=True)
    args = parser.parse_args()
    result = {arm: evaluate(args.evidence_dir, arm, args.capture_id)
              for arm in ("control", "treatment")}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
