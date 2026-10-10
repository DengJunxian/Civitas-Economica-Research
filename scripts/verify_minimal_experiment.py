"""Run the README experiment in fresh processes and save a validation receipt.

Requires only requirements-core.txt. Every experiment subprocess blocks network
connections; this check never calls an external market feed or model service.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFLINE_CLI = '''
import socket, sys
def blocked(*args, **kwargs):
    raise AssertionError("Clean validation must not access the network")
socket.socket.connect = blocked
socket.create_connection = blocked
from main import main
raise SystemExit(main(sys.argv[1:]))
'''


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_results(run: Path, cfg: dict) -> dict:
    frames = {}
    for name in ("metrics", "agents", "decisions", "orders", "order_events", "trades", "social"):
        with (run / f"{name}.csv").open(newline="", encoding="utf-8") as file:
            frames[name] = list(csv.DictReader(file))
    assert len(frames["metrics"]) == cfg["steps"]
    assert len(frames["social"]) == cfg["steps"]
    assert len(frames["agents"]) == cfg["n_agents"] * (cfg["steps"] + 1)
    assert len(frames["decisions"]) == cfg["n_agents"] * cfg["steps"]
    assert len(read_json(run / "population.json")) == cfg["n_agents"]
    assert read_json(run / "model_calls.json") == []
    summary = read_json(run / "summary.json")
    assert summary["status"] == "completed" and summary["completed_steps"] == cfg["steps"]
    assert summary["output_origin"] == "simulation"
    assert summary["trade_count"] == len(frames["trades"]) > 0
    metrics = {int(row["step"]): row for row in frames["metrics"]}
    initial = [row for row in frames["agents"] if int(row["step"]) == 0]
    initial_cash = math.fsum(float(row["cash"]) for row in initial)
    initial_shares = sum(int(row["shares"]) for row in initial)
    max_residual = 0.0
    for step in range(cfg["steps"] + 1):
        agents = [row for row in frames["agents"] if int(row["step"]) == step]
        assert len({row["agent_id"] for row in agents}) == cfg["n_agents"]
        assert all(float(row["cash"]) >= -1e-6 and int(row["shares"]) >= 0 for row in agents)
        assert sum(int(row["shares"]) for row in agents) == initial_shares
        fees = float(metrics[step]["fees_cumulative"]) if step else 0.0
        residual = abs(math.fsum(float(row["cash"]) for row in agents) + fees - initial_cash)
        assert residual <= 1e-6, f"Cash plus fees does not conserve at step {step}: {residual}"
        max_residual = max(max_residual, residual)
        if step:
            trades = [row for row in frames["trades"] if int(row["step"]) == step]
            assert sum(int(row["quantity"]) for row in trades) == int(metrics[step]["volume"])
    return {"row_counts": {name: len(rows) for name, rows in frames.items()},
            "trade_count": summary["trade_count"], "final_price": summary["final_price"],
            "output_origin": summary["output_origin"], "max_cash_conservation_residual": max_residual,
            "total_shares": initial_shares, "model_calls": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="outputs/clean-validation")
    args = parser.parse_args()
    folder = Path(args.output).resolve() / f"check-{uuid.uuid4().hex[:12]}"
    folder.mkdir(parents=True, exist_ok=False)
    receipt = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
               "python": sys.version, "network_blocked": True, "commands": [], "status": "running"}

    def cli(*arguments):
        result = subprocess.run([sys.executable, "-c", OFFLINE_CLI, *arguments], cwd=ROOT,
                                capture_output=True, text=True, timeout=120)
        receipt["commands"].append({"argv": ["main.py", *arguments], "exit_code": result.returncode,
                                    "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode:
            raise RuntimeError(f"CLI {arguments[0]} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    try:
        cfg = json.loads(cli("validate", "--config", "examples/experiments/minimal.yaml"))
        run = Path(cli("run", "--config", "examples/experiments/minimal.yaml", "--output", str(folder / "experiments")))
        receipt["run"] = str(run)
        receipt["config"] = cfg
        receipt["verification"] = json.loads(cli("verify", str(run)))
        receipt["results"] = check_results(run, cfg)
        receipt["reproduction"] = json.loads(cli("reproduce", str(run), "--output", str(folder / "reproductions")))
        receipt["rerun_verification"] = json.loads(cli("verify", receipt["reproduction"]["rerun"]))
        manifest = read_json(next(run.glob("*.manifest.json")))
        receipt["git_commit"] = manifest["git_commit"]
        receipt["git_dirty"] = manifest["notes"]["git_dirty"]
        receipt["source_hash"] = manifest["notes"]["source_hash"]
        receipt["config_hash"] = manifest["config_hash"]
        receipt["environment"] = read_json(run / "environment.json")
        receipt["status"] = "passed"
    except Exception as exc:
        receipt.update(status="failed", error_type=type(exc).__name__, error=str(exc))
    finally:
        receipt["ended_at"] = datetime.now(timezone.utc).isoformat()
        path = folder / "validation.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "receipt": str(path),
                      "results": receipt.get("results"), "error": receipt.get("error")}, ensure_ascii=False, indent=2))
    return int(receipt["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
