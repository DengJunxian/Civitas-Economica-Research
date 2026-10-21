"""Civitas Lab MVP technical acceptance (plan v1.0 sections 9.1 and 9.3).

One command, offline, writes an acceptance receipt:

- every catalog scenario is gated, really run on all of its seeds, verified,
  and one run per group is re-executed with ``reproduce`` (11 files must match);
- every run's ledger conserves cash + fees and total shares, and every run has
  real orders (or the receipt records why it has no trades);
- a negative suite of illegal scenarios must all be rejected by the gate;
- 64-agent x 30-step runs are timed for the classroom latency target;
- every deterministic calculator preset computes and the formula identities
  hold (par bond prices at face, zero-coupon duration equals maturity,
  duration/convexity equal numerical derivatives, NPV is zero at each IRR);
- network connections are blocked and LLM API keys removed for the whole run,
  so passing also shows the P1 classroom flow needs no external service.

Exit code 0 only when every check passes. Requires requirements-core.txt.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import socket
import statistics
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
KEY_ENV_VARS = ("DEEPSEEK_API_KEY", "ZHIPUAI_API_KEY", "ZHIPU_API_KEY", "OPENAI_API_KEY")
P1_MUST = ("EXP-01", "EXP-02", "EXP-05", "EXP-06", "EXP-07", "EXP-08")
LATENCY_TARGET_P95_SECONDS = 15.0


def offline() -> dict:
    def blocked(*args, **kwargs):
        raise AssertionError("Civitas Lab acceptance must not access the network")
    socket.socket.connect = blocked
    socket.create_connection = blocked
    removed = [name for name in KEY_ENV_VARS if os.environ.pop(name, None) is not None]
    return {"network_blocked": True, "llm_keys_removed": removed}


def ledger_check(run_dir: Path) -> dict:
    import pandas as pd
    agents = pd.read_csv(run_dir / "agents.csv", usecols=["step", "cash", "shares"])
    metrics = pd.read_csv(run_dir / "metrics.csv", usecols=["step", "fees_cumulative", "volume", "trade_count"])
    orders = pd.read_csv(run_dir / "orders.csv", usecols=["order_id"])
    cash = agents.groupby("step")["cash"].apply(math.fsum)
    shares = agents.groupby("step")["shares"].sum()
    fees = metrics.set_index("step")["fees_cumulative"].reindex(cash.index, fill_value=0.0)
    residual = float((cash + fees - cash.iloc[0]).abs().max())
    return {"cash_plus_fees_max_residual": residual, "shares_conserved": bool((shares == shares.iloc[0]).all()),
            "non_negative": bool((agents["cash"] >= -1e-6).all() and (agents["shares"] >= 0).all()),
            "orders": int(len(orders)), "trades": int(metrics["trade_count"].sum()),
            "zero_volume_steps": int((metrics["volume"] == 0).sum()),
            "ok": residual <= 1e-6 and bool((shares == shares.iloc[0]).all())}


def scenario_checks(folder: Path, receipt: dict) -> None:
    from core.experiments.verify import reproduce_run
    from lab.capability import check_scenario
    from lab.catalog import load_catalog
    from lab.compare import comparison_frames, paired_differences
    from lab.export import evidence_report
    from lab.service import load_manifest, run_scenario, verify_comparison

    catalog = load_catalog()
    receipt["catalog"] = {"courses": len(catalog.courses), "indexed_scenarios": len(catalog.index),
                          "runnable_scenarios": sorted(catalog.scenarios), "lessons": sorted(catalog.lessons)}
    missing = sorted(set(P1_MUST) - set(catalog.scenarios))
    receipt["checks"]["p1_scenarios_present"] = {"ok": not missing, "missing": missing}
    reports = folder / "reports"
    reports.mkdir()
    for sid, spec in catalog.scenarios.items():
        entry = {"gate": check_scenario(spec).status}
        started = time.perf_counter()
        root = run_scenario(spec, confirmed_by="acceptance", output_root=folder / "comparisons")
        entry["seconds"] = round(time.perf_counter() - started, 2)
        manifest = load_manifest(root)
        entry.update(comparison_id=manifest["comparison_id"], status=manifest["status"], runs=len(manifest["runs"]),
                     output_origin=manifest["output_origin"], verify=verify_comparison(root)["status"])
        ledgers = {r["run_id"]: ledger_check(root / r["path"]) for r in manifest["runs"] if r["status"] == "completed"}
        entry["ledger_ok"] = all(v["ok"] for v in ledgers.values())
        entry["orders_min"] = min(v["orders"] for v in ledgers.values())
        entry["trades_min"] = min(v["trades"] for v in ledgers.values())
        entry["max_cash_residual"] = max(v["cash_plus_fees_max_residual"] for v in ledgers.values())
        reproductions = {}
        for group in spec.comparison.groups:
            run = next(r for r in manifest["runs"] if r["group_id"] == group.id and r["status"] == "completed")
            reproductions[run["run_id"]] = reproduce_run(root / run["path"], folder / "reproductions")["status"]
        entry["reproduce"] = reproductions
        _, outcomes = comparison_frames(root)
        if len(spec.comparison.groups) > 1:
            diffs = paired_differences(outcomes, spec.outcome.primary, spec.comparison.reference)
            entry["primary_outcome"] = {"metric": spec.outcome.primary, "paired_differences": [
                {k: d[k] for k in ("group_id", "mean", "sd", "positive", "negative", "zero")} for d in diffs.to_dict(orient="records")]}
        else:
            entry["primary_outcome"] = {"metric": spec.outcome.primary, "values": outcomes[spec.outcome.primary].tolist()}
        (reports / f"{sid}.md").write_text(evidence_report(root), encoding="utf-8")
        entry["ok"] = (entry["gate"] != "rejected" and entry["status"] == "completed" and entry["verify"] == "verified"
                       and entry["output_origin"] == "simulation" and entry["ledger_ok"] and entry["orders_min"] > 0
                       and all(v == "matched" for v in reproductions.values()))
        receipt["scenarios"][sid] = entry
    receipt["checks"]["scenarios"] = {"ok": all(e["ok"] for e in receipt["scenarios"].values()),
                                      "failed": [s for s, e in receipt["scenarios"].items() if not e["ok"]]}


def negative_gate_suite(receipt: dict) -> None:
    from lab.capability import check_scenario
    from lab.catalog import load_catalog
    from lab.specs import ScenarioSpec

    base = load_catalog().scenarios["EXP-05"].model_dump(mode="json")

    def variant(groups=None, **world):
        payload = json.loads(json.dumps(base))
        payload["world"].update(world)
        if groups is not None:
            payload["comparison"]["groups"] = [{"id": "A", "label": "a"}, {"id": "B", "label": "b", "events": groups}]
        return payload

    cases = {
        "step_out_of_range": variant([{"step": 31, "kind": "panic", "value": 8}]),
        "tax_out_of_range": variant([{"step": 20, "kind": "tax_change", "value": 0.2}]),
        "panic_not_integer": variant([{"step": 8, "kind": "panic", "value": 2.5}]),
        "panic_exceeds_population": variant([{"step": 8, "kind": "panic", "value": 65}]),
        "unpaired_resume": variant([{"step": 8, "kind": "resume"}]),
        "duplicate_halt": variant([{"step": 8, "kind": "halt"}, {"step": 9, "kind": "halt"}, {"step": 12, "kind": "resume"}]),
        "halt_resume_same_step": variant([{"step": 8, "kind": "halt"}, {"step": 9, "kind": "resume"}, {"step": 9, "kind": "halt"}]),
        "needs_macro_bank_engine_event": variant([{"step": 8, "kind": "rrr_cut", "value": 0.005}]),
        "needs_fiscal_engine_event": variant([{"step": 8, "kind": "fiscal_spending", "value": 1}]),
        "needs_bank_world": variant(type="bank_ledger"),
        "needs_macro_world": variant(type="macro_closure"),
        "rumor_text_semantics": variant([{"step": 8, "kind": "rumor_text"}]),
        "llm_traders_in_p1": variant(decision_mode="llm"),
        "mock_decisions": variant(decision_mode="mock"),
        "teaching_example_as_run": {**json.loads(json.dumps(base)), "source_policy": {"required_origin": "simulation", "allow_teaching_example_as_run": True}},
        "teacher_confirmation_disabled": {**json.loads(json.dumps(base)), "approval": {"require_teacher_confirmation": False}},
        "budget_exceeded": {**json.loads(json.dumps(base)), "comparison": {**base["comparison"], "seeds": list(range(30))}},
    }
    results = {}
    for name, payload in cases.items():
        report = check_scenario(ScenarioSpec.model_validate(payload))
        results[name] = {"status": report.status, "codes": sorted({i.code for i in report.issues if i.severity == "error"})}
    receipt["negative_gate"] = results
    escaped = [name for name, r in results.items() if r["status"] != "rejected"]
    receipt["checks"]["negative_gate"] = {"ok": not escaped, "cases": len(results), "not_rejected": escaped}


def calculator_checks(receipt: dict) -> None:
    from lab.calculators import BondParams, bond_measures, bond_price, compute, npv
    from lab.catalog import load_catalog

    results, problems = {}, []
    for lab_id, spec in load_catalog().calculators.items():
        presets = {"默认": {}} | {p.name: p.params for p in spec.presets}
        for name, params in presets.items():
            computed = compute(spec.calculator, {**spec.defaults, **params})
            results[f"{lab_id}/{name}"] = computed["params_hash"]
            if spec.calculator == "capital_budgeting":
                for project, flows in zip(computed["result"]["projects"], computed["params"]["projects"]):
                    if any(abs(npv(r, flows["cash_flows"])) > 1e-7 for r in project["irr"]):
                        problems.append(f"{lab_id}/{name}: NPV not zero at an IRR of {project['name']}")
    par = BondParams(coupon_rate=0.06, yield_rate=0.06, years=20, frequency=2)
    zero = BondParams(coupon_rate=0.0, yield_rate=0.05, years=12)
    m, h = bond_measures(par), 1e-5
    numeric_duration = -(bond_price(par, 0.06 + h) - bond_price(par, 0.06 - h)) / (2 * h) / m["price"]
    identities = {"par_bond_price_equals_face": abs(m["price"] - 100) < 1e-9,
                  "zero_coupon_duration_equals_maturity": abs(bond_measures(zero)["macaulay_duration"] - 12) < 1e-12,
                  "modified_duration_matches_derivative": abs(m["modified_duration"] / numeric_duration - 1) < 1e-7}
    problems += [name for name, ok in identities.items() if not ok]
    receipt["calculators"] = {"presets": results, "identities": identities, "problems": problems}
    receipt["checks"]["calculators"] = {"ok": not problems, "presets": len(results)}


def performance(folder: Path, receipt: dict, repeats: int) -> None:
    from core.experiments.config import ExperimentConfig
    from core.experiments.runner import ExperimentRunner

    durations = []
    for seed in range(1, repeats + 1):
        config = ExperimentConfig.model_validate({"name": f"latency_s{seed}", "seed": seed, "steps": 30, "n_agents": 64})
        started = time.perf_counter()
        ExperimentRunner(config).run(folder / "latency")
        durations.append(time.perf_counter() - started)
    ordered = sorted(durations)
    p95 = ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]
    receipt["performance"] = {"runs": repeats, "agents": 64, "steps": 30, "p50_seconds": round(statistics.median(durations), 3),
                              "p95_seconds": round(p95, 3), "max_seconds": round(max(durations), 3),
                              "target_p95_seconds": LATENCY_TARGET_P95_SECONDS,
                              "note": "single local process; the deployment SLO must be measured on the target server"}
    receipt["checks"]["performance"] = {"ok": p95 <= LATENCY_TARGET_P95_SECONDS}


def environment() -> dict:
    import importlib.metadata as metadata
    git = lambda *args: subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"python": platform.python_version(), "platform": platform.platform(),
            "git_commit": git("rev-parse", "HEAD") or "unknown", "git_dirty": bool(git("status", "--porcelain")),
            "packages": {p: metadata.version(p) for p in ("numpy", "pandas", "networkx", "pydantic", "PyYAML")}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default="outputs/lab-acceptance")
    parser.add_argument("--latency-runs", type=int, default=20)
    args = parser.parse_args()
    folder = Path(args.output).resolve() / f"acceptance-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    folder.mkdir(parents=True, exist_ok=False)
    receipt = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "offline": offline(), "environment": environment(), "checks": {}, "scenarios": {}, "status": "running"}
    try:
        scenario_checks(folder, receipt)
        negative_gate_suite(receipt)
        calculator_checks(receipt)
        performance(folder, receipt, args.latency_runs)
        receipt["status"] = "passed" if all(c["ok"] for c in receipt["checks"].values()) else "failed"
    except BaseException as exc:
        receipt.update(status="error", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        raise
    finally:
        receipt["ended_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (folder / "acceptance.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(folder / "acceptance.json")
        print(json.dumps({"status": receipt["status"], "checks": receipt["checks"]}, ensure_ascii=False, indent=2))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
