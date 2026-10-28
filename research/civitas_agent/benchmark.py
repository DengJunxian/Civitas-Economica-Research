"""S6 guarded eight-arm scorecard: inspectable paired-world comparisons only.

Descriptive results are never automatically promoted to a causal or empirical
conclusion. Confirmatory interpretation requires adequate independent worlds,
passed market-quality gates, real model calls, and external validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.experiments.config import load_config
from core.experiments.verify import verify_run
from research.civitas_agent.analysis import _measure, _s5_measure, _usage, _bootstrap_mean_ci
from research.civitas_agent.matrix import ARM_INDEX, PRIMARY_OUTCOMES, SECONDARY_OUTCOMES, RULE_ARM_KEYS


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_run_dirs(root: Path) -> list[Path]:
    return sorted({p.parent for p in root.rglob("config.json")})


def _validate_arm_config(config_path: Path, sha: str):
    if _digest(config_path) != sha:
        raise ValueError(f"Study arm configuration has changed: {config_path}")
    return load_config(config_path)


def summarize(
    plan_dir: Path, runs_root: Path, output: Path, *,
    partition: str = "development",
    rule_only: bool = False,
    unlock_heldout: bool = False,
    bootstrap: int = 1000,
) -> dict[str, Any]:
    if bootstrap < 100:
        raise ValueError("bootstrap must be >= 100")
    if partition == "heldout_worlds" and not unlock_heldout:
        raise ValueError("Held-out results require an explicit unlock_heldout=True")
    if partition not in {"development", "validation", "heldout_worlds"}:
        raise ValueError("Unrecognized study seed partition")
    plan = json.loads((plan_dir / "study_plan.json").read_text(encoding="utf-8"))
    seeds = plan["seed_partitions"][partition]
    if not seeds:
        raise ValueError("Selected partition contains no seeds")
    expected_rows = [
        row for row in plan["arms"]
        if not rule_only or row["arm"] in RULE_ARM_KEYS
    ]
    expected = {row["arm"]: row for row in expected_rows}
    expected_configs = {
        arm: _validate_arm_config(
            plan_dir / row["config_path"], row["config_sha256"]
        )
        for arm, row in expected.items()
    }
    known_name_to_key = {cfg.name: key for key, cfg in expected_configs.items()}
    indexed: dict[tuple[int, str], Path] = {}
    for root in _find_run_dirs(runs_root):
        raw = json.loads((root / "config.json").read_text(encoding="utf-8"))
        key = known_name_to_key.get(raw.get("name"))
        if key is None or raw.get("seed") not in seeds:
            continue
        index_key = (int(raw["seed"]), key)
        if index_key in indexed:
            raise ValueError(f"Duplicate run for world/arm: {index_key}")
        indexed[index_key] = root
    missing = [
        (seed, key) for seed in seeds for key in expected
        if (seed, key) not in indexed
    ]
    if missing:
        raise ValueError(f"Incomplete study partition; missing: {missing[:10]}")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new/empty directory for benchmark analysis")

    world_hashes: dict[int, str] = {}
    population_hashes: dict[int, str] = {}
    cohort_ids: dict[int, list[str]] = {}
    source_hashes: set[str] = set()
    rows = []
    for seed in seeds:
        for key, meta in expected.items():
            root = indexed[(seed, key)]
            cfg = expected_configs[key]
            actual = json.loads((root / "config.json").read_text(encoding="utf-8"))
            expected_cfg = cfg.model_copy(update={"seed": seed}).model_dump(mode="json")
            if actual != expected_cfg:
                raise ValueError(f"Run configuration mismatches frozen plan: {root}")
            verify_run(root)
            snapshot_hash = _digest(root / "world.json")
            population_hash = _digest(root / "population.json")
            cohort = json.loads((root / "cognitive_protocol.json").read_text())["active_llm_agent_ids"]
            if seed in world_hashes and world_hashes[seed] != snapshot_hash:
                raise ValueError(f"Initial world differs across paired arms at seed={seed}")
            if seed in population_hashes and population_hashes[seed] != population_hash:
                raise ValueError(f"Initial population differs at seed={seed}")
            if seed in cohort_ids and cohort_ids[seed] != cohort:
                raise ValueError(f"Cognitive cohort differs at seed={seed}")
            world_hashes[seed] = snapshot_hash
            population_hashes[seed] = population_hash
            cohort_ids[seed] = cohort
            source = json.loads((root / "source.json").read_text(encoding="utf-8"))
            source_hashes.add(source["sha256"])
            protocol = json.loads((root / "cognitive_protocol.json").read_text())
            measurement = _measure(root)
            if not cfg.scientific_world.enabled:
                raise ValueError("S6 requires controlled synthetic scientific worlds")
            scientific, _ = _s5_measure(root, cfg, len(cohort))
            usage = _usage(root)
            if protocol["logical_model_calls"] != meta["logical_llm_calls_per_world"]:
                raise ValueError(f"Unexpected cognitive model-call count for {root}")
            if meta["mode"] == "cognitive_rule":
                if usage["model_call_records"] != 0:
                    raise ValueError("Deterministic control unexpectedly made model calls")
            else:
                if usage["model_call_records"] < meta["logical_llm_calls_per_world"]:
                    raise ValueError("Missing recorded LLM calls")
            quality = plan["quality_gates"]
            problems = []
            if measurement["active_step_share"] < quality["min_active_step_share"]:
                problems.append("INSUFFICIENT_ACTIVE_MARKET_STEPS")
            if measurement["two_sided_quote_share"] < quality["min_two_sided_quote_share"]:
                problems.append("INSUFFICIENT_TWO_SIDED_QUOTES")
            if measurement["stale_price_share"] > quality["max_stale_price_share"]:
                problems.append("TOO_MANY_STALE_PRICE_STEPS")
            rows.append({
                "seed": seed, "arm": key, "run_dir": str(root.resolve()),
                "decision_policy": meta["policy"],
                "model_calls_expected": meta["logical_llm_calls_per_world"],
                "model_calls_observed": protocol["logical_model_calls"],
                "cohort_size": len(cohort),
                "quality_gate_pass": not problems,
                "quality_gate_failure_reasons": ",".join(problems),
                **measurement, **scientific, **usage,
            })
    if len(source_hashes) != 1:
        raise ValueError("Mixed source fingerprints within a frozen study partition")

    ref = "B2_claim" if rule_only else plan["baseline_contrast_reference"]
    if ref not in expected:
        raise ValueError(f"Reference arm {ref} is missing")
    outcomes = tuple(plan["primary_outcomes"]) + tuple(plan["secondary_outcomes"])
    contrast_rows: list[dict[str, Any]] = []
    for seed in seeds:
        per_seed = {row["arm"]: row for row in rows if row["seed"] == seed}
        for key in expected:
            if key == ref:
                continue
            for name in outcomes:
                contrast_rows.append({
                    "seed": seed, "arm": key, "reference": ref,
                    "metric": name,
                    "value": float(per_seed[key][name]),
                    "reference_value": float(per_seed[ref][name]),
                    "paired_difference": float(per_seed[key][name] - per_seed[ref][name]),
                })
    summary = []
    for key in expected:
        if key == ref:
            continue
        for name in outcomes:
            differences = [
                item["paired_difference"] for item in contrast_rows
                if item["arm"] == key and item["metric"] == name
            ]
            lo, hi = _bootstrap_mean_ci(differences, bootstrap, seed=20261028)
            summary.append({
                "arm": key, "reference": ref, "metric": name,
                "world_count": len(differences),
                "mean_paired_difference": float(np.mean(differences)),
                "ci95_lower": lo, "ci95_upper": hi,
                "inference": (
                    "descriptive_only" if len(differences) < 5
                    else "exploratory_world_bootstrap"
                ),
            })
    any_failed_gate = any(not row["quality_gate_pass"] for row in rows)
    output.mkdir(parents=True, exist_ok=True)
    tables = {
        "baseline_per_run.csv": pd.DataFrame(rows).sort_values(["seed", "arm"]),
        "paired_baseline_contrasts.csv": pd.DataFrame(contrast_rows).sort_values(
            ["metric", "seed", "arm"]
        ),
        "baseline_effect_summary.csv": pd.DataFrame(summary).sort_values(
            ["metric", "arm"]
        ),
    }
    for name, dataframe in tables.items():
        dataframe.to_csv(output / name, index=False)
    manifest = {
        "schema_version": 1,
        "study_type": "S6_baseline_comparison",
        "study_status": "offline_control_smoke_only" if rule_only else "exploratory_pilot",
        "partition": partition,
        "quality_status": (
            "UNSUITABLE_FOR_MARKET_REALISM_CLAIMS" if any_failed_gate
            else "PILOT_QUALITY_GATES_PASSED_NOT_EMPIRICALLY_VALIDATED"
        ),
        "contains_confirmatory_inference": False,
        "contains_real_market_validation": False,
        "contains_paid_models": not rule_only,
        "independent_worlds": len(seeds),
        "arm_count": len(expected),
        "reference_arm": ref,
        "source_fingerprint": next(iter(source_hashes)),
        "quality_gate_failures": sum(not row["quality_gate_pass"] for row in rows),
        "table_sha256": {name: _digest(output / name) for name in tables},
        "inference_warning": (
            "S6 model baselines differ in complexity and API cost; "
            "market fidelity, behavior and computational expense must be "
            "reported separately. Control fake-model tests are not scientific evidence."
        ),
    }
    (output / "benchmark_manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--runs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--partition", default="development",
                        choices=["development", "validation", "heldout_worlds"])
    parser.add_argument("--rule-only", action="store_true")
    parser.add_argument("--unlock-heldout", action="store_true")
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    result = summarize(
        args.plan, args.runs, args.output,
        partition=args.partition, rule_only=args.rule_only,
        unlock_heldout=args.unlock_heldout, bootstrap=args.bootstrap,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
