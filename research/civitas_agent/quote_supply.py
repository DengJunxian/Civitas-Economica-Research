"""S6.2: matched rule-only quote-supply mechanism experiment.

A small cohort may submit genuine, cash/share-backed *passive* limit orders,
one side per decision. This is deliberately designed to address thin bids;
any increased quote availability is a mechanism test, NOT external validity.
No model provider, simulator-induced price, or synthetic transaction exists.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from core.experiments.config import ExperimentConfig
from core.experiments.runner import ExperimentRunner
from research.civitas_agent.analysis import _bootstrap_mean_ci
from research.civitas_agent.liquidity_sweep import (
    _build_control, _check_values, _passes, _sha, diagnose,
)


POLICIES = ("random", "passive_quote")
COMPARE = (
    "two_sided_quote_share", "active_step_share", "stale_price_share",
    "empty_book_share", "bid_only_share", "ask_only_share",
    "mean_bid_depth", "mean_ask_depth", "order_rejection_share",
    "ttl_cancellation_share", "total_volume",
    "buy_decision_share", "sell_decision_share",
    "requested_buy_quantity", "requested_sell_quantity",
)


def _config(
    template: Path, *, policy: str, ttl: int, seed: int, steps: int,
    n_agents: int,
) -> ExperimentConfig:
    if policy not in POLICIES:
        raise ValueError("Unsupported quote-supply study policy")
    source = _build_control(
        template, ttl=ttl, seed=seed, steps=steps, n_agents=n_agents
    )
    payload = source.model_dump(mode="json")
    payload["name"] = f"s62_{policy}_t{ttl}_s{seed}"
    payload["cognitive"]["decision_policy"] = policy
    # Equal per-step opportunities for the random and passive-quote cohorts.
    payload["cognitive"]["decision_interval"] = 1
    return ExperimentConfig.model_validate(payload)


def run_development(
    output_root: Path, *, template_path: Path, ttls: Iterable[int],
    seeds: Iterable[int], n_agents: int = 64, steps: int = 40,
) -> dict[str, Any]:
    ttls, seeds = _check_values(ttls, seeds, n_agents=n_agents, steps=steps)
    if output_root.exists():
        raise ValueError("Refusing to overwrite a previous quote-supply study")
    planned = [
        (seed, ttl, policy, _config(
            template_path, policy=policy, ttl=ttl, seed=seed,
            steps=steps, n_agents=n_agents
        ))
        for seed in seeds for ttl in ttls for policy in POLICIES
    ]
    output_root.mkdir(parents=True, exist_ok=False)
    design = {
        "schema_version": 1,
        "research_type": "controlled_synthetic_market_mechanism",
        "hypothesis": "Account-funded passive limit quote suppliers increase actual resting bid-side liquidity versus matched-frequency random agents",
        "quality_concern": "Designed-to-improve-quote-policy is not evidence of realistic investor behavior",
        "policies": list(POLICIES),
        "ttls": ttls, "development_seeds": seeds,
        "steps": steps, "n_agents": n_agents,
        "same_action_frequency": True,
        "same_cognitive_cohort": True,
        "same_true_clearing_and_matching": True,
        "template_sha256": _sha(template_path),
        "llm_enabled": False,
        "test_or_heldout_seeds_used": False,
    }
    (output_root / "study_design.json").write_text(
        json.dumps(design, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )
    result: list[dict[str, Any]] = []
    world_ids: dict[int, str] = {}
    populations: dict[int, str] = {}
    source_hashes: set[str] = set()
    cohort_ids: dict[int, list[str]] = {}
    arrival_fingerprints: dict[tuple[int, int], str] = {}
    for seed, ttl, policy, cfg in planned:
        path = ExperimentRunner(cfg).run(output_root / "runs")
        measurement = diagnose(path)
        passed, reasons = _passes(measurement)
        world_hash = _sha(path / "world.json")
        pop_hash = _sha(path / "population.json")
        cohort = json.loads((path / "cognitive_protocol.json").read_text())[
            "active_llm_agent_ids"
        ]
        schedule = _sha(path / "study_order_schedule.jsonl")
        if seed in world_ids and world_ids[seed] != world_hash:
            raise ValueError("Initial world changed across quote-supply arms")
        if seed in populations and populations[seed] != pop_hash:
            raise ValueError("Initial population changed across policies")
        if seed in cohort_ids and cohort_ids[seed] != cohort:
            raise ValueError("Cognitive cohort differs across mechanisms")
        key = (seed, ttl)
        if key in arrival_fingerprints and arrival_fingerprints[key] != schedule:
            raise ValueError("Submission arrival schedule differs across policies")
        world_ids[seed] = world_hash
        populations[seed] = pop_hash
        cohort_ids[seed] = cohort
        arrival_fingerprints[key] = schedule
        source = json.loads((path / "source.json").read_text())
        source_hashes.add(source["sha256"])
        result.append({
            "seed": seed, "ttl": ttl, "policy": policy,
            "path": str(path.resolve()),
            "base_quality_gate_pass": passed,
            "base_quality_failures": ",".join(reasons),
            **measurement,
        })
    if len(source_hashes) != 1:
        raise ValueError("Mixed simulator versions across quote experiment arms")
    table = pd.DataFrame(result).sort_values(["seed", "ttl", "policy"])
    table.to_csv(output_root / "quote_supply_per_run.csv", index=False)
    differences: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for seed in seeds:
        for ttl in ttls:
            paired = table[(table.seed == seed) & (table.ttl == ttl)].set_index("policy")
            if set(paired.index) != set(POLICIES):
                raise ValueError("Missing required random/passive quoting pair")
            base = paired.loc["random"]
            passive = paired.loc["passive_quote"]
            for metric in COMPARE:
                differences.append({
                    "seed": seed, "ttl": ttl, "metric": metric,
                    "passive_minus_random": float(passive[metric] - base[metric]),
                })
            activity_delta = float(passive["active_step_share"] - base["active_step_share"])
            reject_delta = float(passive["order_rejection_share"] - base["order_rejection_share"])
            # Exploratory guardrails: intended quote coverage is not enough
            # if it destroys trading participation or sharply increases rejects.
            eligible = bool(
                passive["base_quality_gate_pass"]
                and passive["two_sided_quote_share"] > base["two_sided_quote_share"]
                and activity_delta >= -0.10
                and reject_delta <= 0.10
            )
            diagnostics.append({
                "seed": seed, "ttl": ttl,
                "random_two_sided": float(base["two_sided_quote_share"]),
                "passive_two_sided": float(passive["two_sided_quote_share"]),
                "active_step_change": activity_delta,
                "rejection_rate_change": reject_delta,
                "development_mechanism_guard_pass": eligible,
            })
    diff_frame = pd.DataFrame(differences)
    diff_frame.to_csv(output_root / "paired_policy_differences.csv", index=False)
    pd.DataFrame(diagnostics).to_csv(
        output_root / "policy_guardrails_by_world.csv", index=False
    )
    effect = []
    for ttl in ttls:
        for metric in COMPARE:
            values = diff_frame[(diff_frame.ttl == ttl) &
                                (diff_frame.metric == metric)]["passive_minus_random"].tolist()
            lo, hi = _bootstrap_mean_ci(values, 1000, seed=20261028)
            effect.append({
                "ttl": ttl, "metric": metric, "independent_worlds": len(values),
                "mean_passive_minus_random": float(np.mean(values)),
                "ci95_lower": lo, "ci95_upper": hi,
                "inference": "descriptive_only" if len(values) < 5 else "exploratory_bootstrap",
            })
    pd.DataFrame(effect).to_csv(
        output_root / "policy_effect_summary.csv", index=False
    )
    guards = pd.DataFrame(diagnostics)
    eligible = [
        int(ttl) for ttl in ttls
        if guards[guards.ttl == ttl].development_mechanism_guard_pass.all()
    ]
    decision = {
        "schema_version": 1,
        "study": "S6.2 controlled quote-supply mechanism development pilot",
        "measured_worlds": len(seeds),
        "finished_runs": len(result),
        "candidate_ttl_with_passive_quotes": min(eligible) if eligible else None,
        "status": (
            "EXPLORATORY_QUOTE_SUPPLY_CANDIDATE_UNVALIDATED" if eligible
            else "QUOTE_SUPPLY_MECHANISM_DID_NOT_CLEAR_ALL_DEV_GUARDS"
        ),
        "model_api_calls": 0,
        "paid_llm_ready": False,
        "heldout_seeds_used": False,
        "market_realism_verified": False,
        "warnings": [
            "Deliberately market-quality-targeted algorithm: quote improvement is not a scientific realism finding",
            "Only development seeds; independently validate mechanism and rule robustness",
            "Same initial endowment does not imply equal executed strategy returns",
            "Actual model trading may differ from deterministic passive quote supplier behavior",
        ],
        "source_fingerprint": next(iter(source_hashes)),
        "file_sha256": {
            name: _sha(output_root / name) for name in (
                "study_design.json", "quote_supply_per_run.csv",
                "paired_policy_differences.csv", "policy_guardrails_by_world.csv",
                "policy_effect_summary.csv",
            )
        },
    }
    (output_root / "decision.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return decision


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--template", type=Path,
        default=Path("examples/experiments/scientific_s5_no_claim_m1_s1.yaml"))
    p.add_argument("--ttls", default="1,3")
    p.add_argument("--seeds", default="41,42")
    p.add_argument("--agents", type=int, default=64)
    p.add_argument("--steps", type=int, default=40)
    args = p.parse_args()
    decision = run_development(
        args.output, template_path=args.template,
        ttls=[int(value) for value in args.ttls.split(",")],
        seeds=[int(value) for value in args.seeds.split(",")],
        steps=args.steps, n_agents=args.agents,
    )
    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()
