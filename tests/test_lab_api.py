import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from lab.api import create_app  # noqa: E402


def small_spec(**world):
    return {"scenario_id": "EXP-98", "version": "0.1.0", "title": "api test", "module": "E1", "phase": "P1",
            "course_codes": ["107349"], "question": "q", "objectives": ["o"], "limitations": ["l"],
            "world": {"agents": 12, "steps": 6, **world},
            "comparison": {"seeds": [3, 4, 5], "groups": [
                {"id": "A", "label": "base"},
                {"id": "B", "label": "panic", "events": [{"step": 2, "kind": "panic", "value": 3}]}]},
            "observables": ["price", "volume"], "outcome": {"primary": "cumulative_volume", "secondary": ["return_volatility"]}}


def wait(client, job_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/v1/experiments/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, token="")) as c:
        yield c


def test_catalog_endpoints(client):
    courses = client.get("/v1/courses", params={"program": "graduate"}).json()
    assert courses and all(c["program"] == "graduate" for c in courses)
    detail = client.get("/v1/courses/107349").json()
    assert {"EXP-01", "EXP-07"} <= {s["id"] for s in detail["scenarios"] if s["runnable"]}
    assert client.get("/v1/courses/999999").status_code == 404
    index = {s["id"]: s for s in client.get("/v1/scenarios").json()}
    assert len(index) == 27 and index["EXP-05"]["runnable"] and not index["EXP-19"]["runnable"]
    planned = client.get("/v1/scenarios/EXP-19").json()
    assert planned["spec"] is None and planned["entry"]["engine"] == "bank_ledger"
    card = client.get("/v1/scenarios/EXP-08/card")
    assert card.headers["content-type"].startswith("text/markdown") and "模型限制" in card.text
    assert "硕士" in client.get("/v1/lessons/LESSON-MKT-005/worksheet", params={"level": "graduate"}).text
    assert "负结果讨论" in client.get("/v1/lessons/LESSON-BEH-002/guide").text
    assert client.get("/v1/lessons/LESSON-NOPE-001").status_code == 404


def test_validate_reports_gate_result(client):
    assert client.post("/v1/scenarios/validate", json={"scenario_id": "EXP-07"}).json()["status"] == "supported"
    rejected = client.post("/v1/scenarios/validate", json={"scenario": small_spec(type="bank_ledger")}).json()
    assert rejected["status"] == "rejected" and rejected["issues"][0]["code"] == "UNSUPPORTED_WORLD"
    assert client.post("/v1/scenarios/validate", json={"scenario": {"title": "x"}}).status_code == 422
    assert client.post("/v1/scenarios/validate", json={"scenario_id": "EXP-07", "scenario": small_spec()}).status_code == 422
    assert client.post("/v1/scenarios/validate", json={"scenario_id": "EXP-19"}).status_code == 404


def test_experiment_lifecycle_and_comparison_reads(client):
    assert client.post("/v1/experiments", json={"scenario": small_spec()}).status_code == 403
    gate = client.post("/v1/experiments", json={"scenario": small_spec(decision_mode="llm"), "confirmed_by": "t"})
    assert gate.status_code == 422 and gate.json()["detail"]["capability"]["status"] == "rejected"

    created = client.post("/v1/experiments", json={"scenario": small_spec(), "confirmed_by": "teacher", "idempotency_key": "k1"})
    assert created.status_code == 202
    job = wait(client, created.json()["job_id"])
    assert job["status"] == "completed" and job["progress"] == {"done": 6, "total": 6, "failed": 0}
    again = client.post("/v1/experiments", json={"scenario": small_spec(), "confirmed_by": "teacher", "idempotency_key": "k1"})
    assert again.json()["job_id"] == job["job_id"]
    conflict = client.post("/v1/experiments", json={"scenario": small_spec(steps=5), "confirmed_by": "teacher", "idempotency_key": "k1"})
    assert conflict.status_code == 409

    cid = job["comparison_id"]
    assert [c["comparison_id"] for c in client.get("/v1/comparisons").json()] == [cid]
    assert client.get(f"/v1/comparisons/{cid}").json()["confirmed_by"] == "teacher"
    card = client.get(f"/v1/comparisons/{cid}/evidence", params={"metric": "return_volatility"}).json()
    assert card["available"] and len(card["values"]) == 6
    assert client.get(f"/v1/comparisons/{cid}/evidence", params={"metric": "alpha"}).status_code == 422
    rows = client.get(f"/v1/comparisons/{cid}/steps", params={"observable": ["price", "spread"]}).json()
    assert len(rows) == 6 * 6 and set(rows[0]) == {"group_id", "seed", "run_id", "step", "price", "spread"}
    assert any(r["spread"] is None for r in rows)  # undefined spread is null, not 0
    assert "证据报告" in client.get(f"/v1/comparisons/{cid}/report").text
    assert client.post(f"/v1/comparisons/{cid}/verify").json()["status"] == "verified"
    for bad in ("nope", "..", "x" * 200):
        assert client.get(f"/v1/comparisons/{bad}").status_code == 404


def test_single_seed_nan_is_serialised_as_null(client):
    spec = small_spec()
    spec["comparison"]["seeds"] = [3]
    job = wait(client, client.post("/v1/experiments", json={"scenario": spec, "confirmed_by": "t"}).json()["job_id"])
    card = client.get(f"/v1/comparisons/{job['comparison_id']}/evidence").json()
    assert card["group_summary"][0]["sd"] is None


def test_token_protects_write_endpoints(tmp_path):
    with TestClient(create_app(tmp_path, token="secret")) as c:
        body = {"scenario": small_spec(), "confirmed_by": "t"}
        assert c.post("/v1/experiments", json=body).status_code == 401
        assert c.post("/v1/experiments", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.post("/v1/experiments", json=body, headers={"Authorization": "Bearer secret"})
        assert ok.status_code == 202
        assert c.get("/v1/courses").status_code == 200
        wait(c, ok.json()["job_id"])
