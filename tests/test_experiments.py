import json
import hashlib
import sys
from pathlib import Path

import pandas as pd
import pytest

from core.experiments.config import ExperimentConfig
from core.experiments.runner import ExperimentRunner
from core.experiments.verify import verify_run, reproduce_run, REPLAY_FILES


def small(**overrides):
    return ExperimentConfig.model_validate({"n_agents": 12, "steps": 6, **overrides})


@pytest.mark.parametrize("overrides", [
    {"seed": -1}, {"seed": True}, {"steps": 0}, {"n_agents": 1},
    {"unused_typo": 3}, {"market": {"initial_price": float("nan")}},
    {"social": {"neighbors": 3}}, {"decision_mode": "mock"},
    {"events": [{"step": 90, "kind": "panic", "value": 1}]},
    {"events": [{"step": 1, "kind": "panic", "value": 1.5}]},
    {"decision_mode": "llm"},
])
def test_config_rejects_invalid_settings(overrides):
    with pytest.raises(ValueError):
        small(**overrides)


def test_complete_run_reproduction_and_integrity(tmp_path):
    path = ExperimentRunner(small()).run(tmp_path)
    assert verify_run(path)["status"] == "verified"
    assert reproduce_run(path, tmp_path)["status"] == "matched"
    agents = pd.read_csv(path / "agents.csv")
    trades = pd.read_csv(path / "trades.csv")
    metrics = pd.read_csv(path / "metrics.csv")
    assert len(agents) == 12 * 7 and len(metrics) == 6
    assert len(trades) > 0  # genuine matched orders, not a fabricated price path
    cash = agents.groupby("step").cash.sum()
    shares = agents.groupby("step").shares.sum()
    assert (shares == shares.iloc[0]).all()
    for step in range(1, 7):
        assert cash[step] + metrics.iloc[step-1].fees_cumulative == pytest.approx(cash[0])
    assert json.loads((path / "model_calls.json").read_text()) == []
    with (path / "trades.csv").open("a") as f:
        f.write("tampered\n")
    with pytest.raises(ValueError, match="integrity"):
        verify_run(path)


def test_events_halt_and_network_comparisons(tmp_path):
    cfg = small(events=[{"step": 2, "kind": "panic", "value": 4},
                        {"step": 3, "kind": "halt"}, {"step": 5, "kind": "resume"}])
    path = ExperimentRunner(cfg).run(tmp_path)
    metrics = pd.read_csv(path / "metrics.csv")
    assert metrics.iloc[1].infected >= 4
    assert (metrics.loc[metrics.step.isin([3, 4]), "volume"] == 0).all()
    assert metrics.iloc[2].price == metrics.iloc[1].price == metrics.iloc[3].price
    assert len((path / "events.jsonl").read_text().splitlines()) == 3
    other = ExperimentRunner(small(social={"topology": "empty"})).run(tmp_path)
    assert json.loads((other / "world.json").read_text())["social_edges"] == []


def test_failure_preserves_status_and_completed_steps(tmp_path, monkeypatch):
    runner = ExperimentRunner(small())
    real = runner._decisions
    async def fail(step):
        if step == 2:
            raise RuntimeError("controlled simulation failure")
        return await real(step)
    monkeypatch.setattr(runner, "_decisions", fail)
    with pytest.raises(RuntimeError, match="controlled"):
        runner.run(tmp_path)
    path = next(tmp_path.iterdir())
    summary = json.loads((path / "summary.json").read_text())
    assert summary["status"] == "failed" and summary["completed_steps"] == 1
    assert len(pd.read_csv(path / "metrics.csv")) == 1
    assert (path / "error.log").exists()
    with pytest.raises(ValueError, match="failed"):
        verify_run(path)


