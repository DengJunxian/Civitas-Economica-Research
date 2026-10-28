"""S6 fair-control tests: deterministic agents with zero live model calls."""
import json

import pandas as pd
import pytest

from core.experiments.config import ExperimentConfig
from core.experiments.runner import ExperimentRunner
from core.experiments.verify import COGNITIVE_FILES, SCIENTIFIC_FILES, reproduce_run, verify_run
from research.civitas_agent.baselines import deterministic_intent


def study_cfg(policy="random", **edits):
    raw = {
        "name": f"s6_{policy}", "mode": "research",
        "seed": 77, "n_agents": 12, "steps": 6,
        "decision_mode": "cognitive_rule",
        "cognitive": {
            "decision_policy": policy,
            "llm_share": 0.25,
            "decision_interval": 1,
            "memory_enabled": True,
            "social_enabled": True,
            "social_mode": "news",
            "order_schedule": "seeded_per_step",
        },
        "scientific_world": {
            "enabled": True,
            "shocks": [{
                "shock_id": "c1", "source_id": "sourceA",
                "step": 2, "reveal_step": 5,
                "polarity": -1, "magnitude": 0.03,
                "truthful": False, "direct_fraction": 0.5,
            }]
        },
    }
    for field, value in edits.items():
        if isinstance(value, dict) and isinstance(raw.get(field), dict):
            raw[field].update(value)
        else:
            raw[field] = value
    return ExperimentConfig.model_validate(raw)


@pytest.mark.parametrize("policy", ["random", "profile_rule", "claim_rule"])
def test_offline_cognitive_rule_runs_and_deterministically_replays_all_outputs(
    tmp_path, policy,
):
    cfg = study_cfg(policy)
    runner = ExperimentRunner(cfg)
    path = runner.run(tmp_path / "original")
    assert runner.llm_router is None
    assert verify_run(path)["status"] == "verified"
    assert json.loads((path / "model_calls.json").read_text()) == []
    metadata = json.loads((path / "cognitive_protocol.json").read_text())
    assert metadata["logical_model_calls"] == 0
    assert metadata["deterministic_rule_decisions"] == 18
    assert metadata["decision_policy"] == policy
    decisions = [
        json.loads(line) for line in (path / "cognitive_decisions.jsonl").read_text().splitlines()
    ]
    assert len(decisions) == 18 and set(d["decision_policy"] for d in decisions) == {policy}
    rows = pd.read_csv(path / "metrics.csv")
    assert len(rows) == 6
    assert (rows["fundamental_value"] > 0).all()
    result = reproduce_run(path, tmp_path / "replays")
    assert result["status"] == "matched"
    assert result["compared_files"] == 11 + len(COGNITIVE_FILES) + len(SCIENTIFIC_FILES)
    assert verify_run(result["rerun"])["status"] == "verified"


def test_research_baselines_share_agent_cohort_order_and_initial_world(tmp_path):
    paths = {
        policy: ExperimentRunner(study_cfg(policy)).run(tmp_path)
        for policy in ("random", "profile_rule", "claim_rule")
    }
    cohorts = [
        json.loads((p / "cognitive_protocol.json").read_text())["active_llm_agent_ids"]
        for p in paths.values()
    ]
    assert cohorts[0] == cohorts[1] == cohorts[2]
    worlds = [json.loads((p / "world.json").read_text()) for p in paths.values()]
    assert worlds[0] == worlds[1] == worlds[2]
    arrivals = [
        (p / "study_order_schedule.jsonl").read_text() for p in paths.values()
    ]
    assert arrivals[0] == arrivals[1] == arrivals[2]


def test_claim_rule_uses_observed_evidence_not_researcher_truth():
    observation = {
        "step": 3, "agent_id": "agent_a",
        "market": {"price": 100.0},
        "account": {"cost_basis": 100.0},
        "price_history": [100.0, 100.0],
        "persona": {"loss_aversion": 2.25},
        "episodic_memory": [],
        "information_signals": {
            "received_claims": [{
                "shock_id": "s", "source_id": "x",
                "received_step": 2, "claimed_fractional_value_change": -0.03,
            }],
            "public_verifications": [],
            "observed_source_track_records": {},
        },
    }
    initial = deterministic_intent("claim_rule", observation, seed=4)
    assert initial["action"] == "SELL"
    assert deterministic_intent("random", observation, seed=4) == deterministic_intent(
        "random", observation, seed=4
    )
    observation["information_signals"]["public_verifications"] = [{
        "shock_id": "s", "source_id": "x", "verdict": "refuted",
    }]
    assert deterministic_intent("claim_rule", observation, seed=4)["action"] == "BUY"
    observation["information_signals"]["public_verifications"][0]["verdict"] = "supported"
    assert deterministic_intent("claim_rule", observation, seed=4)["action"] == "SELL"


@pytest.mark.parametrize("mode,policy,reflection", [
    ("cognitive_rule", "llm", False),
    ("cognitive_llm", "random", False),
    ("cognitive_rule", "random", True),
])
def test_invalid_policy_mode_pair_is_rejected(mode, policy, reflection):
    raw = study_cfg("random").model_dump(mode="json")
    raw["decision_mode"] = mode
    raw["cognitive"]["decision_policy"] = policy
    raw["cognitive"]["reflection_enabled"] = reflection
    with pytest.raises(ValueError):
        ExperimentConfig.model_validate(raw)
