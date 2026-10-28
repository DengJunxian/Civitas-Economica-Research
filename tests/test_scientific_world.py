"""Scientific-world causal-scheduling and information-visibility tests.

All model clients are controlled local fakes; no external request is made.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from core.experiments.config import ExperimentConfig, InformationShock, ScientificWorldConfig
from core.experiments.runner import ExperimentRunner
from core.experiments.verify import verify_run
from core.llm.base import LLMResponse
from core.llm.router import LLMRouter
from research.civitas_agent.scientific_world import SyntheticInformationWorld


class ControlledInvestor:
    def __init__(self):
        self.prompts = []

    async def complete(self, messages, **kwargs):
        obs = json.loads(messages[1]["content"])
        self.prompts.append(obs)
        assert kwargs["model"] == "chosen-model"
        return LLMResponse(
            json.dumps({"action": "HOLD", "fraction": 0, "aggressiveness_ticks": 0}),
            "controlled_local_test", "chosen-model", 0.0,
            usage={"total_tokens": 1}, ok=True,
        )


def shock(*, truthful=True, reveal=5, step=2):
    return {
        "shock_id": "claim_1", "source_id": "issuer_A",
        "step": step, "reveal_step": reveal,
        "polarity": -1, "magnitude": 0.04,
        "truthful": truthful, "direct_fraction": 0.34,
    }


def cfg(*, truthful=True, memory=True, social=True, schedule="seeded_per_step", shocks=None):
    return ExperimentConfig.model_validate({
        "name": "s5_controlled",
        "mode": "test", "n_agents": 12, "steps": 6,
        "seed": 314159,
        "decision_mode": "cognitive_llm",
        "cognitive": {
            "llm_share": 0.25, "decision_interval": 1,
            "memory_enabled": memory, "social_enabled": social,
            "social_mode": "news", "order_schedule": schedule,
            "reflection_enabled": False, "memory_limit": 12,
        },
        "llm": {"model": "chosen-model", "provider": "deepseek",
                "max_calls": 18, "max_retries": 0},
        "scientific_world": {
            "enabled": True, "shocks": [shock(truthful=truthful)] if shocks is None else shocks,
        },
    })


def run_fake(tmp_path: Path, cfg):
    model = ControlledInvestor()
    runner = ExperimentRunner(cfg)
    runner.llm_router = LLMRouter(deepseek_client=model, zhipu_client=model)
    run_dir = runner.run(tmp_path)
    return run_dir, model


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def build_world(*, truthful, social):
    settings = ScientificWorldConfig.model_validate({
        "enabled": True, "shocks": [shock(truthful=truthful, reveal=6)],
    })
    graph = {"a": ["b"], "b": ["a", "c"], "c": ["b", "d"], "d": ["c"]}
    return SyntheticInformationWorld(
        config=settings, seed=7, initial_price=100.0,
        active_ids=["a", "b", "c", "d"], neighbors=graph,
        social_enabled=social, social_mode="news", total_steps=7,
    )


def test_latent_truth_does_not_leak_to_pre_revelation_observations():
    true_world = build_world(truthful=True, social=True)
    false_world = build_world(truthful=False, social=True)
    for step in range(1, 6):
        true_world.advance(step)
        false_world.advance(step)
        for aid in ("a", "b", "c", "d"):
            t = true_world.observation_for(aid, step)
            f = false_world.observation_for(aid, step)
            assert t == f
            assert not t["public_verifications"]
            assert "truthful" not in json.dumps(t)
            assert "fundamental" not in json.dumps(t)
    assert true_world.fundamental == pytest.approx(96.0)
    assert false_world.fundamental == pytest.approx(100.0)
    true_world.advance(6)
    false_world.advance(6)
    for aid in ("a", "b", "c", "d"):
        assert true_world.observation_for(aid, 6)["public_verifications"][0]["verdict"] == "supported"
        assert false_world.observation_for(aid, 6)["public_verifications"][0]["verdict"] == "refuted"
    with pytest.raises(ValueError, match="incorrect step"):
        true_world.observation_for("a", 5)


def test_social_forwarding_is_prior_step_neighbor_only():
    social = build_world(truthful=False, social=True)
    isolated = build_world(truthful=False, social=False)
    for step in range(1, 6):
        social.advance(step)
        isolated.advance(step)
    assert {x["agent_id"] for x in isolated.deliveries}.issubset(
        {x["agent_id"] for x in social.deliveries}
    )
    assert len(social.deliveries) > len(isolated.deliveries)
    assert all(x["channel"] == "direct" for x in isolated.deliveries)
    for delivery in social.deliveries:
        if delivery["channel"] == "social":
            aid = delivery["agent_id"]
            sender = delivery["from_agent_id"]
            assert sender in social.neighbors[aid]
            assert social.exposure_steps[delivery["shock_id"]][sender] == delivery["received_step"] - 1
    assert not any(x["received_step"] >= 6 for x in social.deliveries)


def test_end_to_end_validates_both_truth_cases_and_no_market_price_override(tmp_path):
    true_path, true_model = run_fake(tmp_path, cfg(truthful=True))
    false_path, false_model = run_fake(tmp_path, cfg(truthful=False))
    for path in (true_path, false_path):
        assert verify_run(path)["status"] == "verified"
        assert len(read_jsonl(path / "study_order_schedule.jsonl")) == 6
        assert len(read_jsonl(path / "study_news_verifications.jsonl")) == 1
        assert len(read_jsonl(path / "study_news_exposures.jsonl")) > 0
        assert len(read_jsonl(path / "cognitive_observations.jsonl")) == 18
        assert len(json.loads((path / "model_calls.json").read_text())) == 18
        metrics = pd.read_csv(path / "metrics.csv")
        fundamentals = pd.read_csv(path / "study_fundamentals.csv")
        assert len(metrics) == len(fundamentals) == 6
        assert "two_sided_quote_valid" in metrics.columns
        assert "no_trade" in metrics.columns
        assert "spread_pct_when_quoted" in metrics.columns
        assert (metrics.absolute_price_deviation >= 0).all()
        assert (metrics.fundamental_value > 0).all()
        assert len(json.loads((path / "study_world_private.json").read_text())[
            "active_cognitive_agents"
        ]) == 3

    yes = pd.read_csv(true_path / "metrics.csv")
    no = pd.read_csv(false_path / "metrics.csv")
    assert yes.loc[0, "fundamental_value"] == 3000.0
    assert yes.loc[1, "fundamental_value"] == pytest.approx(2880.0)
    assert (no.fundamental_value == 3000.0).all()
    # Exogenous truth does not set the book's transaction price; identical
    # fake decisions and background RNG produce the same price in both worlds.
    assert yes.price.to_list() == no.price.to_list()
    assert read_jsonl(true_path / "study_order_schedule.jsonl") == read_jsonl(
        false_path / "study_order_schedule.jsonl"
    )
    assert sorted(true_model.prompts, key=lambda x: (x["step"], x["agent_id"])) != []
    early_true = [p for p in true_model.prompts if p["step"] < 5]
    early_false = [p for p in false_model.prompts if p["step"] < 5]
    assert early_true == early_false
    assert all(not p["information_signals"]["public_verifications"] for p in early_true)
    assert all("truthful" not in json.dumps(p) for p in early_true)
    late_true = [p for p in true_model.prompts if p["step"] >= 5]
    assert all(p["information_signals"]["public_verifications"] for p in late_true)
    assert all("fundamental_value" not in p["information_signals"] for p in late_true)


def test_schedule_differs_from_fixed_and_is_seed_stable(tmp_path):
    a, _ = run_fake(tmp_path, cfg(schedule="seeded_per_step"))
    b, _ = run_fake(tmp_path, cfg(schedule="seeded_per_step", memory=False, social=False))
    fixed, _ = run_fake(tmp_path, cfg(schedule="fixed"))
    ra = read_jsonl(a / "study_order_schedule.jsonl")
    rb = read_jsonl(b / "study_order_schedule.jsonl")
    rf = read_jsonl(fixed / "study_order_schedule.jsonl")
    assert ra == rb
    assert ra != rf
    assert all(row["ordered_agent_ids"] == rf[0]["ordered_agent_ids"] for row in rf)
    for row in ra:
        assert len(set(row["ordered_agent_ids"])) == 12


@pytest.mark.parametrize("change", [
    {"decision_mode": "vectorized"},
    {"scientific_world": {"enabled": True, "shocks": [shock(reveal=9)]}},
    {"events": [{"step": 3, "kind": "panic", "value": 1}]},
    {"cognitive": {"social_mode": "intent"}},
    {"historical_input": {
        "path": "x", "sha256": "0" * 64, "origin": "historical",
        "description": "not permitted in synthetic study",
    }},
])
def test_scientific_validation_rejects_invalid_world_combinations(change):
    raw = cfg().model_dump(mode="json")
    for key, value in change.items():
        if isinstance(value, dict) and isinstance(raw.get(key), dict):
            raw[key].update(value)
        else:
            raw[key] = value
    with pytest.raises(ValueError):
        ExperimentConfig.model_validate(raw)


def test_shock_schema_rejects_unphysical_or_duplicate_identifiers():
    with pytest.raises(ValueError):
        InformationShock.model_validate(shock(reveal=2))
    with pytest.raises(ValueError):
        InformationShock.model_validate({**shock(), "magnitude": 0.6})
    with pytest.raises(ValueError):
        ScientificWorldConfig.model_validate({
            "enabled": True, "shocks": [shock(), shock()],
        })