def test_snapshot_is_checked_and_source_is_distinct(tmp_path):
    source = tmp_path / "history.csv"
    source.write_text("close\n2999\n3001\n")
    input_cfg = {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                 "origin": "synthetic_test", "description": "controlled test fixture"}
    # Explicit synthetic fixture; never labeled as external historical evidence.
    run = ExperimentRunner(small(mode="test", historical_input=input_cfg)).run(tmp_path / "runs")
    world = json.loads((run / "world.json").read_text())
    assert world["initial_price"] == 3001 and world["input"]["historical_price_use"] == "initial_price_only"
    assert json.loads((run / "summary.json").read_text())["output_origin"] == "simulation"
    assert reproduce_run(run, tmp_path / "reruns")["status"] == "matched"
    source.write_text("close\n1\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        ExperimentRunner(small(mode="test", historical_input=input_cfg)).run(tmp_path / "runs")


def test_mock_label_and_heuristic_are_separate(tmp_path):
    for mode in ("profile_heuristic", "mock"):
        path = ExperimentRunner(small(mode="test", decision_mode=mode)).run(tmp_path)
        origin = json.loads((path / "summary.json").read_text())["output_origin"]
        assert origin == ("mock" if mode == "mock" else "simulation")


def test_cli_batch(tmp_path):
    from main import main
    cfg = tmp_path / "config.json"; cfg.write_text(small(steps=2).model_dump_json())
    assert main(["validate", "--config", str(cfg)]) == 0
    assert main(["batch", "--config", str(cfg), "--seeds", "3,4", "--output", str(tmp_path / "runs")]) == 0
    batch = next((tmp_path / "runs").glob("*/batch.json"))
    rows = json.loads(batch.read_text())
    assert len(rows) == 2 and all(r["status"] == "completed" for r in rows)
    assert rows[0]["path"] != rows[1]["path"]


def test_failed_batch_points_to_failure_archive(tmp_path):
    from main import main
    cfg = tmp_path / "bad.json"
    cfg.write_text(small(market_regime="undefined-regime").model_dump_json())
    assert main(["batch", "--config", str(cfg), "--seeds", "3", "--output", str(tmp_path / "runs")]) == 1
    row = json.loads(next((tmp_path / "runs").glob("*/batch.json")).read_text())[0]
    assert row["status"] == "failed" and row["path"]
    assert json.loads((Path(row["path"]) / "summary.json").read_text())["status"] == "failed"


@pytest.mark.parametrize('quantity', [1, 1.5])
def test_controlled_llm_decision_boundary_and_call_metadata(tmp_path, quantity):
    from core.llm.router import LLMRouter
    from core.llm.base import LLMResponse
    class ControlledClient:
        async def complete(self, messages, **kwargs):
            assert kwargs['model'] == 'chosen-model'
            body = json.loads(messages[1]['content'])
            return LLMResponse(json.dumps({'action': 'BUY' if int(body['account']['shares']) == 0 else 'SELL',
                                           'quantity': quantity, 'price': body['price']}),
                               'controlled_unit_test', 'unit-test-model', 0,
                               usage={'total_tokens': 0}, ok=True)
    cfg = small(mode='test', steps=2, decision_mode='llm',
                llm={'model': 'chosen-model', 'max_calls': 24})
    runner = ExperimentRunner(cfg)
    runner.llm_router = LLMRouter(deepseek_client=ControlledClient(), zhipu_client=ControlledClient())
    if quantity == 1:
        path = runner.run(tmp_path)
        assert verify_run(path)['status'] == 'verified'
        calls = json.loads((path/'model_calls.json').read_text())
        assert len(calls) == 24 and all(c['model'] == 'unit-test-model' for c in calls)
        with pytest.raises(ValueError, match='not deterministic'):
            reproduce_run(path, tmp_path/'replay')
    else:
        with pytest.raises(ValueError, match='decision schema'):
            runner.run(tmp_path)
        summary = json.loads((runner.output_dir/'summary.json').read_text())
        assert summary['status'] == 'failed' and summary['completed_steps'] == 0
        assert json.loads((runner.output_dir/'model_calls.json').read_text())


def test_runner_cannot_reuse_accounts_or_call_records(tmp_path):
    runner = ExperimentRunner(small(steps=1))
    first = runner.run(tmp_path)
    with pytest.raises(RuntimeError, match='single-use'):
        runner.run(tmp_path)
    assert list(tmp_path.iterdir()) == [first]
    assert verify_run(first)['status'] == 'verified'
