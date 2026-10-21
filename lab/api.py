"""Civitas Lab HTTP API v1 (plan 7.5).

Run with ``python -m uvicorn lab.api:app``. Endpoints are a thin layer over
lab.*: every run still passes the capability gate and needs a named teacher
confirmation, and results are read back from the archives on disk. Runs
execute one at a time in a background worker so classroom latency stays
predictable; job records are in memory, comparisons persist on disk.

The two AI endpoints wrap lab.ai: a scenario draft is only generated and
gated, never run (running it is an ordinary POST /v1/experiments with a teacher
confirmation), and an explanation reads a comparison resolved by id, never a
client path. lab.ai failures keep their code and map to the statuses in
AI_FAILURE_STATUS; no fallback text is returned.

Set CIVITAS_LAB_TOKEN to require ``Authorization: Bearer <token>`` on the
endpoints that start work, call a model or write files. Without it the API is
meant for a single machine or a closed pilot network only.
"""
from __future__ import annotations

import math
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import lab.ai as lab_ai
from core.reproducibility import utc_now_iso
from lab.calculators import compute
from lab.capability import check_scenario
from lab.catalog import load_catalog
from lab.compare import comparison_frames, evidence_card
from lab.compiler import scenario_hash
from lab.export import calculator_report, evidence_report, pilot_record, scenario_card, student_feedback, teacher_guide, worksheet
from lab.observables import OBSERVABLES, OUTCOMES, STEP_COLUMNS
from lab.service import LAB_MANIFEST, list_comparisons, load_manifest, run_scenario, verify_comparison
from lab.specs import ScenarioSpec

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPARISON_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
# lab.ai failure code -> HTTP status; the body is {"detail": <to_dict()>} with status "failed".
AI_FAILURE_STATUS = {
    "empty_input": 422, "input_too_long": 422, "unknown_metric": 422,
    "unknown_course": 404,
    "archive_not_verified": 409, "no_evidence": 409, "not_simulation": 409,
    "budget_exceeded": 429,
    "llm_error": 502, "invalid_json": 502, "schema_invalid": 502, "unsupported_number": 502,
    "llm_not_configured": 503, "llm_dependencies_missing": 503,
}
_AI_SERVICE = ("budget_exceeded", "llm_error", "invalid_json", "llm_not_configured", "llm_dependencies_missing")
DRAFT_FAILURES = ("empty_input", "input_too_long", "unknown_course", "schema_invalid", *_AI_SERVICE)
EXPLAIN_FAILURES = ("empty_input", "input_too_long", "unknown_metric", "archive_not_verified", "no_evidence",
                    "not_simulation", "unsupported_number", *_AI_SERVICE)


class ScenarioRef(BaseModel):
    """Exactly one of a catalog scenario id or an inline ScenarioSpec document."""
    model_config = ConfigDict(extra="forbid")
    scenario_id: str | None = None
    scenario: dict | None = None


class ExperimentRequest(ScenarioRef):
    confirmed_by: str = ""
    idempotency_key: str | None = Field(None, min_length=1, max_length=128)


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    course_code: str
    goal: str


class ExplainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    metric: str | None = None


class CalculationRequest(BaseModel):
    """Parameters are merged as spec defaults <- preset <- params."""
    model_config = ConfigDict(extra="forbid")
    preset: str | None = None
    params: dict = Field(default_factory=dict)


