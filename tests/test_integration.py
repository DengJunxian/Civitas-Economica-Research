import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_cli_is_independent_of_ui_and_external_models(tmp_path):
    code = '''
import sys
from core.experiments.config import ExperimentConfig
from core.experiments.runner import ExperimentRunner
ExperimentRunner(ExperimentConfig(n_agents=8,steps=2)).run(sys.argv[1])
assert not any(m in sys.modules for m in ["streamlit","openai","mesa","chromadb"])
'''
    process = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=ROOT, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr


def test_reproduction_across_process_and_hashseed(tmp_path):
    from core.experiments.config import ExperimentConfig
    from core.experiments.runner import ExperimentRunner
    path = ExperimentRunner(ExperimentConfig(n_agents=12, steps=4)).run(tmp_path)
    env = {**os.environ, "PYTHONHASHSEED": "713", "TZ": "UTC"}
    result = subprocess.run([sys.executable, str(ROOT / "main.py"), "reproduce", str(path),
                             "--output", str(tmp_path / "replayed")], cwd=tmp_path, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_ui_runs_same_service(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from core.experiments.runner import ExperimentRunner
    run = ExperimentRunner.run
    monkeypatch.setattr(ExperimentRunner, "run", lambda self, output: run(self, tmp_path))
    app = AppTest.from_file(str(ROOT / "app.py")).run(timeout=20)
    assert not app.exception
    app.sidebar.radio[0].set_value("配置与运行").run(timeout=20)
    app.text_area[0].set_value("n_agents: 12\nsteps: 3\n")
    app.button[1].click().run(timeout=20)
    assert not app.exception
    assert len(app.success) == 1
    summary = json.loads(next(tmp_path.glob("*/summary.json")).read_text())
    assert summary["status"] == "completed" and summary["completed_steps"] == 3


def test_ipc_matching_smoke(tmp_path):
    # Exercise the real spawned worker, while all external networking is blocked.
    from simulation_runner import SimulationRunner, BufferedIntent
    runner = SimulationRunner(ipc_root=tmp_path, symbol="X", prev_close=100, seed=77)
    runner.start()
    try:
        response = runner.submit_batch([
            BufferedIntent("sell", "seller", "sell", 2, 100, symbol="X"),
            BufferedIntent("buy", "buyer", "buy", 2, 101, symbol="X"),
        ])
        assert response["trade_count"] == 1
        assert response["trades"][0]["price"] == 100
        assert response["seed"] == 77 and response["matching_backend"] == "python"
        assert response["account_settlement"] is False
    finally:
        runner.stop()


def test_explicit_news_rule_mode_and_failure():
    from data_flywheel.nlp_processor import NlpProcessor
    from data_flywheel.schemas import RawArticle
    class Failing:
        def sync_call_with_fallback(self, **kwargs):
            raise TimeoutError("controlled error")
    article = RawArticle("测试", "测试政策文本", "fixture", source_url="mock://fixture/1")
    with pytest.raises(TimeoutError):
        NlpProcessor(Failing()).process(article)
    event = NlpProcessor(Failing(), processing_mode="rules").process(article)
    assert event.data_origin == "mock" and event.processing_mode == "rules" and not event.model_used


def test_mesa_explicit_population_and_initial_price(tmp_path, monkeypatch):
    pytest.importorskip("mesa")
    monkeypatch.chdir(tmp_path)
    from core.mesa.civitas_model import CivitasModel
    model = CivitasModel(n_agents=4, initial_price=123, seed=9, n_vectorized=0,
                         include_national_team=False, use_llm=False, load_real_data=False)
    assert model.current_price == 123 and len(model.agents) == 4
    assert model.population.n_vectorized == 0
    assert all(not agent.core.use_llm for agent in model.agents)
