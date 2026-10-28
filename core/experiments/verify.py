"""Integrity checking and deterministic reruns of archived experiments."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from core.experiment_manifest import ExperimentManifest, load_experiment_manifest, write_experiment_manifest
from core.experiments.config import ExperimentConfig
from core.experiments.runner import ExperimentRunner, write_json
from core.reproducibility import stable_hash, utc_now_iso

REPLAY_FILES = ("population.json", "world.json", "metrics.csv", "agents.csv", "trades.csv",
                "orders.csv", "order_events.csv", "decisions.csv", "social.csv", "events.jsonl", "model_calls.json")
COGNITIVE_FILES = (
    "cognitive_observations.jsonl", "cognitive_decisions.jsonl",
    "cognitive_outcomes.jsonl", "cognitive_social_messages.jsonl",
    "cognitive_reflections.jsonl", "cognitive_protocol.json",
)
SCIENTIFIC_FILES = (
    "study_fundamentals.csv", "study_news_exposures.jsonl",
    "study_news_verifications.jsonl", "study_order_schedule.jsonl",
    "study_world_private.json",
)


def _replay_files(config: dict) -> tuple[str, ...]:
    files = REPLAY_FILES
    if config.get("decision_mode") == "cognitive_rule":
        files += COGNITIVE_FILES
        if config.get("scientific_world", {}).get("enabled"):
            files += SCIENTIFIC_FILES
    return files


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_path(root: Path) -> Path:
    manifests = list(root.glob("*.manifest.json"))
    if len(manifests) != 1:
        raise ValueError("Expected exactly one experiment manifest")
    return manifests[0]


def verify_run(run_dir: str | Path) -> dict:
    root = Path(run_dir)
    manifest = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
    if manifest["notes"]["status"] != "completed":
        raise ValueError(f"Experiment status is {manifest['notes']['status']}")
    required = set(REPLAY_FILES) | {"config.json", "summary.json", "source.json", "environment.json", "run.log"}
    if manifest["notes"].get("archive_schema_version", 1) >= 2:
        required.add("requirements-lock.txt")
    if manifest["config"].get("historical_input"):
        required.add("input_snapshot.csv")
    if manifest["config"].get("decision_mode") in {"cognitive_llm", "cognitive_rule"}:
        required.update(COGNITIVE_FILES)
    if manifest["config"].get("scientific_world", {}).get("enabled"):
        required.update(SCIENTIFIC_FILES)
    if manifest.get("parent_run_id"):
        required.add("reproduction.json")
    errors = sorted(required - set(manifest["artifacts"]))
    for name, artifact in manifest["artifacts"].items():
        path = root / name
        if (Path(name).name != name or path.is_symlink() or not path.is_file()
                or _sha256(path) != artifact["sha256"] or path.stat().st_size != artifact["bytes"]):
            errors.append(name)
    if errors:
        raise ValueError(f"Artifact integrity mismatch: {errors}")

    # File hashes alone do not establish that seed/config/snapshot identities agree.
    payload = json.loads((root / "config.json").read_text(encoding="utf-8"))
    cfg = ExperimentConfig.model_validate(payload)
    # Check the *archived* expanded configuration rather than injecting
    # defaults introduced by a newer code version into an old experiment ID.
    identity_config = json.loads(json.dumps(payload))
    if cfg.historical_input:
        identity_config["historical_input"]["path"] = "input_snapshot.csv"
        if _sha256(root / "input_snapshot.csv") != cfg.historical_input.sha256:
            raise ValueError("Historical input snapshot hash mismatch")
    world = json.loads((root / "world.json").read_text(encoding="utf-8"))
    population = json.loads((root / "population.json").read_text(encoding="utf-8"))
    snapshot_hash = stable_hash({"world": world, "population": population})
    expected = ExperimentManifest.build(module="market_experiment", seed=cfg.seed,
        dataset_snapshot_id=snapshot_hash, config=identity_config,
        feature_flags=manifest["feature_flags"], data_slice=manifest["data_slice"],
        model_version=cfg.llm.model if cfg.decision_mode in {"llm", "cognitive_llm"} else cfg.decision_mode)
    fields = ("module", "seed", "config", "config_hash", "dataset_snapshot_id", "data_snapshot_hash",
              "parameter_set_id", "experiment_id", "model_version")
    inconsistent = [field for field in fields if manifest[field] != getattr(expected, field)]
    if manifest["code_version"] != manifest["git_commit"]:
        inconsistent.append("code_version")
    source = json.loads((root / "source.json").read_text(encoding="utf-8"))
    if source["sha256"] != stable_hash(source["files"]) or source["sha256"] != manifest["notes"]["source_hash"]:
        inconsistent.append("source_hash")
    if source["git_dirty"] != manifest["notes"]["git_dirty"]:
        inconsistent.append("git_dirty")
    if manifest["notes"].get("archive_schema_version", 1) >= 2:
        environment = json.loads((root / "environment.json").read_text(encoding="utf-8"))
        expected_lock = "".join(f"{name}=={version}\n" for name, version in sorted(environment["installed_packages"].items()))
        if (root / "requirements-lock.txt").read_text(encoding="utf-8") != expected_lock:
            inconsistent.append("requirements-lock")
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    for field in ("run_id", "experiment_id"):
        if summary[field] != manifest[field]:
            inconsistent.append(f"summary.{field}")
    for field in ("status", "completed_steps", "source_hash", "output_origin", "determinism", "git_dirty"):
        if summary[field] != manifest["notes"][field]:
            inconsistent.append(f"summary.{field}")
    origin = "mock" if cfg.decision_mode == "mock" else "simulation"
    if manifest["notes"]["completed_steps"] != cfg.steps or summary["output_origin"] != origin:
        inconsistent.append("completion/origin")
    if len(population) != cfg.n_agents or len({p["agent_id"] for p in population}) != cfg.n_agents:
        inconsistent.append("population")
    for name, expected_rows in (("metrics", cfg.steps), ("social", cfg.steps),
                                ("agents", cfg.n_agents * (cfg.steps + 1)),
                                ("decisions", cfg.n_agents * cfg.steps)):
        with (root / f"{name}.csv").open(newline="", encoding="utf-8") as file:
            if sum(1 for _ in csv.DictReader(file)) != expected_rows:
                inconsistent.append(f"{name}.rows")
    if cfg.scientific_world.enabled:
        with (root / "study_fundamentals.csv").open(
            newline="", encoding="utf-8"
        ) as file:
            rows = list(csv.DictReader(file))
            if len(rows) != cfg.steps or [
                int(row["step"]) for row in rows
            ] != list(range(1, cfg.steps + 1)):
                inconsistent.append("scientific.fundamental_steps")
        def _records(filename):
            return [
                json.loads(line) for line in (root / filename).read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
        schedules = _records("study_order_schedule.jsonl")
        if len(schedules) != cfg.steps or any(
            row["step"] != step or
            len(row["ordered_agent_ids"]) != cfg.n_agents or
            len(set(row["ordered_agent_ids"])) != cfg.n_agents
            for step, row in enumerate(schedules, start=1)
        ):
            inconsistent.append("scientific.order_schedule")
        verdicts = _records("study_news_verifications.jsonl")
        if len(verdicts) != len(cfg.scientific_world.shocks):
            inconsistent.append("scientific.verification_count")
        observations = _records("cognitive_observations.jsonl")
        prior_seen_by_agent: dict[str, int] = {}
        for row in observations:
            aid, step = row["agent_id"], int(row["step"])
            previous = prior_seen_by_agent.get(aid, 0)
            signals = row.get("information_signals", {})
            claims = signals.get("received_claims", [])
            verdicts = signals.get("public_verifications", [])
            if (step <= previous
                    or set(signals) != {
                        "received_claims", "public_verifications",
                        "observed_source_track_records",
                    }
                    or any(not previous < item["received_step"] <= step
                           for item in claims)
                    or any(not previous < item["reveal_step"] <= step
                           for item in verdicts)
                    or "fundamental_value" in signals
                    or "truthful" in json.dumps(signals)
                    or (not cfg.cognitive.memory_enabled and row.get("episodic_memory"))):
                inconsistent.append("scientific.observation_as_of")
                break
            prior_seen_by_agent[aid] = step
    if manifest.get("parent_run_id"):
        report = json.loads((root / "reproduction.json").read_text(encoding="utf-8"))
        if report["original"]["run_id"] != manifest["parent_run_id"] or report["rerun_identity"]["run_id"] != manifest["run_id"]:
            inconsistent.append("reproduction.lineage")
    if inconsistent:
        raise ValueError(f"Archive metadata mismatch: {inconsistent}")
    return {"run_id": manifest["run_id"], "checked_files": len(manifest["artifacts"]), "status": "verified"}


def _identity(root: Path) -> dict:
    manifest_path = _manifest_path(root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {"run_id": manifest["run_id"], "experiment_id": manifest["experiment_id"],
            "git_commit": manifest["git_commit"], "source_hash": manifest["notes"].get("source_hash")}


def _save_reproduction(root: Path, report: dict) -> Path:
    path = root / "reproduction.json"
    write_json(path, report)
    manifest = load_experiment_manifest(_manifest_path(root))
    manifest.artifacts[path.name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    write_experiment_manifest(manifest, root)
    return path


def reproduce_run(run_dir: str | Path, output_root: str | Path) -> dict:
    verify_run(run_dir)
    root = Path(run_dir).resolve()
    payload = json.loads((root / "config.json").read_text(encoding="utf-8"))
    if payload["decision_mode"] in {"llm", "cognitive_llm"}:
        raise ValueError("Live LLM reruns require a new explicit run; they are not deterministic replays")
    original = _identity(root)
    original["manifest_sha256"] = _sha256(_manifest_path(root))
    if payload.get("historical_input"):
        payload["historical_input"]["path"] = str((root / "input_snapshot.csv").resolve())
    runner = ExperimentRunner(ExperimentConfig.model_validate(payload), parent_run_id=original["run_id"])
    try:
        path = runner.run(output_root)
    except BaseException as exc:
        if hasattr(runner, "output_dir"):
            _save_reproduction(runner.output_dir, {"schema_version": 1, "status": "rerun_failed",
                "timestamp": utc_now_iso(), "original": original, "rerun": str(runner.output_dir),
                "rerun_identity": _identity(runner.output_dir), "error_type": type(exc).__name__,
                "comparisons": []})
        raise
    result_files = _replay_files(payload)
    comparisons = [{"file": name, "original_sha256": _sha256(root / name),
                    "rerun_sha256": _sha256(path / name)} for name in result_files]
    for item in comparisons:
        item["matched"] = item["original_sha256"] == item["rerun_sha256"]
    mismatches = [item["file"] for item in comparisons if not item["matched"]]
    before = json.loads((root / "environment.json").read_text(encoding="utf-8"))
    after = json.loads((path / "environment.json").read_text(encoding="utf-8"))
    rerun_identity = _identity(path)
    report = {"schema_version": 1, "timestamp": utc_now_iso(),
              "status": "different" if mismatches else "matched", "original": original,
              "rerun_identity": rerun_identity, "rerun": str(path), "compared_files": len(result_files),
              "comparisons": comparisons, "mismatches": mismatches,
              "source_matches": original["source_hash"] == rerun_identity["source_hash"],
              "environment_differences": [key for key in ("python", "platform", "packages", "installed_packages", "backend")
                                          if before.get(key) != after.get(key)]}
    report_path = _save_reproduction(path, report)
    if mismatches:
        raise ValueError(f"Reproduction differs in {mismatches}; report archived at {report_path}")
    return {"status": "matched", "compared_files": len(result_files), "rerun": str(path),
            "report": str(report_path), "source_matches": report["source_matches"],
            "environment_differences": report["environment_differences"]}
