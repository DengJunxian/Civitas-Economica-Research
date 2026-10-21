"""ScenarioSpec -> core ExperimentConfig, one per (group, seed).

Only fields the core schema owns are emitted. Question, objectives,
observables, approval and review stay in the scenario document and are linked
to runs through the lab manifest, never written into core configs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.experiments.config import ExperimentConfig
from core.reproducibility import stable_hash
from lab.capability import ordered_events
from lab.specs import GroupSpec, ScenarioSpec


@dataclass(frozen=True)
class CompiledRun:
    group_id: str
    seed: int
    config: ExperimentConfig


def scenario_hash(spec: ScenarioSpec) -> str:
    return stable_hash(spec.model_dump(mode="json"))


def _run_name(spec: ScenarioSpec, group: GroupSpec, seed: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", spec.scenario_id).strip("_").lower()
    return f"{slug}_{group.id}_s{seed}"[:64]


def _merged(base, override) -> dict:
    return {**base.model_dump(exclude_none=True), **override.model_dump(exclude_none=True)}


def compile_group(spec: ScenarioSpec, group: GroupSpec, seed: int) -> ExperimentConfig:
    world = spec.world
    payload = {
        "schema_version": 1,
        "name": _run_name(spec, group, seed),
        "mode": "research",
        "seed": seed,
        "steps": world.steps,
        "n_agents": world.agents,
        "decision_mode": world.decision_mode,
        "market_regime": world.market_regime,
        "market": {"initial_price": world.initial_price, **_merged(world.market, group.market)},
        "social": _merged(world.social, group.social),
        "events": [{"step": e.step, "kind": e.kind, "value": 0.0 if e.kind in {"halt", "resume"} else e.value}
                   for e in ordered_events(group.events)],
    }
    return ExperimentConfig.model_validate(payload)


def compile_scenario(spec: ScenarioSpec) -> list[CompiledRun]:
    """Seed-major order so that interrupted batches still hold complete pairs."""
    return [CompiledRun(group.id, seed, compile_group(spec, group, seed))
            for seed in spec.comparison.seeds for group in spec.comparison.groups]
