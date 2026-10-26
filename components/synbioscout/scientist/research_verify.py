"""Read-only external receipt verifier: python -m scientist.research_verify RUN_JSON.

No repair/backfill of old results. A new budgeted continuation is the explicit
fallback when evidence or a version review was not executed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from .models import ResearchRun
from .research_flow import ResearchFlow


def verify_run(run):
    if not run.co_scientist or not run.co_scientist.research_flow:
        return {"valid": False, "status": "not_recorded", "errors": ["legacy_receipts_absent_no_backfill"]}
    flow = object.__new__(ResearchFlow)
    flow.engine = SimpleNamespace(run=run, state=run.co_scientist)
    flow.data = run.co_scientist.research_flow
    report = flow.verify()
    limits = run.brief.budget
    budget = run.co_scientist.budget
    for key, maximum in (("literature_queries", limits.max_searches), ("llm_calls", limits.max_llm_calls),
                         ("http_attempts", limits.max_http_requests), ("tool_calls", limits.max_tool_calls)):
        if budget.get(key, 0) > maximum:
            report["errors"].append("shared_budget:" + key)
    if len({h.hypothesis_id for h in run.co_scientist.hypotheses}) > limits.max_hypotheses:
        report["errors"].append("hypothesis_budget")
    report["valid"] = not report["errors"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_json", type=Path)
    args = parser.parse_args()
    run = ResearchRun.model_validate_json(args.run_json.read_bytes())
    result = verify_run(run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["valid"] else 1)


if __name__ == "__main__":
    main()