def _clean(value):
    """JSON-safe copy: NaN/inf become null instead of failing serialisation."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _ai_responses(codes: tuple[str, ...]) -> dict:
    """OpenAPI entries listing which failure codes each status carries."""
    by_status: dict[int, list[str]] = {}
    for code in codes:
        by_status.setdefault(AI_FAILURE_STATUS[code], []).append(code)
    return {status: {"description": "detail.failure: " + ", ".join(names)} for status, names in sorted(by_status.items())}


def _ai_result(result):
    """Success body, or the same dict as an error with the status of its failure code."""
    body = _clean(result.to_dict())
    if not result.ok:
        raise HTTPException(AI_FAILURE_STATUS.get(result.failure, 500), body)
    return body


class JobQueue:
    def __init__(self, output_root: Path, max_workers: int):
        self.output_root = output_root
        self.max_workers = max_workers
        self.jobs: dict[str, dict] = {}
        self.keys: dict[str, tuple[str, str]] = {}
        self.lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None

    def submit(self, spec: ScenarioSpec, confirmed_by: str, key: str | None) -> tuple[dict, bool]:
        fingerprint = f"{scenario_hash(spec)}:{confirmed_by}"
        with self.lock:
            if key and key in self.keys:
                job_id, previous = self.keys[key]
                if previous != fingerprint:
                    raise HTTPException(409, "idempotency_key was already used for a different request")
                return dict(self.jobs[job_id]), False
            job_id = uuid.uuid4().hex[:12]
            job = {"job_id": job_id, "status": "queued", "scenario_id": spec.scenario_id,
                   "scenario_hash": scenario_hash(spec), "confirmed_by": confirmed_by, "created_at": utc_now_iso(),
                   "progress": {"done": 0, "total": len(spec.comparison.groups) * len(spec.comparison.seeds), "failed": 0},
                   "comparison_id": None, "error": None}
            self.jobs[job_id] = job
            if key:
                self.keys[key] = (job_id, fingerprint)
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="civitas-lab")
            self._executor.submit(self._run, job_id, spec, confirmed_by)
            return dict(job), True

    def _update(self, job_id: str, **fields) -> None:
        with self.lock:
            self.jobs[job_id].update(fields)

    def _run(self, job_id: str, spec: ScenarioSpec, confirmed_by: str) -> None:
        self._update(job_id, status="running", started_at=utc_now_iso())

        def progress(done: int, total: int, entry: dict) -> None:
            with self.lock:
                p = self.jobs[job_id]["progress"]
                p.update(done=done, total=total, failed=p["failed"] + (entry["status"] != "completed"))

        try:
            root = run_scenario(spec, confirmed_by=confirmed_by, output_root=self.output_root, on_progress=progress)
            self._update(job_id, status=load_manifest(root)["status"], comparison_id=root.name, ended_at=utc_now_iso())
        except Exception as exc:  # recorded on the job; never reported as success
            self._update(job_id, status="failed", error=f"{type(exc).__name__}: {exc}", ended_at=utc_now_iso())

    def get(self, job_id: str) -> dict:
        with self.lock:
            if job_id not in self.jobs:
                raise HTTPException(404, "unknown job")
            job = dict(self.jobs[job_id])
            job["progress"] = dict(job["progress"])
            return job


def create_app(output_root: str | Path | None = None, *, token: str | None = None, max_workers: int = 1) -> FastAPI:
    output_root = Path(output_root or PROJECT_ROOT / "outputs/lab").resolve()
    token = token if token is not None else os.environ.get("CIVITAS_LAB_TOKEN", "")
    queue = JobQueue(output_root, max_workers)
    app = FastAPI(title="Civitas Lab API", version="0.1.0",
                  description="课程驱动的经济金融实验教学接口。仿真教学用途，结果不是市场预测或投资建议。")
    app.state.jobs = queue

    def require_token(authorization: str = Header("")) -> None:
        if token and authorization != f"Bearer {token}":
            raise HTTPException(401, "missing or invalid bearer token")

    def resolve(ref: ScenarioRef) -> ScenarioSpec:
        if (ref.scenario_id is None) == (ref.scenario is None):
            raise HTTPException(422, "provide exactly one of scenario_id or scenario")
        if ref.scenario_id is not None:
            catalog = load_catalog()
            if ref.scenario_id not in catalog.scenarios:
                raise HTTPException(404, f"no runnable spec for {ref.scenario_id}")
            return catalog.scenarios[ref.scenario_id]
        try:
            return ScenarioSpec.model_validate(ref.scenario)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from None

    def comparison_dir(comparison_id: str) -> Path:
        if not COMPARISON_ID.match(comparison_id):
            raise HTTPException(404, "unknown comparison")
        root = (output_root / comparison_id).resolve()
        if root.parent != output_root or not (root / LAB_MANIFEST).is_file():
            raise HTTPException(404, "unknown comparison")
        return root

    def markdown(text: str, name: str = "handout", fmt: str = "md"):
        """Handout as Markdown, or as PDF with ?format=pdf (needs reportlab)."""
        if fmt == "md":
            return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")
        try:
            from lab.pdf import markdown_pdf
            data = markdown_pdf(text, title=name)
        except ImportError as exc:
            raise HTTPException(501, str(exc)) from None
        return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})

    @app.get("/v1/courses")
    def courses(q: str = "", program: Literal["undergraduate", "graduate"] | None = None):
        catalog = load_catalog()
        return [{**c, "review_status": "proposed_pending_teacher_review",
                 "scenarios": [{"id": e["id"], "runnable": e["runnable"]} for e in catalog.scenarios_for_course(c["code"])]}
                for c in catalog.search(q) if program is None or c["program"] == program]

    @app.get("/v1/courses/{code}")
    def course(code: str):
        catalog = load_catalog()
        if code not in {c["code"] for c in catalog.courses}:
            raise HTTPException(404, "unknown course")
        return {"course": {**catalog.course(code), "review_status": "proposed_pending_teacher_review"},
                "scenarios": catalog.scenarios_for_course(code),
                "lessons": [{"lesson_id": l.lesson_id, "title": l.title} for l in catalog.lessons_for_course(code)]}

    @app.get("/v1/scenarios")
    def scenarios():
        catalog = load_catalog()
        return [{**e, "runnable": e["id"] in catalog.scenarios} for e in catalog.index]

    @app.get("/v1/scenarios/{scenario_id}")
    def scenario(scenario_id: str):
        catalog = load_catalog()
        if scenario_id not in {e["id"] for e in catalog.index}:
            raise HTTPException(404, "unknown scenario")
        spec, calculator = catalog.scenarios.get(scenario_id), catalog.calculators.get(scenario_id)
        return {"entry": catalog.entry(scenario_id), "kind": catalog.kind(scenario_id),
                "runnable": catalog.kind(scenario_id) is not None,
                "spec": spec.model_dump(mode="json") if spec else None,
                "scenario_hash": scenario_hash(spec) if spec else None,
                "calculator": calculator.model_dump(mode="json") if calculator else None}

    def calculation(lab_id: str, request: CalculationRequest):
        catalog = load_catalog()
        if lab_id not in catalog.calculators:
            raise HTTPException(404, "unknown calculator lab")
        spec = catalog.calculators[lab_id]
        presets = {p.name: p.params for p in spec.presets}
        if request.preset is not None and request.preset not in presets:
            raise HTTPException(422, f"unknown preset; available: {', '.join(presets)}")
        try:
            return spec, compute(spec.calculator, {**spec.defaults, **presets.get(request.preset, {}), **request.params})
        except ValidationError as exc:
            raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from None

    @app.get("/v1/calculators")
    def calculators():
        return [{"lab_id": c.lab_id, "title": c.title, "calculator": c.calculator, "course_codes": list(c.course_codes),
                 "phase": c.phase, "presets": [p.name for p in c.presets], "review_status": c.review.status,
                 "origin": "deterministic_calculation"} for c in load_catalog().calculators.values()]

    @app.get("/v1/calculators/{lab_id}")
    def calculator(lab_id: str):
        catalog = load_catalog()
        if lab_id not in catalog.calculators:
            raise HTTPException(404, "unknown calculator lab")
        return catalog.calculators[lab_id].model_dump(mode="json")

    @app.post("/v1/calculators/{lab_id}/compute")
    def calculator_compute(lab_id: str, request: CalculationRequest):
        """确定性公式计算：不是仿真；相同输入总得到相同结果（params_hash）。"""
        return _clean(calculation(lab_id, request)[1])

    @app.post("/v1/calculators/{lab_id}/report")
    def calculator_result_report(lab_id: str, request: CalculationRequest, fmt: Literal["md", "pdf"] = Query("md", alias="format")):
        spec, computed = calculation(lab_id, request)
        return markdown(calculator_report(spec, computed), f"{lab_id}-result", fmt)

    @app.get("/v1/scenarios/{scenario_id}/card")
    def card(scenario_id: str, fmt: Literal["md", "pdf"] = Query("md", alias="format")):
        catalog = load_catalog()
        if scenario_id not in catalog.scenarios:
            raise HTTPException(404, f"no runnable spec for {scenario_id}")
        return markdown(scenario_card(catalog.scenarios[scenario_id], catalog), f"{scenario_id}-card", fmt)

    @app.post("/v1/scenarios/validate")
    def validate(ref: ScenarioRef):
        spec = resolve(ref)
        return {**check_scenario(spec).to_dict(), "scenario_hash": scenario_hash(spec)}

    @app.get("/v1/registry")
    def registry():
        return {"observables": {k: vars(v) for k, v in OBSERVABLES.items()},
                "outcomes": {k: vars(v) for k, v in OUTCOMES.items()}, "step_columns": STEP_COLUMNS}

    @app.get("/v1/lessons")
    def lessons():
        return [{"lesson_id": l.lesson_id, "title": l.title, "duration_minutes": l.duration_minutes,
                 "scenarios": list(l.scenarios), "review_status": l.review.status} for l in load_catalog().lessons.values()]

    def lesson_or_404(lesson_id: str):
        catalog = load_catalog()
        if lesson_id not in catalog.lessons:
            raise HTTPException(404, "unknown lesson")
        return catalog, catalog.lessons[lesson_id]

    @app.get("/v1/lessons/{lesson_id}")
    def lesson(lesson_id: str):
        return lesson_or_404(lesson_id)[1].model_dump(mode="json")

    @app.get("/v1/lessons/{lesson_id}/guide")
    def guide(lesson_id: str, fmt: Literal["md", "pdf"] = Query("md", alias="format")):
        catalog, spec = lesson_or_404(lesson_id)
        return markdown(teacher_guide(spec, catalog), f"{lesson_id}-guide", fmt)

    @app.get("/v1/lessons/{lesson_id}/worksheet")
    def lesson_worksheet(lesson_id: str, level: Literal["undergraduate", "graduate"] = "undergraduate", fmt: Literal["md", "pdf"] = Query("md", alias="format")):
        catalog, spec = lesson_or_404(lesson_id)
        return markdown(worksheet(spec, catalog, level), f"{lesson_id}-worksheet-{level}", fmt)

    @app.get("/v1/lessons/{lesson_id}/pilot-record")
    def lesson_pilot_record(lesson_id: str, fmt: Literal["md", "pdf"] = Query("md", alias="format")):
        catalog, spec = lesson_or_404(lesson_id)
        return markdown(pilot_record(spec, catalog), f"{lesson_id}-pilot-record", fmt)

    @app.get("/v1/lessons/{lesson_id}/feedback-form")
    def lesson_feedback_form(lesson_id: str, fmt: Literal["md", "pdf"] = Query("md", alias="format")):
        catalog, spec = lesson_or_404(lesson_id)
        return markdown(student_feedback(spec, catalog), f"{lesson_id}-feedback", fmt)

    @app.post("/v1/experiments", status_code=202, dependencies=[Depends(require_token)])
    def create_experiment(request: ExperimentRequest):
        spec = resolve(request)
        report = check_scenario(spec)
        if not report.ok:
            raise HTTPException(422, {"message": "rejected by capability gate", "capability": report.to_dict()})
        if spec.approval.require_teacher_confirmation and not request.confirmed_by.strip():
            raise HTTPException(403, "teacher confirmation (confirmed_by) is required before running")
        job, _ = queue.submit(spec, request.confirmed_by.strip(), request.idempotency_key)
        return {**job, "capability": report.to_dict()}

    @app.get("/v1/experiments/{job_id}")
    def experiment(job_id: str):
        return queue.get(job_id)

    @app.get("/v1/comparisons")
    def comparisons():
        rows = []
        for root in list_comparisons(output_root):
            m = load_manifest(root)
            rows.append({k: m.get(k) for k in ("comparison_id", "scenario_id", "scenario_version", "status",
                                               "review_status", "confirmed_by", "created_at")})
        return rows

    @app.get("/v1/comparisons/{comparison_id}")
    def comparison(comparison_id: str):
        return load_manifest(comparison_dir(comparison_id))

    @app.get("/v1/comparisons/{comparison_id}/evidence")
    def evidence(comparison_id: str, metric: str | None = None):
        if metric is not None and metric not in OUTCOMES:
            raise HTTPException(422, f"unknown outcome metric; available: {', '.join(OUTCOMES)}")
        return _clean(evidence_card(comparison_dir(comparison_id), metric))

    @app.get("/v1/comparisons/{comparison_id}/steps")
    def steps(comparison_id: str, observable: list[str] = Query(default=[])):
        unknown = set(observable) - set(STEP_COLUMNS)
        if unknown:
            raise HTTPException(422, f"unknown observable: {', '.join(sorted(unknown))}")
        frame, _ = comparison_frames(comparison_dir(comparison_id))
        if frame.empty:
            return []
        columns = ["group_id", "seed", "run_id", "step"]
        columns += list(dict.fromkeys(c for o in observable for c in STEP_COLUMNS[o])) or \
            [c for c in frame.columns if c not in columns]
        return _clean(frame[columns].to_dict(orient="records"))

    @app.get("/v1/comparisons/{comparison_id}/report")
    def report(comparison_id: str, fmt: Literal["md", "pdf"] = Query("md", alias="format")):
        return markdown(evidence_report(comparison_dir(comparison_id)), f"{comparison_id}-report", fmt)

    @app.post("/v1/comparisons/{comparison_id}/verify", dependencies=[Depends(require_token)])
    def verify(comparison_id: str):
        return verify_comparison(comparison_dir(comparison_id))

    @app.post("/v1/scenarios/draft", dependencies=[Depends(require_token)], responses=_ai_responses(DRAFT_FAILURES))
    def draft(request: DraftRequest):
        """AI 场景草案：只生成并过门禁，从不运行；运行仍走 POST /v1/experiments 与教师确认。"""
        body = _ai_result(lab_ai.draft_scenario(request.goal, request.course_code, output_root=output_root / "ai_drafts"))
        if body.get("path"):  # relative to the archive root; never expose server paths
            body["path"] = Path(body["path"]).relative_to(output_root).as_posix()
        return body

    @app.post("/v1/comparisons/{comparison_id}/explain", dependencies=[Depends(require_token)],
              responses=_ai_responses(EXPLAIN_FAILURES))
    def explain(comparison_id: str, request: ExplainRequest):
        """AI 证据解释：数值只能来自该对照的证据卡，并附模型限制。"""
        return _ai_result(lab_ai.explain_comparison(comparison_dir(comparison_id), request.question, metric=request.metric))

    return app


app = create_app()
