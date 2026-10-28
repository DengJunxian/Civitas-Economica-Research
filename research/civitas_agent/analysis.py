"""Offline, paired-world evidence tables for CivitasAgent factorial experiments.

This module does not run models or interpret simulated outcomes as real data.
Repeated time steps and investors are not treated as independent samples.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.experiments.config import ExperimentConfig
from core.experiments.verify import verify_run


OUTCOMES = (
    "active_step_share",
    "two_sided_quote_share",
    "stale_price_share",
    "mean_abs_return",
    "realized_volatility",
    "total_volume",
    "mean_depth",
)

S5_OUTCOMES = (
    "mean_abs_fundamental_deviation",
    "final_abs_fundamental_deviation",
    "information_reach_share",
)


def _discover(root: Path) -> list[Path]:
    candidates = [root] if (root / "config.json").is_file() else [
        f.parent for f in root.rglob("config.json")
    ]
    return sorted(set(candidates))


def _harmonized_config(cfg: ExperimentConfig) -> str:
    payload = cfg.model_dump(mode="json")
    payload.pop("name")
    payload.pop("seed")
    payload["cognitive"].pop("memory_enabled")
    payload["cognitive"].pop("social_enabled")
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _arm(cfg: ExperimentConfig) -> str:
    return f"M{int(cfg.cognitive.memory_enabled)}S{int(cfg.cognitive.social_enabled)}"


def _measure(path: Path) -> dict[str, Any]:
    metrics = pd.read_csv(path / "metrics.csv")
    if metrics.empty:
        raise ValueError(f"Empty market metrics: {path}")
    for column in ("return", "price", "volume", "depth_bid_total", "depth_ask_total"):
        if column not in metrics:
            raise ValueError(f"Missing required column {column} in {path}")
    if not np.isfinite(metrics[["return", "price", "volume",
                                "depth_bid_total", "depth_ask_total"]].to_numpy(dtype=float)).all():
        raise ValueError(f"Non-finite financial metric in {path}")
    ret = metrics["return"].to_numpy(dtype=float)
    volume = metrics["volume"].to_numpy(dtype=float)
    bid = metrics["depth_bid_total"].to_numpy(dtype=float)
    ask = metrics["depth_ask_total"].to_numpy(dtype=float)
    traded = volume > 0
    quoted = (bid > 0) & (ask > 0)
    if np.any(volume < 0) or np.any(bid < 0) or np.any(ask < 0):
        raise ValueError(f"Negative market quantities in {path}")
    rows = {
        "steps": len(metrics),
        "active_step_share": float(np.mean(traded)),
        "two_sided_quote_share": float(np.mean(quoted)),
        "stale_price_share": float(np.mean(np.abs(ret) <= 1e-12)),
        "mean_abs_return": float(np.mean(np.abs(ret))),
        "realized_volatility": float(np.std(ret, ddof=1)) if len(ret) > 1 else 0.0,
        "total_volume": float(np.sum(volume)),
        "mean_depth": float(np.mean(bid + ask)),
        "valid_spread_observations": int(quoted.sum()),
        "mean_spread_pct_when_quoted": (
            float(np.mean(metrics.loc[quoted, "spread_pct"]))
            if quoted.any() and "spread_pct" in metrics else None
        ),
    }
    rows["quality_flags"] = ",".join([
        flag for condition, flag in (
            (not traded.any(), "NO_EXECUTED_TRADES"),
            (not quoted.any(), "NO_TWO_SIDED_QUOTES"),
            ((~traded).mean() > 0.75, "OVER_75PCT_INACTIVE_STEPS"),
            (rows["stale_price_share"] > 0.75, "OVER_75PCT_STALE_PRICE_STEPS"),
        ) if condition
    ])
    return rows


def _s5_measure(path: Path, cfg: ExperimentConfig, cohort_size: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Synthetic benchmark comparison; it is not an empirical price target."""
    market = pd.read_csv(path / "metrics.csv")
    ground = pd.read_csv(path / "study_fundamentals.csv")
    if len(market) != len(ground) or not (market.step == ground.step).all():
        raise ValueError(f"Misaligned market/fundamental paths in {path}")
    deviation = market["absolute_price_deviation"].to_numpy(dtype=float)
    if not np.isfinite(deviation).all() or np.any(deviation < 0):
        raise ValueError(f"Invalid synthetic benchmark deviation in {path}")
    deliveries = [
        json.loads(line) for line in (path / "study_news_exposures.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    shocks = sorted(cfg.scientific_world.shocks, key=lambda x: x.step)
    reach = []
    windows: list[dict[str, Any]] = []
    for index, shock in enumerate(shocks):
        seen = {
            row["agent_id"] for row in deliveries
            if row["shock_id"] == shock.shock_id
        }
        fraction = len(seen) / cohort_size
        reach.append(fraction)
        next_step = shocks[index + 1].step if index + 1 < len(shocks) else cfg.steps + 1
        pre = market[(market.step >= max(1, shock.step - 5)) & (market.step < shock.step)]
        rumor = market[(market.step >= shock.step) & (market.step < shock.reveal_step)]
        post = market[(market.step >= shock.reveal_step) &
                      (market.step <= min(cfg.steps, shock.reveal_step + 5, next_step - 1))]
        windows.append({
            "run_dir": str(path.resolve()),
            "shock_id": shock.shock_id,
            "source_id": shock.source_id,
            "claim_step": shock.step,
            "reveal_step": shock.reveal_step,
            "verdict": "supported" if shock.truthful else "refuted",
            "pre_mean_abs_deviation": (
                float(pre.absolute_price_deviation.mean()) if len(pre) else None
            ),
            "unverified_mean_abs_deviation": (
                float(rumor.absolute_price_deviation.mean()) if len(rumor) else None
            ),
            "post_verification_mean_abs_deviation": (
                float(post.absolute_price_deviation.mean()) if len(post) else None
            ),
            "cognitive_reach_share": fraction,
            "causal_interpretation": "descriptive_window_only",
        })
    return ({
        "mean_abs_fundamental_deviation": float(np.mean(deviation)),
        "final_abs_fundamental_deviation": float(deviation[-1]),
        "information_reach_share": float(np.mean(reach)) if reach else 0.0,
        "shock_count": len(shocks),
    }, windows)


def _usage(path: Path) -> dict[str, Any]:
    calls = json.loads((path / "model_calls.json").read_text(encoding="utf-8"))
    tokens = [
        call.get("usage", {}).get("total_tokens") for call in calls
        if isinstance(call.get("usage"), dict)
    ]
    tokens = [int(t) for t in tokens if t is not None]
    return {
        "model_call_records": len(calls),
        "model_failed_calls": sum(not bool(call.get("ok")) for call in calls),
        "total_tokens_reported": sum(tokens) if tokens else None,
    }


def _bootstrap_mean_ci(values: list[float], reps: int, seed: int) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    x = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.choice(x, size=(reps, len(x)), replace=True).mean(axis=1)
    lower, upper = np.quantile(draws, [0.025, 0.975])
    return float(lower), float(upper)


def analyze(root: Path, output: Path, *, bootstrap: int = 1000) -> dict[str, Any]:
    if bootstrap < 100:
        raise ValueError("bootstrap must be >= 100")
    paths = _discover(root)
    if not paths:
        raise ValueError(f"No experiment config.json found beneath {root}")
    runs: list[dict[str, Any]] = []
    event_windows: list[dict[str, Any]] = []
    signatures: set[str] = set()
    s5_enabled: bool | None = None
    for path in paths:
        raw = json.loads((path / "config.json").read_text(encoding="utf-8"))
        if raw.get("decision_mode") != "cognitive_llm":
            continue
        cfg = ExperimentConfig.model_validate(raw)
        verify_run(path)  # Fail rather than silently analyze tampered archives.
        if s5_enabled is None:
            s5_enabled = cfg.scientific_world.enabled
        if s5_enabled != cfg.scientific_world.enabled:
            raise ValueError("Cannot mix scientific and legacy cognitive runs")
        signatures.add(_harmonized_config(cfg))
        protocol = json.loads((path / "cognitive_protocol.json").read_text(encoding="utf-8"))
        row = {
            "run_dir": str(path.resolve()), "seed": cfg.seed, "arm": _arm(cfg),
            "llm_cohort_size": len(protocol["active_llm_agent_ids"]),
            "logical_model_calls": protocol["logical_model_calls"],
            **_measure(path), **_usage(path),
        }
        if row["steps"] != cfg.steps:
            raise ValueError(f"Step count mismatch in {path}")
        if cfg.scientific_world.enabled:
            study, windows = _s5_measure(path, cfg, row["llm_cohort_size"])
            row.update(study)
            for window in windows:
                event_windows.append({**window, "seed": cfg.seed, "arm": _arm(cfg)})
        runs.append(row)
    if not runs:
        raise ValueError("No completed cognitive_llm runs were found")
    if len(signatures) != 1:
        raise ValueError("Arms differ beyond seed, experiment name and memory/social treatment")
    groups: dict[int, dict[str, dict[str, Any]]] = {}
    for row in runs:
        by_arm = groups.setdefault(row["seed"], {})
        if row["arm"] in by_arm:
            raise ValueError(f"Duplicate paired arm: seed={row['seed']} arm={row['arm']}")
        by_arm[row["arm"]] = row
    expected = {"M0S0", "M1S0", "M0S1", "M1S1"}
    for seed, arms in groups.items():
        if set(arms) != expected:
            raise ValueError(f"Incomplete factorial world seed={seed}: missing {sorted(expected-set(arms))}")
    metrics_to_compare = OUTCOMES + (S5_OUTCOMES if s5_enabled else ())
    contrasts: list[dict[str, Any]] = []
    for seed, arms in sorted(groups.items()):
        for metric in metrics_to_compare:
            y00 = float(arms["M0S0"][metric])
            y10 = float(arms["M1S0"][metric])
            y01 = float(arms["M0S1"][metric])
            y11 = float(arms["M1S1"][metric])
            contrasts.append({
                "seed": seed, "metric": metric,
                "memory_effect_at_s0": y10 - y00,
                "social_effect_at_m0": y01 - y00,
                "interaction": y11 - y10 - y01 + y00,
                "y00": y00, "y10": y10, "y01": y01, "y11": y11,
            })
    summary: list[dict[str, Any]] = []
    for metric in metrics_to_compare:
        rows = [r for r in contrasts if r["metric"] == metric]
        for effect in ("memory_effect_at_s0", "social_effect_at_m0", "interaction"):
            values = [float(r[effect]) for r in rows]
            lo, hi = _bootstrap_mean_ci(values, bootstrap, seed=20261028)
            summary.append({
                "metric": metric, "effect": effect, "independent_worlds": len(values),
                "mean_effect": float(np.mean(values)), "ci95_lower": lo, "ci95_upper": hi,
                "inferential_status": "descriptive_only" if len(values) < 5 else "exploratory_bootstrap",
            })
    output.mkdir(parents=True, exist_ok=True)
    files = {
        "per_run.csv": pd.DataFrame(runs).sort_values(["seed", "arm"]),
        "paired_effects.csv": pd.DataFrame(contrasts).sort_values(["metric", "seed"]),
        "effect_summary.csv": pd.DataFrame(summary).sort_values(["metric", "effect"]),
    }
    if s5_enabled:
        files["shock_windows.csv"] = pd.DataFrame(event_windows, columns=[
            "run_dir", "seed", "arm", "shock_id", "source_id", "claim_step",
            "reveal_step", "verdict", "pre_mean_abs_deviation",
            "unverified_mean_abs_deviation",
            "post_verification_mean_abs_deviation", "cognitive_reach_share",
            "causal_interpretation",
        ]).sort_values(["seed", "arm", "claim_step"])
    for filename, frame in files.items():
        frame.to_csv(output / filename, index=False)
    manifest = {
        "schema_version": 1,
        "research": "CivitasAgent Memory x Social, paired independent worlds",
        "origin": "simulation",
        "source_root": str(root.resolve()),
        "run_count": len(runs),
        "complete_worlds": len(groups),
        "effect_definitions": {
            "interaction": "Y11-Y10-Y01+Y00",
            "memory_effect_at_s0": "Y10-Y00",
            "social_effect_at_m0": "Y01-Y00",
        },
        "world_type": "controlled_synthetic_benchmark" if s5_enabled else "market_only",
        "limitations": [
            ("Synthetic latent fundamental is not empirically calibrated"
             if s5_enabled else "No latent fundamental or empirical validation"),
            "No scientific claims from controlled fake-LLM tests",
            "Agent/time-step observations are not independent replicates",
            "Inactivity and missing two-sided quotes must be inspected",
            "Shock windows are descriptive and do not identify effects without control arms",
        ],
        "artifacts_sha256": {
            name: hashlib.sha256((output / name).read_bytes()).hexdigest()
            for name in files
        },
    }
    (output / "analysis_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True, help="Root with four-arm experiment directories")
    parser.add_argument("--output", type=Path, required=True, help="Directory for inspectable effect tables")
    parser.add_argument("--bootstrap", type=int, default=1000)
    args = parser.parse_args()
    print(json.dumps(analyze(args.runs, args.output, bootstrap=args.bootstrap), indent=2))


if __name__ == "__main__":
    main()
