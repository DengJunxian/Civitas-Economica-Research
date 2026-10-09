"""Run bounded, offline engineering experiments and retain an auditable receipt.

This is an execution/consistency check, not a scientific effect-size experiment.
Usage: python scripts/validate_experiments.py --with-cpp
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.experiments.config import load_config
from core.experiments.verify import REPLAY_FILES, verify_run


def inspect_run(path: Path) -> dict:
    integrity = verify_run(path)
    summary = json.loads((path / "summary.json").read_text())
    config = json.loads((path / "config.json").read_text())
    with (path / "agents.csv").open() as f:
        states = list(csv.DictReader(f))
    with (path / "metrics.csv").open() as f:
        metrics = list(csv.DictReader(f))
    with (path / "trades.csv").open() as f:
        trades = list(csv.DictReader(f))
    by_step = {}
    for row in states:
        by_step.setdefault(int(row["step"]), []).append(row)
    initial_cash = math.fsum(float(row["cash"]) for row in by_step[0])
    initial_shares = sum(int(row["shares"]) for row in by_step[0])
    assert len(metrics) == config["steps"]
    assert len(states) == (config["steps"] + 1) * config["n_agents"]
    assert trades and all(t["buyer_agent_id"] != t["seller_agent_id"] for t in trades)
    max_cash_residual = 0.0
    for row in metrics:
        step = int(row["step"])
        accounts = by_step[step]
        residual = abs(math.fsum(float(a["cash"]) for a in accounts) + float(row["fees_cumulative"]) - initial_cash)
        max_cash_residual = max(max_cash_residual, residual)
        assert residual <= max(1e-7, initial_cash * 1e-12)
        assert sum(int(a["shares"]) for a in accounts) == initial_shares
        assert all(float(a["cash"]) >= -1e-7 and int(a["shares"]) >= 0 for a in accounts)
        step_trades = [t for t in trades if int(t["step"]) == step]
        assert int(row["volume"]) == sum(int(t["quantity"]) for t in step_trades)
        previous = float(metrics[step - 2]["price"]) if step > 1 else json.loads((path / "world.json").read_text())["initial_price"]
        assert float(row["price"]) == (float(step_trades[-1]["price"]) if step_trades else previous)
        if row["halted"] == "True":
            assert not step_trades
    assert json.loads((path / "model_calls.json").read_text()) == []
    return {"path": str(path), "name": config["name"], "seed": config["seed"],
            "agents": config["n_agents"], "steps": config["steps"],
            "backend": config["market"]["backend"], "status": summary["status"],
            "trade_count": len(trades), "total_volume": sum(int(t["quantity"]) for t in trades),
            "final_price": summary["final_price"], "max_cash_residual": max_cash_residual,
            "integrity": integrity, "source_hash": summary["source_hash"],
            "artifacts": {name: hashlib.sha256((path/name).read_bytes()).hexdigest() for name in REPLAY_FILES}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "outputs/engineering_validation"))
    parser.add_argument("--with-cpp", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if args.with_cpp and importlib.util.find_spec("_civitas_lob") is None:
        parser.error("--with-cpp requires a built _civitas_lob extension")
    commands = []

    def cli(*args):
        command = [sys.executable, str(ROOT / "main.py"), *map(str, args)]
        started = time.perf_counter()
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        commands.append({"argv": command, "exit_code": result.returncode,
                         "elapsed_seconds": time.perf_counter() - started})
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        return result.stdout.strip()

    batch = Path(cli("batch", "--config", ROOT / "examples/experiments/minimal.yaml",
        "--config", ROOT / "examples/experiments/panic.yaml", "--seeds", "41,42,43", "--output", output))
    rows = json.loads((batch / "batch.json").read_text())
    runs = [inspect_run(Path(row["path"])) for row in rows]
    baseline = Path(rows[1]["path"])
    replay = json.loads(cli("reproduce", baseline, "--output", batch / "reproductions"))
    inspect_run(Path(replay["rerun"]))
    parity = {"status": "not_requested"}
    if args.with_cpp:
        config = load_config(ROOT / "examples/experiments/minimal.yaml").model_dump(mode="json")
        config["market"]["backend"] = "cpp"
        config_path = batch / "cpp_config.json"
        config_path.write_text(json.dumps(config, indent=2))
        cpp = Path(cli("run", "--config", config_path, "--output", batch / "native"))
        runs.append(inspect_run(cpp))
        differences = [name for name in REPLAY_FILES if (baseline/name).read_bytes() != (cpp/name).read_bytes()]
        assert not differences, differences
        parity = {"status": "matched", "compared_files": len(REPLAY_FILES), "python": str(baseline), "cpp": str(cpp)}
    receipt = {"purpose": "offline engineering verification, not scientific inference",
               "runs": runs, "reproduction": replay, "native_parity": parity, "commands": commands,
               "live_llm_calls": 0, "external_market_requests": 0}
    path = batch / "validation_receipt.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(path)


if __name__ == "__main__":
    main()
