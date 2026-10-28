"""S6 frozen design, world-level scorecard and zero-API offline runner tests."""
import json

import pytest
import yaml

from core.experiments.config import ExperimentConfig
from research.civitas_agent.matrix import (
    ARMS, RULE_ARM_KEYS, build_plan, run_rule_controls,
)
from research.civitas_agent.benchmark import summarize


def template(tmp_path):
    path = tmp_path / "template.yaml"
    config = ExperimentConfig.model_validate({
        "name": "s6_template", "mode": "research",
        "seed": 11, "n_agents": 12, "steps": 6,
        "decision_mode": "cognitive_llm",
        "cognitive": {
            "decision_policy": "llm",
            "llm_share": 0.25,
            "decision_interval": 1,
            "memory_enabled": True,
            "social_enabled": True,
            "social_mode": "news",
            "order_schedule": "seeded_per_step",
            "reflection_enabled": False,
            "reflection_interval": 2,
        },
        "llm": {"model": "chosen-model", "provider": "deepseek", "max_calls": 18},
        "scientific_world": {
            "enabled": True,
            "shocks": [{
                "shock_id": "c1", "source_id": "sourceA", "step": 2,
                "reveal_step": 5, "polarity": -1,
                "magnitude": 0.03, "truthful": False,
                "direct_fraction": 0.5,
            }],
        },
    })
    path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False))
    return path


def test_s6_plan_is_valid_explicit_and_does_not_launch_provider(tmp_path):
    result = build_plan(
        template(tmp_path), tmp_path / "planned",
        dev_seeds=[41], validation_seeds=[42], test_seeds=[43],
    )
    assert result["status"] == "internal_design_draft_not_externally_preregistered"
    assert result["seed_partitions"]["heldout_worlds"] == [43]
    assert len(result["arms"]) == len(ARMS) == 8
    assert sum(row["mode"] == "cognitive_rule" for row in result["arms"]) == 3
    assert result["logical_llm_calls_per_seed"] == 5 * 18 + 3 * 2
    assert result["total_logical_llm_calls_all_seed_partitions"] == 3 * (5 * 18 + 3 * 2)
    assert result["primary_outcomes"][0] == "mean_abs_fundamental_deviation"
    assert not list((tmp_path / "planned").rglob("metrics.csv"))
    for row in result["arms"]:
        from core.experiments.config import load_config
        cfg = load_config(tmp_path / "planned" / row["config_path"])
        assert cfg.name == "s6_" + row["arm"]
        if row["arm"] in RULE_ARM_KEYS:
            assert cfg.decision_mode == "cognitive_rule"
            assert cfg.llm.max_calls == 0
            assert cfg.llm.model == ""
        else:
            assert cfg.decision_mode == "cognitive_llm"
            assert cfg.llm.model == "chosen-model"
    assert json.loads((tmp_path / "planned" / "study_plan.json").read_text()) == result


def test_offline_controls_run_and_scorecard_is_descriptive(tmp_path):
    plan_dir = tmp_path / "plan"
    build_plan(template(tmp_path), plan_dir, dev_seeds=[41],
               validation_seeds=[42], test_seeds=[43])
    paths = run_rule_controls(
        plan_dir, tmp_path / "runs", partition="development"
    )
    assert len(paths) == 3
    result = summarize(
        plan_dir, tmp_path / "runs", tmp_path / "scorecard",
        rule_only=True, partition="development", bootstrap=100,
    )
    assert result["study_status"] == "offline_control_smoke_only"
    assert result["contains_confirmatory_inference"] is False
    assert result["contains_real_market_validation"] is False
    assert result["independent_worlds"] == 1
    assert result["arm_count"] == 3
    assert result["reference_arm"] == "B2_claim"
    import pandas as pd
    per_run = pd.read_csv(tmp_path / "scorecard" / "baseline_per_run.csv")
    paired = pd.read_csv(tmp_path / "scorecard" / "paired_baseline_contrasts.csv")
    effects = pd.read_csv(tmp_path / "scorecard" / "baseline_effect_summary.csv")
    assert len(per_run) == 3 and set(per_run.arm) == set(RULE_ARM_KEYS)
    assert per_run.model_calls_observed.eq(0).all()
    assert effects.inference.eq("descriptive_only").all()
    assert len(paired) == 2 * len(
        json.loads((plan_dir / "study_plan.json").read_text())["primary_outcomes"] +
        json.loads((plan_dir / "study_plan.json").read_text())["secondary_outcomes"]
    )
    assert (tmp_path / "scorecard" / "benchmark_manifest.json").exists()


def test_s6_heldout_guard_and_config_integrity(tmp_path):
    plan_dir = tmp_path / "plan"
    build_plan(template(tmp_path), plan_dir, dev_seeds=[41],
               validation_seeds=[42], test_seeds=[43])
    with pytest.raises(ValueError, match="unlock"):
        summarize(plan_dir, tmp_path / "missing", tmp_path / "out",
                  partition="heldout_worlds", rule_only=True)
    config_path = plan_dir / "configs" / "s6_B0_random.yaml"
    config_path.write_text(config_path.read_text() + "\n# changed\n")
    with pytest.raises(ValueError, match="Tampered frozen"):
        run_rule_controls(plan_dir, tmp_path / "runs", partition="development")


@pytest.mark.parametrize("dev,val,test", [
    ([1], [1], [3]),
    ([], [], []),
    ([-1], [], []),
])
def test_s6_requires_nonoverlapping_research_worlds(tmp_path, dev, val, test):
    with pytest.raises(ValueError):
        build_plan(template(tmp_path), tmp_path / "invalid",
                   dev_seeds=dev, validation_seeds=val, test_seeds=test)


def test_s6_refuses_to_build_from_uncontrolled_template(tmp_path):
    path = template(tmp_path)
    config = yaml.safe_load(path.read_text())
    config["scientific_world"]["enabled"] = False
    config["scientific_world"]["shocks"] = []
    config["cognitive"]["social_mode"] = "intent"
    path.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="S5-enabled"):
        build_plan(path, tmp_path / "invalid", dev_seeds=[41],
                   validation_seeds=[], test_seeds=[])
