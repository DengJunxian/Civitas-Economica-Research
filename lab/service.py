"""Scenario execution service: the only path from a teaching scenario to runs.

check gate -> require teacher confirmation -> compile -> ExperimentRunner per
(group, seed) -> lab manifest. Each child run keeps its own unchanged core
manifest; the lab manifest only adds teaching links (scenario version and
hash, groups, confirmation) and is rewritten after every run so interrupted
comparisons still show which runs exist and which failed.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.experiments.runner import ExperimentRunner, write_json
from core.experiments.verify import verify_run
from core.reproducibility import stable_hash, utc_now_iso
from lab.capability import CapabilityReport, check_scenario
from lab.compiler import compile_scenario, scenario_hash
from lab.specs import ScenarioSpec

LAB_MANIFEST = "lab_manifest.json"
LAB_SCHEMA_VERSION = 1
COMPARISON_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")


class ScenarioRejected(ValueError):
    def __init__(self, report: CapabilityReport):
        self.report = report
        errors = "; ".join(f"{i.path}: {i.message}" for i in report.issues if i.severity == "error")
        super().__init__(f"Scenario {report.scenario_id} rejected by capability gate: {errors}")


class ConfirmationRequired(PermissionError):
    pass


def new_comparison_id(spec: ScenarioSpec) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", spec.scenario_id).strip("-")
    return f"{slug}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


def run_scenario(spec: ScenarioSpec, *, confirmed_by: str = "", output_root: str | Path = "outputs/lab",
                 on_progress: Callable[[int, int, dict], None] | None = None, comparison_id: str | None = None) -> Path:
    report = check_scenario(spec)
    if not report.ok:
        raise ScenarioRejected(report)
    if spec.approval.require_teacher_confirmation and not confirmed_by.strip():
        raise ConfirmationRequired("Teacher confirmation is required before running this scenario")
    compiled = compile_scenario(spec)
    comparison_id = comparison_id or new_comparison_id(spec)
    if not COMPARISON_ID.match(comparison_id):
        raise ValueError(f"invalid comparison_id {comparison_id!r}")
    root = Path(output_root).resolve() / comparison_id
    root.mkdir(parents=True, exist_ok=False)
    write_json(root / "scenario.json", spec.model_dump(mode="json"))
    write_json(root / "capability.json", report.to_dict())
    manifest = {
        "lab_schema_version": LAB_SCHEMA_VERSION, "comparison_id": comparison_id,
        "scenario_id": spec.scenario_id, "scenario_version": spec.version,
        "scenario_hash": scenario_hash(spec), "spec_version": spec.spec_version,
        "course_codes": list(spec.course_codes), "levels": list(spec.levels),
        "review_status": spec.review.status, "purpose": "teaching_simulation",
        "output_origin": "simulation", "pairing": spec.comparison.pairing,
        "reference_group": spec.comparison.reference, "seeds": list(spec.comparison.seeds),
        "groups": [{"id": g.id, "label": g.label} for g in spec.comparison.groups],
        "confirmed_by": confirmed_by.strip(), "created_at": utc_now_iso(), "status": "running", "runs": [],
    }
    write_json(root / LAB_MANIFEST, manifest)
    try:
        for index, item in enumerate(compiled, start=1):
            entry = {"group_id": item.group_id, "seed": item.seed,
                     "config_hash": stable_hash(item.config.model_dump(mode="json")), "status": "running"}
            manifest["runs"].append(entry)
            write_json(root / LAB_MANIFEST, manifest)  # an interrupted comparison still shows the run in flight
            runner = ExperimentRunner(item.config)
            try:
                path = runner.run(root / "runs")
                summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
                entry.update(status="completed", run_id=path.name, path=str(path.relative_to(root)),
                             experiment_id=summary["experiment_id"])
            except Exception as exc:
                entry.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:500])
                if hasattr(runner, "output_dir"):
                    entry.update(run_id=runner.output_dir.name, path=str(runner.output_dir.relative_to(root)))
            write_json(root / LAB_MANIFEST, manifest)
            if on_progress:
                on_progress(index, len(compiled), entry)
        manifest["status"] = "completed" if all(r["status"] == "completed" for r in manifest["runs"]) else "partial_failure"
    except BaseException:
        manifest["status"] = "interrupted"
        raise
    finally:
        manifest["ended_at"] = utc_now_iso()
        write_json(root / LAB_MANIFEST, manifest)
    return root


def load_manifest(root: str | Path) -> dict:
    return json.loads((Path(root) / LAB_MANIFEST).read_text(encoding="utf-8"))


def list_comparisons(output_root: str | Path = "outputs/lab") -> list[Path]:
    return sorted((p.parent for p in Path(output_root).glob(f"*/{LAB_MANIFEST}")), reverse=True)


def verify_comparison(root: str | Path) -> dict:
    """Core integrity check of every completed child run plus scenario identity."""
    root = Path(root)
    manifest = load_manifest(root)
    spec = ScenarioSpec.model_validate(json.loads((root / "scenario.json").read_text(encoding="utf-8")))
    problems = []
    if scenario_hash(spec) != manifest["scenario_hash"]:
        problems.append("scenario_hash")
    expected = {(c.group_id, c.seed): stable_hash(c.config.model_dump(mode="json")) for c in compile_scenario(spec)}
    runs = []
    for entry in manifest["runs"]:
        if entry["status"] != "completed":
            runs.append({**entry, "verified": False})
            continue
        if expected.get((entry["group_id"], entry["seed"])) != entry["config_hash"]:
            problems.append(f"config_hash:{entry['run_id']}")
        try:
            verify_run(root / entry["path"])
            runs.append({"run_id": entry["run_id"], "group_id": entry["group_id"], "seed": entry["seed"], "verified": True})
        except Exception as exc:
            problems.append(f"{entry['run_id']}: {exc}")
            runs.append({"run_id": entry["run_id"], "group_id": entry["group_id"], "seed": entry["seed"], "verified": False})
    status = "verified" if not problems and manifest["status"] == "completed" else "not_verified"
    return {"comparison_id": manifest["comparison_id"], "status": status, "problems": problems, "runs": runs}


def run_scenario_job(spec_payload: dict, confirmed_by: str, output_root: str, comparison_id: str) -> None:
    """Child-process entry point (picklable for the spawn start method)."""
    run_scenario(ScenarioSpec.model_validate(spec_payload), confirmed_by=confirmed_by,
                 output_root=output_root, comparison_id=comparison_id)


def mark_stopped(root: str | Path, status: str, reason: str) -> None:
    """Record that a comparison was stopped from outside (e.g. a timeout); completed runs stay as they are."""
    path = Path(root) / LAB_MANIFEST
    if not path.is_file():
        return
    manifest = load_manifest(root)
    for entry in manifest["runs"]:
        if entry["status"] == "running":
            entry.update(status="interrupted", error=reason)
    manifest.update(status=status, error=reason, ended_at=utc_now_iso())
    write_json(path, manifest)


def manifest_progress(root: str | Path, total: int) -> dict:
    path = Path(root) / LAB_MANIFEST
    if not path.is_file():
        return {"done": 0, "total": total, "failed": 0}
    runs = load_manifest(root)["runs"]
    finished = [r for r in runs if r["status"] != "running"]
    return {"done": len(finished), "total": total, "failed": sum(r["status"] != "completed" for r in finished)}
