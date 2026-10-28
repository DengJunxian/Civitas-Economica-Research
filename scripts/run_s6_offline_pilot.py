"""Execute an offline, zero-provider S6 synthetic-control smoke experiment.

This runs **three rule baselines only**, never requests an LLM, and writes
actual market/agent/world archives plus the quality-gated comparison.
A 1-seed controlled demo is engineering evidence, not a scientific finding.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import yaml

from core.experiments.config import ExperimentConfig
from research.civitas_agent.benchmark import summarize
from research.civitas_agent.matrix import build_plan, run_rule_controls


def run(root: Path, seed: int = 41, *, full_size: bool = False) -> dict:
    if root.exists():
        raise ValueError("Offline demo requires a new output directory")
    original = Path(__file__).resolve().parents[1] / "examples/experiments/scientific_s5_m1_s1.yaml"
    if full_size:
        template = original
    else:
        # A deliberately tiny execution probe rather than a performance test.
        # The full 64x100 experiment is a separate explicit local choice.
        payload = yaml.safe_load(original.read_text(encoding="utf-8"))
        payload["name"] = "s6_offline_smoke_template"
        payload["n_agents"] = 12
        payload["steps"] = 8
        payload["cognitive"]["decision_interval"] = 2
        payload["scientific_world"]["shocks"][0]["step"] = 3
        payload["scientific_world"]["shocks"][0]["reveal_step"] = 6
        payload["llm"]["max_calls"] = 100
        config = ExperimentConfig.model_validate(payload)
        root.mkdir(parents=True, exist_ok=False)
        template = root / "pilot_template.yaml"
        template.write_text(
            yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )
    plan_dir = root / "plan"
    build_plan(
        template, plan_dir,
        dev_seeds=[seed], validation_seeds=[seed + 1],
        test_seeds=[seed + 2],
    )
    paths = run_rule_controls(
        plan_dir, root / "runs", partition="development",
    )
    scorecard = summarize(
        plan_dir, root / "runs", root / "analysis",
        partition="development", rule_only=True, bootstrap=200,
    )
    table = pd.read_csv(root / "analysis" / "baseline_per_run.csv")
    summary = {
        "run_count": len(paths),
        "seed": seed,
        "completed_control_arms": sorted(table["arm"].tolist()),
        "model_calls": int(table["model_call_records"].sum()),
        "quality_gate_failure_count": scorecard["quality_gate_failures"],
        "scorecard_status": scorecard["study_status"],
        "market_diagnostics": [
            {
                "arm": row["arm"],
                "active_step_share": row["active_step_share"],
                "two_sided_quote_share": row["two_sided_quote_share"],
                "stale_price_share": row["stale_price_share"],
                "mean_abs_fundamental_deviation": row["mean_abs_fundamental_deviation"],
                "quality_gate_pass": bool(row["quality_gate_pass"]),
            }
            for row in table.to_dict(orient="records")
        ],
        "interpretation": (
            "Synthetic, one-world engineering demonstration. "
            "Not LLM evidence, not calibrated market realism, no causal claim."
        ),
    }
    (root / "offline_pilot_summary.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("outputs/s6-offline-ci"))
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--full-size", action="store_true",
                        help="Explicitly run larger 64-agent, 100-step offline controls")
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.seed, full_size=args.full_size), indent=2))


if __name__ == "__main__":
    main()
