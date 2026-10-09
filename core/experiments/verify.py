"""Integrity checking and deterministic reruns of archived experiments."""
import hashlib
import json
from pathlib import Path

from core.experiments.config import ExperimentConfig
from core.experiments.runner import ExperimentRunner

REPLAY_FILES = ("population.json", "world.json", "metrics.csv", "agents.csv", "trades.csv",
                "orders.csv", "order_events.csv", "decisions.csv", "social.csv", "events.jsonl", "model_calls.json")


def verify_run(run_dir):
    root = Path(run_dir)
    manifests = list(root.glob("*.manifest.json"))
    if len(manifests) != 1:
        raise ValueError("Expected exactly one experiment manifest")
    manifest = json.loads(manifests[0].read_text())
    required = set(REPLAY_FILES) | {"config.json", "summary.json", "source.json", "environment.json", "run.log"}
    errors = sorted(required - set(manifest["artifacts"]))
    for name, artifact in manifest["artifacts"].items():
        path = root / name
        if Path(name).name != name or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
            errors.append(name)
    if errors:
        raise ValueError(f"Artifact integrity mismatch: {errors}")
    if manifest["notes"]["status"] != "completed":
        raise ValueError(f"Experiment status is {manifest['notes']['status']}")
    return {"run_id": manifest["run_id"], "checked_files": len(manifest["artifacts"]), "status": "verified"}


def reproduce_run(run_dir, output_root):
    verify_run(run_dir)
    root = Path(run_dir)
    payload = json.loads((root / "config.json").read_text())
    if payload["decision_mode"] == "llm":
        raise ValueError("Live LLM reruns require a new explicit run; they are not deterministic replays")
    if payload.get("historical_input"):
        payload["historical_input"]["path"] = str((root / "input_snapshot.csv").resolve())
    path = ExperimentRunner(ExperimentConfig.model_validate(payload)).run(output_root)
    mismatches = [name for name in REPLAY_FILES if (root / name).read_bytes() != (path / name).read_bytes()]
    if mismatches:
        raise ValueError(f"Reproduction differs in {mismatches}; rerun archived at {path}")
    return {"status": "matched", "compared_files": len(REPLAY_FILES), "rerun": str(path)}
