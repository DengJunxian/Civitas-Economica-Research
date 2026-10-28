"""Controlled research tests: no network requests, no paid model calls."""
import json

import pytest

from core.experiments.config import ExperimentConfig
from core.experiments.runner import ExperimentRunner
from core.experiments.verify import reproduce_run, verify_run
from core.llm.base import LLMResponse
from core.llm.router import LLMRouter
from research.civitas_agent.protocol import _strict_intent


class FakeModel:
    def __init__(self, *, malformed=False):
        self.observations = []
        self.reflection_inputs = []
        self.malformed = malformed

    async def complete(self, messages, **kwargs):
        assert kwargs["model"] == "chosen-model"
        payload = json.loads(messages[1]["content"])
        if isinstance(payload, dict):
            self.observations.append(payload)
            response = {
                "action": "BUY",
                "fraction": 1.2 if self.malformed else 0.05,
                "aggressiveness_ticks": 0,
            }
        else:
            self.reflection_inputs.append(payload)
            response = {"reflection": "Review prior decisions and risk exposure."}
        return LLMResponse(
            json.dumps(response), "fake_research_test", "chosen-model", 0,
            usage={"total_tokens": 1}, ok=True,
        )


def config(memory, social, *, reflection=False, max_calls=6):
    return ExperimentConfig.model_validate({
        "name": "civitas_agent_unit",
        "mode": "test",
        "n_agents": 12, "steps": 4,
        "decision_mode": "cognitive_llm",
        "seed": 123,
        "cognitive": {
            "llm_share": 0.25,
            "decision_interval": 2,
            "memory_enabled": memory,
            "social_enabled": social,
            "reflection_enabled": reflection,
            "reflection_interval": 2,
            "memory_limit": 8,
            "social_window": 2,
        },
        "llm": {"provider": "deepseek", "model": "chosen-model", "max_calls": max_calls},
    })


def run_with_fake(tmp_path, cfg, *, malformed=False):
    fake = FakeModel(malformed=malformed)
    runner = ExperimentRunner(cfg)
    runner.llm_router = LLMRouter(deepseek_client=fake, zhipu_client=fake)
    path = runner.run(tmp_path)
    return path, fake


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("memory,social", [
    (False, False), (True, False), (False, True), (True, True),
])
def test_factorial_arms_and_asof_observations(tmp_path, memory, social):
    path, fake = run_with_fake(tmp_path, config(memory, social))
    assert verify_run(path)["status"] == "verified"
    assert len(fake.observations) == 6  # 3 LLM investors x 2 scheduled decisions
    assert len(jsonl(path / "cognitive_decisions.jsonl")) == 6
    assert len(jsonl(path / "cognitive_outcomes.jsonl")) == 6
    assert len(jsonl(path / "cognitive_social_messages.jsonl")) == 12 * 4
    protocol = json.loads((path / "cognitive_protocol.json").read_text())
    assert len(protocol["active_llm_agent_ids"]) == 3
    assert protocol["logical_model_calls"] == 6
    assert all(call["model"] == "chosen-model" for call in json.loads(
        (path / "model_calls.json").read_text()
    ))
    for obs in fake.observations:
        assert all(item["step"] < obs["step"] for item in obs["episodic_memory"])
        assert all(item["step"] < obs["step"] for item in obs["neighbor_intentions"])
        if not memory:
            assert not obs["episodic_memory"]
        if not social:
            assert not obs["neighbor_intentions"]
    later = [obs for obs in fake.observations if obs["step"] == 3]
    assert len(later) == 3
    assert bool(later[0]["episodic_memory"]) == memory
    assert bool(later[0]["neighbor_intentions"]) == social
    with pytest.raises(ValueError, match="not deterministic"):
        reproduce_run(path, tmp_path / "rerun")


def test_cohort_assignment_is_same_across_arms(tmp_path):
    ids = []
    for memory, social in [(False, False), (True, True)]:
        path, _ = run_with_fake(tmp_path, config(memory, social))
        ids.append(json.loads((path / "cognitive_protocol.json").read_text())[
            "active_llm_agent_ids"
        ])
    assert ids[0] == ids[1]


def test_reflection_is_post_settlement_and_only_visible_later(tmp_path):
    cfg = config(True, True, reflection=True, max_calls=9)
    path, fake = run_with_fake(tmp_path, cfg)
    assert len(fake.reflection_inputs) == 3
    assert len(jsonl(path / "cognitive_reflections.jsonl")) == 3
    assert json.loads((path / "cognitive_protocol.json").read_text())[
        "logical_model_calls"
    ] == 9
    step3 = [obs for obs in fake.observations if obs["step"] == 3]
    assert len(step3) == 3
    assert all(any(m.get("event") == "reflection" and m["step"] == 2
                   for m in obs["episodic_memory"]) for obs in step3)
    assert verify_run(path)["status"] == "verified"


def test_invalid_json_decision_fails_closed_and_archives(tmp_path):
    fake = FakeModel(malformed=True)
    runner = ExperimentRunner(config(True, True))
    runner.llm_router = LLMRouter(deepseek_client=fake, zhipu_client=fake)
    with pytest.raises(ValueError, match="decision schema"):
        runner.run(tmp_path)
    summary = json.loads((runner.output_dir / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert (runner.output_dir / "error.log").exists()
    assert len(jsonl(runner.output_dir / "cognitive_observations.jsonl")) >= 1


@pytest.mark.parametrize("kwargs", [
    {"cognitive": {"llm_share": 0}},
    {"cognitive": {"decision_interval": 0}},
    {"cognitive": {"reflection_enabled": True, "memory_enabled": False}},
    {"llm": {"model": "chosen-model", "max_calls": 5}},
])
def test_invalid_cognitive_settings_rejected(kwargs):
    raw = config(True, True).model_dump(mode="json")
    for key, value in kwargs.items():
        raw[key].update(value)
    with pytest.raises(ValueError):
        ExperimentConfig.model_validate(raw)


def test_strict_intent_rejects_type_confusion():
    with pytest.raises(ValueError):
        _strict_intent('{"action":"BUY","fraction":true,"aggressiveness_ticks":0}')
    with pytest.raises(ValueError):
        _strict_intent('{"action":"SELL","fraction":0.5,"aggressiveness_ticks":2.0}')
