"""S6.1: paired, rule-only market microstructure diagnostic (NO LLM calls).

Answers a narrowly defined development question: can longer, *genuinely
resting* limit orders improve TWO-SIDED QUOTE AVAILABILITY in Civitas without
fabricating fills, prices, cash or shares? The only manipulated market setting
is order_ttl. No result here validates real-world market fidelity.

Design: same baseline synthetic initial conditions, background trader policy,
cognitive decision cadence, random order-arrival schedule, rule decision,
agent identities and world seeds for each TTL. Quote measures are taken from
end-of-step order-book snapshots; missing spreads are not treated as zero.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from core.experiments.config import ExperimentConfig, load_config
from core.experiments.runner import ExperimentRunner
from core.experiments.verify import verify_run
from research.civitas_agent.analysis import _bootstrap_mean_ci


METRICS = (
    "two_sided_quote_share",
    "active_step_share",
    "stale_price_share",
    "empty_book_share",
    "bid_only_share",
    "ask_only_share",
    "mean_bid_depth",
    "mean_ask_depth",
    "total_volume",
    "order_rejection_share",
    "ttl_cancellation_share",
)
GATES = {
    "min_two_sided_quote_share": 0.10,
    "min_active_step_share": 0.20,
    "max_stale_price_share": 0.75,
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_values(ttls: Iterable[int], seeds: Iterable[int], *, steps: int, n_agents: int) -> tuple[list[int], list[int]]:
    values = list(ttls)
    worlds = list(seeds)
    if not values or values[0] != 1 or len(set(values)) != len(values) or any(
        type(ttl) is not int or ttl < 1 or ttl > 100 for ttl in values
    ):
        raise ValueError("TTL sensitivity requires unique positive values <=100, first TTL=1")
    if not worlds or len(set(worlds)) != len(worlds) or any(
        type(seed) is not int or not 0 <= seed < 2**32 for seed in worlds
    ):
        raise ValueError("Require independent, unique uint32 world seeds")
    if steps < 6 or steps > 100000 or n_agents < 8 or n_agents > 100000:
        raise ValueError("Invalid sweep horizon or population size")
    return values, worlds


def _build_control(template_path: Path, *, ttl: int, seed: int, steps: int, n_agents: int) -> ExperimentConfig:
    base = load_config(template_path)
    if not base.scientific_world.enabled or base.scientific_world.shocks:
        raise ValueError("A no-news scientific-world template is required to isolate liquidity")
    if base.decision_mode != "cognitive_llm" or base.cognitive.social_mode != "news":
        raise ValueError("Expected an S5 cognitive/no-news research template")
    payload = base.model_dump(mode="json")
    payload.update(
        name=f"s61_ttl_{ttl}_seed_{seed}", seed=seed, steps=steps,
        n_agents=n_agents, decision_mode="cognitive_rule",
    )
    payload["market"]["order_ttl"] = ttl
    payload["market"]["steps_per_day"] = max(steps + 1, 101)
    payload["cognitive"].update(
        decision_policy="random", memory_enabled=False,
        social_enabled=False, reflection_enabled=False,
        social_mode="news", order_schedule="seeded_per_step",
        decision_interval=min(10, steps),
    )
    payload["llm"].update(model="", max_calls=0, max_retries=0)
    return ExperimentConfig.model_validate(payload)


def diagnose(path: Path) -> dict[str, Any]:
    verify_run(path)
    metrics = pd.read_csv(path / "metrics.csv")
    orders = pd.read_csv(path / "orders.csv")
    events = pd.read_csv(path / "order_events.csv")
    if not len(metrics):
        raise ValueError("Empty metrics cannot establish microstructure quality")
    for col in ("step", "price", "volume", "return", "depth_bid_total",
                "depth_ask_total", "spread_pct"):
        if col not in metrics:
            raise ValueError(f"Missing recorded metric {col}")
    obs = metrics[["price", "volume", "return", "depth_bid_total",
                   "depth_ask_total"]].to_numpy(dtype=float)
    if not np.isfinite(obs).all() or np.any(obs[:, [0, 1, 3, 4]] < 0):
        raise ValueError("Non-finite/negative market quantities")
    bid = metrics["depth_bid_total"].to_numpy(float)
    ask = metrics["depth_ask_total"].to_numpy(float)
    volume = metrics["volume"].to_numpy(float)
    ret = metrics["return"].to_numpy(float)
    both = (bid > 0) & (ask > 0)
    none = (bid == 0) & (ask == 0)
    bid_only = (bid > 0) & (ask == 0)
    ask_only = (ask > 0) & (bid == 0)
    if int(both.sum() + none.sum() + bid_only.sum() + ask_only.sum()) != len(metrics):
        raise ValueError("Quote availability partition is not exhaustive")
    if both.any():
        spreads = metrics.loc[both, "spread_pct"].to_numpy(float)
        if not np.isfinite(spreads).all() or np.any(spreads < 0):
            raise ValueError("Conditional spread invalid")
        conditional_spread = float(np.mean(spreads))
    else:
        conditional_spread = None
    order_count = int(len(orders))
    if order_count <= 0:
        raise ValueError("Market has no actual submitted/rejected order records")
    rejected = int((orders["status"].astype(str) == "rejected").sum())
    ttl_cancel = int((
        (events["event"].astype(str) == "cancel")
        & (events["reason"].astype(str) == "time_to_live")
    ).sum())
    account = pd.read_csv(path / "agents.csv")
    if account.empty or account.cash.isna().any() or account.shares.isna().any():
        raise ValueError("Incomplete investor account history")
    # Original ClearingHouse invariant checks were applied after *every* step;
    # here we add independent checks against archived agent and fee trajectories.
    cash_by_step = account.groupby("step").cash.sum().sort_index()
    shares_by_step = account.groupby("step").shares.sum().sort_index()
    fee_by_step = metrics.set_index("step")["fees_cumulative"].sort_index()
    if shares_by_step.nunique() != 1:
        raise ValueError("Asset conservation failed in sensitivity run")
    for step, fees in fee_by_step.items():
        if not math.isclose(float(cash_by_step.loc[step]) + float(fees),
                            float(cash_by_step.iloc[0]), rel_tol=1e-12, abs_tol=1e-6):
            raise ValueError("Cash+fee conservation failed in sensitivity run")
    observed = {
        "steps": int(len(metrics)),
        "two_sided_quote_share": float(np.mean(both)),
        "active_step_share": float(np.mean(volume > 0)),
        "stale_price_share": float(np.mean(np.abs(ret) <= 1e-12)),
        "empty_book_share": float(np.mean(none)),
        "bid_only_share": float(np.mean(bid_only)),
        "ask_only_share": float(np.mean(ask_only)),
        "mean_bid_depth": float(np.mean(bid)),
        "mean_ask_depth": float(np.mean(ask)),
        "mean_spread_pct_when_quoted": conditional_spread,
        "valid_spread_step_count": int(both.sum()),
        "total_volume": float(np.sum(volume)),
        "orders_recorded": order_count,
        "order_rejection_share": rejected / order_count,
        "ttl_cancellation_share": ttl_cancel / order_count,
        "ttl_cancellation_count": ttl_cancel,
        "reported_llm_calls": len(json.loads((path / "model_calls.json").read_text())),
    }
    if observed["reported_llm_calls"]:
        raise ValueError("Offline S6.1 sweep unexpectedly made LLM calls")
    return observed


def _passes(row: dict[str, Any]) -> tuple[bool, list[str]]:
    failed = []
    if row["two_sided_quote_share"] < GATES["min_two_sided_quote_share"]:
        failed.append("LOW_TWO_SIDED_QUOTES")
    if row["active_step_share"] < GATES["min_active_step_share"]:
        failed.append("LOW_TRADE_ACTIVITY")
    if row["stale_price_share"] > GATES["max_stale_price_share"]:
        failed.append("EXCESS_STALE_PRICES")
    return not failed, failed


def run_development(
    output_root: Path, *, template_path: Path, ttls: Iterable[int],
    seeds: Iterable[int], steps: int = 60, n_agents: int = 64,
) -> dict[str, Any]:
    values, worlds = _check_values(ttls, seeds, steps=steps, n_agents=n_agents)
    if output_root.exists():
        raise ValueError("Refusing to overwrite an existing development evidence directory")
    configs = [
        (ttl, seed, _build_control(template_path, ttl=ttl, seed=seed,
                                   steps=steps, n_agents=n_agents))
        for seed in worlds for ttl in values
    ]
    output_root.mkdir(parents=True, exist_ok=False)
    planned = {
        "study": "S6.1 development-only TTL sensitivity",
        "origin": "controlled_synthetic",
        "hypothesis": "Allowing genuine unmatched orders to rest across steps improves both-sided quote availability",
        "variable": "market.order_ttl",
        "ttls": values, "development_seeds": worlds,
        "steps": steps, "n_agents": n_agents,
        "decision_policy": "cognitive_rule/random (25% cohort), remaining vectorized",
        "quality_gates": GATES,
        "template_sha256": _sha(template_path),
        "no_model_calls": True,
    }
    (output_root / "sweep_plan.json").write_text(
        json.dumps(planned, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    results: list[dict[str, Any]] = []
    worlds_by_seed: dict[int, str] = {}
    population_by_seed: dict[int, str] = {}
    source_hashes: set[str] = set()
    for ttl, seed, config in configs:
        path = ExperimentRunner(config).run(output_root / "runs")
        measured = diagnose(path)
        passed, failed = _passes(measured)
        world_sha = _sha(path / "world.json")
        pop_sha = _sha(path / "population.json")
        if seed in worlds_by_seed and worlds_by_seed[seed] != world_sha:
            raise ValueError("Initial world changed across TTL arms")
        if seed in population_by_seed and population_by_seed[seed] != pop_sha:
            raise ValueError("Initial investor population changed across TTL arms")
        worlds_by_seed[seed] = world_sha
        population_by_seed[seed] = pop_sha
        source = json.loads((path / "source.json").read_text())
        source_hashes.add(source["sha256"])
        results.append({
            "seed": seed, "ttl": ttl,
            "run_dir": str(path.resolve()),
            "quality_pass": passed,
            "failed_checks": ",".join(failed),
            **measured,
        })
    if len(source_hashes) != 1:
        raise ValueError("Mixed source-code fingerprints in controlled sweep")
    frame = pd.DataFrame(results).sort_values(["seed", "ttl"])
    frame.to_csv(output_root / "liquidity_per_run.csv", index=False)
    differences: list[dict[str, Any]] = []
    for seed in worlds:
        rows = frame.loc[frame.seed == seed].set_index("ttl")
        baseline = rows.loc[1]
        for ttl in values[1:]:
            variant = rows.loc[ttl]
            for metric in METRICS:
                differences.append({
                    "seed": seed, "ttl": ttl, "reference_ttl": 1,
                    "metric": metric,
                    "difference": float(variant[metric] - baseline[metric]),
                })
    paired = pd.DataFrame(differences, columns=[
        "seed", "ttl", "reference_ttl", "metric", "difference"
    ])
    paired.to_csv(output_root / "paired_ttl_differences.csv", index=False)
    effect_rows: list[dict[str, Any]] = []
    for ttl in values[1:]:
        for metric in METRICS:
            diffs = paired[(paired.ttl == ttl) & (paired.metric == metric)]["difference"].tolist()
            lo, hi = _bootstrap_mean_ci(diffs, 1000, seed=20261028)
            effect_rows.append({
                "ttl": ttl, "metric": metric, "independent_worlds": len(diffs),
                "mean_paired_difference": float(np.mean(diffs)),
                "ci95_lower": lo, "ci95_upper": hi,
                "status": "descriptive_only" if len(diffs) < 5 else "exploratory_bootstrap",
            })
    pd.DataFrame(effect_rows).to_csv(output_root / "ttl_effect_summary.csv", index=False)
    by_ttl = frame.groupby("ttl").agg(
        worlds=("seed", "nunique"),
        passed_worlds=("quality_pass", "sum"),
        min_two_sided=("two_sided_quote_share", "min"),
        mean_two_sided=("two_sided_quote_share", "mean"),
        mean_active=("active_step_share", "mean"),
        mean_ttl_cancel=("ttl_cancellation_share", "mean"),
    ).reset_index()
    by_ttl["all_development_worlds_pass"] = by_ttl["worlds"] == by_ttl["passed_worlds"]
    by_ttl.to_csv(output_root / "ttl_quality_summary.csv", index=False)
    eligible = [
        int(row.ttl) for row in by_ttl.itertuples(index=False)
        if row.all_development_worlds_pass
    ]
    candidate = min(eligible) if eligible else None
    decision = {
        "schema_version": 1,
        "study": planned["study"],
        "recorded_runs": len(results),
        "evaluated_independent_worlds": len(worlds),
        "source_fingerprint": next(iter(source_hashes)),
        "candidate_ttl": candidate,
        "selection_rule": "smallest TTL that passes all pre-declared gates in every development world",
        "development_status": (
            "DEVELOPMENT_CANDIDATE_UNVALIDATED" if candidate is not None
            else "NO_ACCEPTABLE_TTL_IN_DEVELOPMENT"
        ),
        "paid_llm_ready": False,
        "heldout_touched": False,
        "limitations": [
            "Uncalibrated synthetic single-asset market",
            "Control investors are deterministic, not realistic economic agents",
            "No LLM or historical market records in this experiment",
            "An end-of-step quote is not a time-weighted spread observation",
            "Candidate needs independent validation seeds before any LLM pilot",
        ],
        "artifacts": {
            name: _sha(output_root / name)
            for name in (
                "sweep_plan.json", "liquidity_per_run.csv",
                "paired_ttl_differences.csv", "ttl_effect_summary.csv",
                "ttl_quality_summary.csv",
            )
        },
    }
    (output_root / "decision.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return decision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--template", type=Path,
        default=Path("examples/experiments/scientific_s5_no_claim_m1_s1.yaml")
    )
    parser.add_argument("--ttls", default="1,3,8")
    parser.add_argument("--seeds", default="41,42")
    parser.add_argument("--steps", type=int, default=60)
    parser.add_argument("--agents", type=int, default=64)
    args = parser.parse_args()
    result = run_development(
        args.output, template_path=args.template,
        ttls=[int(s) for s in args.ttls.split(",")],
        seeds=[int(s) for s in args.seeds.split(",")],
        steps=args.steps, n_agents=args.agents,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
