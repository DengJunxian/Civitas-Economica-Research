"""Evaluate the Civitas Lab AI teaching desk against the configured real model.

Needs DEEPSEEK_API_KEY or ZHIPUAI_API_KEY (requirements-llm.txt); without one
it reports "skipped" and exits 0. Uses its own budget ledger in the output
folder, so it does not consume the classroom's daily AI budget. See
lab/ai_eval.py for the cases and what each outcome means.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lab.ai_eval import run_eval  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default="outputs/lab-ai-eval")
    parser.add_argument("--comparison", help="Existing comparison directory to explain (default: run EXP-05 on 3 seeds)")
    args = parser.parse_args()
    folder = Path(args.output).resolve() / f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    receipt = run_eval(folder, comparison=args.comparison)
    print(json.dumps({k: v for k, v in receipt.items() if k != "cases"}, ensure_ascii=False, indent=2))
    if receipt["status"] == "completed":
        print(folder / "ai_eval.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
