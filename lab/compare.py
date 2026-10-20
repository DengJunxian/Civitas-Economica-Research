"""Matched-seed comparison over completed runs of one lab comparison.

Differences are reported per seed and summarised descriptively. Nothing here is
a causal estimate of a real policy: results hold for this model, parameter set
and seed set only. Missing or failed runs are reported, never filled in.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from lab.observables import OUTCOMES, run_outcomes, step_frame
from lab.service import load_manifest
from lab.specs import ScenarioSpec

BOOTSTRAP_RESAMPLES = 2000


def load_scenario_of(root: str | Path) -> ScenarioSpec:
    return ScenarioSpec.model_validate(json.loads((Path(root) / "scenario.json").read_text(encoding="utf-8")))


def comparison_frames(root: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per-step long table, per-run outcome table) for completed runs only."""
    root = Path(root)
    steps, outcomes = [], []
    for entry in load_manifest(root)["runs"]:
        if entry["status"] != "completed":
            continue
        run_dir = root / entry["path"]
        frame = step_frame(run_dir)
        keys = {"group_id": entry["group_id"], "seed": entry["seed"], "run_id": entry["run_id"]}
        steps.append(frame.assign(**keys))
        outcomes.append({**keys, **run_outcomes(run_dir, frame)})
    if not steps:
        return pd.DataFrame(), pd.DataFrame()
    return pd.concat(steps, ignore_index=True), pd.DataFrame(outcomes)


def _describe(values: np.ndarray) -> dict:
    values = values[np.isfinite(values)]
    n = len(values)
    return {"n": n, "mean": float(values.mean()) if n else float("nan"),
            "sd": float(values.std(ddof=1)) if n > 1 else float("nan"),
            "min": float(values.min()) if n else float("nan"), "max": float(values.max()) if n else float("nan")}


def _bootstrap_interval(values: np.ndarray) -> tuple[float, float] | None:
    values = values[np.isfinite(values)]
    if len(values) < 3:
        return None
    rng = np.random.default_rng(0)
    means = rng.choice(values, size=(BOOTSTRAP_RESAMPLES, len(values)), replace=True).mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return float(low), float(high)


def group_summary(outcomes: pd.DataFrame, metric: str) -> pd.DataFrame:
    rows = [{"group_id": g, **_describe(frame[metric].to_numpy(dtype=float))}
            for g, frame in outcomes.groupby("group_id", sort=False)]
    return pd.DataFrame(rows)


def paired_differences(outcomes: pd.DataFrame, metric: str, reference: str) -> pd.DataFrame:
    """Per non-reference group: Y(group, seed) - Y(reference, seed) over seeds present in both."""
    wide = outcomes.pivot(index="seed", columns="group_id", values=metric)
    rows = []
    for group in wide.columns:
        if group == reference or reference not in wide:
            continue
        diff = (wide[group] - wide[reference]).dropna().to_numpy(dtype=float)
        interval = _bootstrap_interval(diff)
        rows.append({"group_id": group, "reference": reference, **_describe(diff),
                     "positive": int((diff > 0).sum()), "negative": int((diff < 0).sum()), "zero": int((diff == 0).sum()),
                     "bootstrap_low": interval[0] if interval else float("nan"),
                     "bootstrap_high": interval[1] if interval else float("nan"),
                     "per_seed": {int(s): float(v) for s, v in (wide[group] - wide[reference]).dropna().items()}})
    return pd.DataFrame(rows)


def factorial_interaction(outcomes: pd.DataFrame, metric: str, spec: ScenarioSpec) -> dict | None:
    f = spec.comparison.factorial
    if f is None:
        return None
    wide = outcomes.pivot(index="seed", columns="group_id", values=metric)
    if not {f.neither, f.a_only, f.b_only, f.both} <= set(wide.columns):
        return {"available": False, "reason": "缺少 2×2 中至少一个完成的实验组"}
    delta = ((wide[f.both] - wide[f.a_only]) - (wide[f.b_only] - wide[f.neither])).dropna()
    values = delta.to_numpy(dtype=float)
    interval = _bootstrap_interval(values)
    return {"available": True, "metric": metric, "factor_a": f.factor_a, "factor_b": f.factor_b,
            "formula": f"[Y({f.both}) - Y({f.a_only})] - [Y({f.b_only}) - Y({f.neither})]",
            **_describe(values), "per_seed": {int(s): float(v) for s, v in delta.items()},
            "bootstrap_interval": interval}


def evidence_card(root: str | Path, metric: str | None = None) -> dict:
    """Facts an explanation may cite: every number carries its run_id and definition."""
    root = Path(root)
    manifest = load_manifest(root)
    spec = load_scenario_of(root)
    metric = metric or spec.outcome.primary
    _, outcomes = comparison_frames(root)
    card = {"comparison_id": manifest["comparison_id"], "scenario_id": spec.scenario_id,
            "scenario_version": spec.version, "scenario_hash": manifest["scenario_hash"],
            "output_origin": manifest["output_origin"], "question": spec.question,
            "metric": metric, "metric_definition": OUTCOMES[metric].definition,
            "failed_runs": [r for r in manifest["runs"] if r["status"] != "completed"],
            "limitations": list(spec.limitations), "assumptions": list(spec.assumptions)}
    if outcomes.empty:
        return {**card, "available": False}
    card["values"] = outcomes[["group_id", "seed", "run_id", metric]].to_dict(orient="records")
    card["group_summary"] = group_summary(outcomes, metric).to_dict(orient="records")
    card["paired_differences"] = paired_differences(outcomes, metric, spec.comparison.reference).to_dict(orient="records")
    card["interaction"] = factorial_interaction(outcomes, metric, spec)
    card["available"] = True
    return card
