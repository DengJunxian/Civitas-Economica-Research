"""Regression guards for teaching content (plan 5.5 items 7 and 8).

1. Versioning: changing a scenario or lesson document without bumping its
   version fails. Refresh the record with CIVITAS_UPDATE_GOLDEN=1 after a bump.
2. Claims: lesson packs state facts about model behaviour (identical paths
   before a shock, zero trading during halts, zero volume response to the tax
   cut...). If the engine changes and a claim stops holding, the handout is
   wrong and this test points at it.
"""
import json
import os
from pathlib import Path

import pytest

from core.reproducibility import stable_hash
from lab.catalog import load_catalog
from lab.compare import comparison_frames
from lab.compiler import scenario_hash
from lab.service import run_scenario

GOLDEN = Path(__file__).with_name("golden") / "lab_content_versions.json"


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def _current() -> dict:
    catalog = load_catalog()
    return {**{sid: {"version": s.version, "hash": scenario_hash(s)} for sid, s in catalog.scenarios.items()},
            **{lid: {"version": l.version, "hash": stable_hash(l.model_dump(mode="json"))} for lid, l in catalog.lessons.items()}}


def test_content_changes_require_version_bump():
    current = _current()
    recorded = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    problems = []
    for key, now in current.items():
        old = recorded.get(key)
        if old and now["hash"] != old["hash"] and _version(now["version"]) <= _version(old["version"]):
            problems.append(f"{key}: content changed but version {now['version']} was not bumped")
        elif old and now["hash"] == old["hash"] and now["version"] != old["version"]:
            problems.append(f"{key}: version changed without content change")
    assert not problems, problems
    if os.environ.get("CIVITAS_UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    stale = sorted(set(current) ^ set(recorded)) + sorted(k for k in current if k in recorded and current[k] != recorded[k])
    assert not stale, f"content version record is stale for {stale}; rerun with CIVITAS_UPDATE_GOLDEN=1"


def _run(tmp_path, scenario_id, seed=41):
    spec = load_catalog().scenarios[scenario_id]
    spec = spec.model_copy(update={"comparison": spec.comparison.model_copy(update={"seeds": (seed,)})})
    steps, _ = comparison_frames(run_scenario(spec, confirmed_by="regression", output_root=tmp_path))
    return {g: frame.set_index("step") for g, frame in steps.groupby("group_id")}


@pytest.mark.parametrize("scenario_id, reference, other, first_event", [
    ("EXP-05", "A", "B", 8), ("EXP-07", "B", "C", 15), ("EXP-08", "A", "T", 20)])
def test_matched_arms_are_identical_before_first_difference(tmp_path, scenario_id, reference, other, first_event):
    groups = _run(tmp_path, scenario_id)
    before = slice(1, first_event - 1)
    for column in ("price", "volume", "sell_decisions", "fees"):
        assert groups[reference].loc[before, column].equals(groups[other].loc[before, column]), column


def test_lesson_claims_about_halt_still_hold(tmp_path):
    halted = _run(tmp_path, "EXP-07")["C"].loc[15:17]
    assert halted["halted"].all() and (halted["volume"] == 0).all() and (halted["orders_submitted"] == 0).all()
    assert (halted["sell_decisions"] > 0).any()        # LESSON-MKT-004 Q2: intentions continue during the halt
    assert (halted["orders_cancelled_halt"] == 0).all()  # LESSON-MKT-004: TTL already cancelled resting orders


def test_lesson_claims_about_tax_cut_still_hold(tmp_path):
    groups = _run(tmp_path, "EXP-08")
    # LESSON-MKT-005 / EXP-08 limitations: fees fall, trading does not respond.
    assert groups["T"]["fees"].iloc[-1] < groups["A"]["fees"].iloc[-1]
    assert groups["T"]["volume"].equals(groups["A"]["volume"])


def test_lesson_claims_about_weak_contagion_still_hold(tmp_path):
    groups = _run(tmp_path, "EXP-06")
    # LESSON-BEH-002 / baseline audit F2: panic barely spreads beyond the 8 seeds by default.
    assert all(frame["infected"].max() <= 12 for frame in groups.values())
    assert groups["EM"]["infected"].max() == 8


def test_lesson_claim_about_pairing_still_holds(tmp_path):
    # LESSON-EMP-001: matched-seed differences vary far less than outcomes across seeds.
    from lab.compare import group_summary, paired_differences
    spec = load_catalog().scenarios["EXP-10"]
    spec = spec.model_copy(update={"comparison": spec.comparison.model_copy(update={"seeds": (41, 42, 43, 44)})})
    _, outcomes = comparison_frames(run_scenario(spec, confirmed_by="regression", output_root=tmp_path))
    spread = group_summary(outcomes, "cumulative_volume").set_index("group_id")["sd"].min()
    assert paired_differences(outcomes, "cumulative_volume", "A").iloc[0]["sd"] < spread / 5
