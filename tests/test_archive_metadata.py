"""Regression coverage for archive identities and persistent reproduction receipts."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from core.experiments.config import ExperimentConfig
from core.experiments.runner import ExperimentRunner, write_json
from core.experiments.verify import REPLAY_FILES, reproduce_run, verify_run


def run_small(tmp_path, **overrides):
    cfg = ExperimentConfig.model_validate({"n_agents": 12, "steps": 4, **overrides})
    return ExperimentRunner(cfg).run(tmp_path)


def rewrite_artifact(root, name, payload):
    """Rehash a changed file to exercise semantic validation after file integrity."""
    write_json(root / name, payload)
    manifest_path = next(root.glob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    raw = (root / name).read_bytes()
    manifest["artifacts"][name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    write_json(manifest_path, manifest)


@pytest.mark.parametrize("field,value", [
    ("seed", 43), ("config_hash", "0" * 64), ("data_snapshot_hash", "0" * 64),
    ("experiment_id", "exp_inconsistent"), ("code_version", "incorrect_commit"),
])
def test_verify_rejects_inconsistent_manifest_identity(tmp_path, field, value):
    root = run_small(tmp_path)
    path = next(root.glob("*.manifest.json"))
    manifest = json.loads(path.read_text())
    manifest[field] = value
    write_json(path, manifest)
    with pytest.raises(ValueError, match="metadata mismatch"):
        verify_run(root)


@pytest.mark.parametrize("file,field,value", [
    ("config.json", "seed", 43), ("summary.json", "completed_steps", 3),
    ("source.json", "sha256", "0" * 64), ("environment.json", "installed_packages", {}),
])
def test_verify_checks_rehashed_metadata_against_identities(tmp_path, file, field, value):
    root = run_small(tmp_path)
    payload = json.loads((root / file).read_text())
    payload[field] = value
    rewrite_artifact(root, file, payload)
    with pytest.raises(ValueError, match="metadata mismatch"):
        verify_run(root)


def test_missing_historical_snapshot_is_rejected_even_if_omitted_from_manifest(tmp_path):
    source = tmp_path / "fixture.csv"
    source.write_text("close\n3000\n")
    root = run_small(tmp_path / "runs", mode="test", historical_input={
        "path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "origin": "synthetic_test", "description": "explicit unit-test input"})
    path = next(root.glob("*.manifest.json"))
    manifest = json.loads(path.read_text())
    del manifest["artifacts"]["input_snapshot.csv"]
    (root / "input_snapshot.csv").unlink()
    write_json(path, manifest)
    with pytest.raises(ValueError, match="input_snapshot.csv"):
        verify_run(root)


def test_reproduction_report_and_lineage_are_archived(tmp_path):
    original = run_small(tmp_path / "original")
    original_manifest = next(original.glob("*.manifest.json"))
    before = original_manifest.read_bytes()
    result = reproduce_run(original, tmp_path / "reruns")
    report_path = Path(result["report"])
    report = json.loads(report_path.read_text())
    manifest = json.loads(next(report_path.parent.glob("*.manifest.json")).read_text())
    assert result["status"] == "matched" and result["source_matches"]
    assert result["environment_differences"] == []
    assert manifest["parent_run_id"] == report["original"]["run_id"]
    assert report["original"]["manifest_sha256"] == hashlib.sha256(before).hexdigest()
    assert len(report["comparisons"]) == len(REPLAY_FILES)
    assert all(row["matched"] for row in report["comparisons"])
    assert original_manifest.read_bytes() == before
    assert verify_run(report_path.parent)["status"] == "verified"


def test_reproduction_mismatch_saves_report_before_raising(tmp_path, monkeypatch):
    original = run_small(tmp_path / "original")

    async def hold(self, step):
        n = self.config.n_agents
        return np.zeros(n, dtype=int), np.zeros(n, dtype=int), np.full(n, self.price)

    monkeypatch.setattr(ExperimentRunner, "_decisions", hold)
    with pytest.raises(ValueError, match="Reproduction differs"):
        reproduce_run(original, tmp_path / "reruns")
    report_path = next((tmp_path / "reruns").glob("*/reproduction.json"))
    report = json.loads(report_path.read_text())
    assert report["status"] == "different" and report["mismatches"]
    assert verify_run(report_path.parent)["status"] == "verified"


def test_reproduction_failure_preserves_error_receipt(tmp_path, monkeypatch):
    original = run_small(tmp_path / "original")

    def fail(self, output):
        raise RuntimeError("controlled initialization failure")

    monkeypatch.setattr(ExperimentRunner, "_initialize", fail)
    with pytest.raises(RuntimeError, match="controlled"):
        reproduce_run(original, tmp_path / "reruns")
    report_path = next((tmp_path / "reruns").glob("*/reproduction.json"))
    report = json.loads(report_path.read_text())
    assert report["status"] == "rerun_failed" and report["error_type"] == "RuntimeError"
    assert (report_path.parent / "error.log").exists()
    manifest = json.loads(next(report_path.parent.glob("*.manifest.json")).read_text())
    assert manifest["artifacts"]["reproduction.json"]["sha256"] == hashlib.sha256(report_path.read_bytes()).hexdigest()


def test_verify_accepts_previous_archive_without_dependency_lock(tmp_path):
    root = run_small(tmp_path)
    path = next(root.glob("*.manifest.json"))
    manifest = json.loads(path.read_text())
    del manifest["notes"]["archive_schema_version"]
    del manifest["artifacts"]["requirements-lock.txt"]
    (root / "requirements-lock.txt").unlink()
    write_json(path, manifest)
    assert verify_run(root)["status"] == "verified"
