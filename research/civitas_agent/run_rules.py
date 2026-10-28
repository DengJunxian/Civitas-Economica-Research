"""Run ONLY frozen S6 deterministic controls from an existing plan."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.civitas_agent.matrix import run_rule_controls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--partition", choices=["development", "validation", "heldout_worlds"],
        default="development",
    )
    args = parser.parse_args()
    if args.partition == "heldout_worlds":
        parser.error(
            "The offline runner does not execute held-out worlds during "
            "development. Use an explicit finalized experiment procedure."
        )
    paths = run_rule_controls(args.plan, args.output, partition=args.partition)
    print(json.dumps({
        "mode": "offline_deterministic_cognitive_controls",
        "model_provider_requests": 0,
        "partition": args.partition,
        "runs_verified": len(paths),
        "run_directories": paths,
    }, indent=2))


if __name__ == "__main__":
    main()
