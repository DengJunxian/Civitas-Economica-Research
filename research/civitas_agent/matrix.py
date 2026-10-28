"""S6 study-plan builder: no API calls and no experiments at plan time.

Generate fixed matched-world YAML configs and an inspectable draft protocol.
The plan is an INTERNAL design freeze, not externally preregistered research.
Use explicit CLI experiments later; this module never launches paid models.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.experiments.config import ExperimentConfig, load_config


@dataclass(frozen=True)
class Arm:
    key: str
    mode: str
    policy: str
    memory: bool
    social: bool
    reflection: bool


ARMS = (
    Arm("B0_random", "cognitive_rule", "random", False, False, False),
    Arm("B1_profile", "cognitive_rule", "profile_rule", False, False, False),
    Arm("B2_claim", "cognitive_rule", "claim_rule", True, True, False),
    Arm("B3_llm_stateless", "cognitive_llm", "llm", False, False, False),
    Arm("B4_llm_memory", "cognitive_llm", "llm", True, False, False),
    Arm("B5_llm_social", "cognitive_llm", "llm", False, True, False),
    Arm("B6_llm_full", "cognitive_llm", "llm", True, True, False),
    Arm("B7_llm_reflection", "cognitive_llm", "llm", True, True, True),
)
ARM_INDEX = {arm.key: arm for arm in ARMS}
RULE_ARM_KEYS = tuple(arm.key for arm in ARMS if arm.mode == "cognitive_rule")
PRIMARY_OUTCOMES = (
    "mean_abs_fundamental_deviation",
    "active_step_share",
    "two_sided_quote_share",
)
SECONDARY_OUTCOMES = (
    "final_abs_fundamental_deviation",
    "realized_volatility",
    "total_volume",
    "mean_depth",
    "information_reach_share",
)


def _parse_seeds(raw: str) -> list[int]:
    if not raw.strip():
        return []
    values = [int(token.strip()) for token in raw.split(",")]
    if (len(set(values)) != len(values)
            or any(value < 0 or value >= 2 ** 32 for value in values)):
        raise ValueError("Seeds must be unique unsigned 32-bit integers")
    return values


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_plan(
    template_path: Path,
    output: Path,
    *,
    dev_seeds: list[int],
    validation_seeds: list[int],
    test_seeds: list[int],
    min_active_step_share: float = 0.2,
    min_two_sided_quote_share: float = 0.10,
    max_stale_price_share: float = 0.75,
) -> dict:
    all_seeds = dev_seeds + validation_seeds + test_seeds
    if not all_seeds or len(all_seeds) != len(set(all_seeds)):
        raise ValueError("Provide at least one seed and disjoint seed partitions")
    if any(not 0 <= s < 2 ** 32 for s in all_seeds):
        raise ValueError("Out-of-range world seed")
    limits = (
        min_active_step_share, min_two_sided_quote_share, max_stale_price_share
    )
    if any(not 0 <= v <= 1 for v in limits):
        raise ValueError("Quality thresholds must lie in [0,1]")
    base = load_config(template_path)
    if not base.scientific_world.enabled or base.decision_mode != "cognitive_llm":
        raise ValueError("S6 matrix requires an S5-enabled cognitive_llm template")
    if base.cognitive.social_mode != "news":
        raise ValueError("S6 information study requires social_mode=news")
    if base.cognitive.reflection_enabled:
        raise ValueError("Template must have reflection disabled; B7 enables it")
    if base.cognitive.order_schedule != "seeded_per_step":
        raise ValueError("S6 requires seeded_per_step order arrival")
    if not base.scientific_world.shocks:
        raise ValueError("S6 mechanism matrix needs at least one controlled shock")
    if base.llm.model.strip() == "":
        raise ValueError("Name a fixed LLM model in the S6 template")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Study plan destination must be an empty/new directory")

    configs = []
    for arm in ARMS:
        payload = base.model_dump(mode="json")
        payload["name"] = f"s6_{arm.key}"
        payload["decision_mode"] = arm.mode
        payload["cognitive"].update(
            decision_policy=arm.policy,
            memory_enabled=arm.memory,
            social_enabled=arm.social,
            reflection_enabled=arm.reflection,
        )
        if arm.mode == "cognitive_rule":
            payload["llm"].update(model="", max_calls=0)
        else:
            cohort = max(1, math.ceil(base.n_agents * base.cognitive.llm_share))
            decisions = 1 + (base.steps - 1) // base.cognitive.decision_interval
            reflections = (
                (base.steps - 1) // base.cognitive.reflection_interval
                if arm.reflection else 0
            )
            # This is a fixed *logical request* budget, not an estimated price
            # or a provider-side retry budget. No silent extra model calls.
            payload["llm"]["max_calls"] = cohort * (decisions + reflections)
        cfg = ExperimentConfig.model_validate(payload)
        configs.append((arm, cfg, f"configs/{cfg.name}.yaml"))

    output.mkdir(parents=True, exist_ok=False)
    (output / "configs").mkdir()
    rows = []
    for arm, cfg, relative in configs:
        filepath = output / relative
        filepath.write_text(
            yaml.safe_dump(
                cfg.model_dump(mode="json"), allow_unicode=True, sort_keys=False
            ),
            encoding="utf-8",
        )
        rows.append({
            "arm": arm.key,
            "mode": arm.mode,
            "policy": arm.policy,
            "memory_enabled": arm.memory,
            "social_enabled": arm.social,
            "reflection_enabled": arm.reflection,
            "config_path": relative,
            "config_sha256": _sha(filepath),
            "logical_llm_calls_per_world": (
                cfg.llm.max_calls if arm.mode == "cognitive_llm" else 0
            ),
        })

    result = {
        "schema_version": 1,
        "study_type": "S6_mechanism_pilot",
        "status": "internal_design_draft_not_externally_preregistered",
        "data_origin": "controlled_synthetic_world",
        "scenario": base.scientific_world.model_dump(mode="json"),
        "study_steps": base.steps,
        "n_agents": base.n_agents,
        "llm_cohort_size": max(1, math.ceil(base.n_agents * base.cognitive.llm_share)),
        "llm_model_identifier": base.llm.model,
        "seed_partitions": {
            "development": list(dev_seeds),
            "validation": list(validation_seeds),
            "heldout_worlds": list(test_seeds),
        },
        "primary_outcomes": list(PRIMARY_OUTCOMES),
        "secondary_outcomes": list(SECONDARY_OUTCOMES),
        "quality_gates": {
            "min_active_step_share": min_active_step_share,
            "min_two_sided_quote_share": min_two_sided_quote_share,
            "max_stale_price_share": max_stale_price_share,
            "requires_all_worlds_pass": True,
        },
        "statistical_unit": "independent simulation world (not agent-step)",
        "factorial_estimand": "Y_M1S1 - Y_M1S0 - Y_M0S1 + Y_M0S0",
        "baseline_contrast_reference": "B6_llm_full",
        "arms": rows,
        "logical_llm_calls_per_seed": sum(
            row["logical_llm_calls_per_world"] for row in rows
        ),
        "total_logical_llm_calls_all_seed_partitions": sum(
            row["logical_llm_calls_per_world"] for row in rows
        ) * len(all_seeds),
        "provider_retry_calls_not_included": True,
        "limitations": [
            "No paid inference or real market data at plan generation",
            "Rule policies deliberately differ in functional complexity from LLMs",
            "Real-market calibration and held-out historical periods remain separate",
            "Quality gates are pilot diagnostics, not thresholds derived from real data",
            "Do not inspect heldout_worlds while selecting model or simulation parameters",
        ],
    }
    (output / "study_plan.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def run_rule_controls(plan_dir: Path, destination: Path, *, partition: str) -> list[str]:
    """Run **only** offline deterministic controls; never instantiate a model."""
    from core.experiments.runner import ExperimentRunner
    from core.experiments.verify import verify_run

    plan = json.loads((plan_dir / "study_plan.json").read_text())
    if partition not in ("development", "validation", "heldout_worlds"):
        raise ValueError("Unknown seed partition")
    seeds = plan["seed_partitions"][partition]
    if not seeds:
        raise ValueError(f"No seeds in partition {partition}")
    destination.mkdir(parents=True, exist_ok=True)
    result = []
    for row in plan["arms"]:
        if row["mode"] != "cognitive_rule":
            continue
        file = plan_dir / row["config_path"]
        if _sha(file) != row["config_sha256"]:
            raise ValueError(f"Tampered frozen research arm configuration: {file}")
        template = load_config(file)
        for seed in seeds:
            config = template.model_copy(update={"seed": seed})
            if config.decision_mode != "cognitive_rule":
                raise ValueError("Offline study runner refuses model-based arms")
            path = ExperimentRunner(config).run(destination)
            verify_run(path)
            result.append(str(path))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dev-seeds", default="41,42")
    parser.add_argument("--val-seeds", default="43,44")
    parser.add_argument("--heldout-seeds", default="45,46,47,48,49")
    args = parser.parse_args()
    result = build_plan(
        args.template,
        args.output,
        dev_seeds=_parse_seeds(args.dev_seeds),
        validation_seeds=_parse_seeds(args.val_seeds),
        test_seeds=_parse_seeds(args.heldout_seeds),
    )
    print(json.dumps({
        "plan_path": str(args.output / "study_plan.json"),
        "arms": [arm["arm"] for arm in result["arms"]],
        "logical_llm_calls_per_seed": result["logical_llm_calls_per_seed"],
        "total_logical_llm_calls": result["total_logical_llm_calls_all_seed_partitions"],
        "provider_calls_launched": 0,
    }, indent=2))


if __name__ == "__main__":
    main()
