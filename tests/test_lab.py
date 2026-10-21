import json

import numpy as np
import pandas as pd
import pytest

from core.experiments.verify import verify_run
from lab.capability import check_scenario
from lab.catalog import load_catalog
from lab.compare import comparison_frames, evidence_card, factorial_interaction, paired_differences
from lab.compiler import compile_scenario
from lab.observables import step_frame
from lab.service import ConfirmationRequired, ScenarioRejected, load_manifest, run_scenario, verify_comparison
from lab.specs import LessonSpec, ScenarioSpec

P1_MUST = ("EXP-01", "EXP-02", "EXP-05", "EXP-06", "EXP-07", "EXP-08")


def spec(**overrides) -> ScenarioSpec:
    payload = {
        "scenario_id": "EXP-99", "version": "0.1.0", "title": "test", "module": "E1", "phase": "P1",
        "course_codes": ["107349"], "question": "q", "objectives": ["o"], "limitations": ["l"],
        "world": {"agents": 12, "steps": 6},
        "comparison": {"seeds": [3, 4], "groups": [
            {"id": "A", "label": "base"},
            {"id": "B", "label": "panic", "events": [{"step": 2, "kind": "panic", "value": 3}]}]},
        "observables": ["price"], "outcome": {"primary": "cumulative_volume"},
    }
    for key, value in overrides.items():
        target = payload
        *parents, leaf = key.split("__")
        for parent in parents:
            target = target[parent]
        target[leaf] = value
    return ScenarioSpec.model_validate(payload)


def codes(report):
    return {i.code for i in report.issues}


def test_catalog_cross_references_and_p1_scenarios_pass_gate():
    catalog = load_catalog()
    assert set(P1_MUST) <= set(catalog.scenarios)
    for sid, scenario in catalog.scenarios.items():
        assert check_scenario(scenario).ok, sid
        assert scenario.source_policy.required_origin == "simulation"
        assert scenario.limitations and scenario.question
    runnable = {e["id"] for e in catalog.scenarios_for_course("107349") if e["runnable"]}
    assert {"EXP-01", "EXP-02", "EXP-07", "EXP-08"} <= runnable
    lesson = catalog.lessons["LESSON-MKT-001"]
    assert lesson.duration_minutes == 45 and sum(r.weight for r in lesson.rubric) == 100


def test_lesson_plan_must_cover_declared_duration():
    lesson = load_catalog().lessons["LESSON-MKT-001"].model_dump()
    lesson["duration_minutes"] = 50
    with pytest.raises(ValueError, match="cover 45 minutes"):
        LessonSpec.model_validate(lesson)


def test_compiler_keeps_teaching_metadata_out_and_orders_events():
    s = spec(comparison__groups=[{"id": "A", "label": "a", "social": {"topology": "ring"},
                                  "events": [{"step": 4, "kind": "halt"}, {"step": 4, "kind": "panic", "value": 2},
                                             {"step": 5, "kind": "resume"}, {"step": 4, "kind": "tax_change", "value": 0.001}]}])
    compiled = compile_scenario(s)
    assert [(c.group_id, c.seed) for c in compiled] == [("A", 3), ("A", 4)]
    config = compiled[0].config
    assert config.social.topology == "ring" and config.mode == "research"
    assert [e.kind for e in config.events] == ["tax_change", "panic", "halt", "resume"]
    dumped = json.dumps(config.model_dump(mode="json"), ensure_ascii=False)
    assert all(word not in dumped for word in ("question", "objectives", "observables", "approval", "limitations"))


@pytest.mark.parametrize("overrides, code", [
    ({"world__type": "bank_ledger"}, "UNSUPPORTED_WORLD"),
    ({"world__type": "galaxy"}, "UNKNOWN_WORLD"),
    ({"world__decision_mode": "llm"}, "UNSUPPORTED_DECISION"),
    ({"world__decision_mode": "mock"}, "UNSUPPORTED_DECISION"),
    ({"world__social": {"topology": "scale_free"}}, "UNKNOWN_TOPOLOGY"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "rrr_cut", "value": 0.5}]}]}, "UNSUPPORTED_MECHANISM"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "rumor_text"}]}]}, "UNSUPPORTED_MECHANISM"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "moonshot"}]}]}, "UNKNOWN_EVENT"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 9, "kind": "panic", "value": 1}]}]}, "STEP_OUT_OF_RANGE"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "panic", "value": 1.5}]}]}, "VALUE_OUT_OF_RANGE"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "panic", "value": 13}]}]}, "VALUE_OUT_OF_RANGE"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "tax_change", "value": 0.2}]}]}, "VALUE_OUT_OF_RANGE"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "resume"}]}]}, "UNPAIRED_RESUME"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "halt"}, {"step": 3, "kind": "halt"}]}]}, "DUPLICATE_HALT"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "halt"}, {"step": 3, "kind": "resume"}, {"step": 3, "kind": "halt"}]}]}, "HALT_RESUME_SAME_STEP"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "tax_change", "value": 0.001}, {"step": 2, "kind": "tax_change", "value": 0.002}]}]}, "DUPLICATE_EVENT"),
    ({"comparison__groups": [{"id": "A", "label": "a"}, {"id": "A", "label": "b"}]}, "DUPLICATE_GROUP"),
    ({"comparison__reference_group": "Z"}, "UNKNOWN_GROUP"),
    ({"comparison__seeds": [3, 3]}, "DUPLICATE_SEED"),
    ({"comparison__seeds": list(range(30))}, "BUDGET"),
    ({"comparison__factorial": {"factor_a": "x", "factor_b": "y", "neither": "A", "a_only": "B", "b_only": "B", "both": "A"}}, "INVALID_FACTORIAL"),
    ({"observables": ["true_market_value"]}, "UNKNOWN_OBSERVABLE"),
    ({"outcome": {"primary": "alpha"}}, "UNKNOWN_OUTCOME"),
    ({"source_policy": {"required_origin": "teaching_example"}}, "SOURCE_POLICY"),
    ({"source_policy": {"allow_teaching_example_as_run": True}}, "SOURCE_POLICY"),
    ({"approval": {"require_teacher_confirmation": False}}, "APPROVAL_REQUIRED"),
    ({"world__social": {"neighbors": 3}}, "CORE_SCHEMA"),
])
def test_gate_rejects_with_explanation(overrides, code):
    report = check_scenario(spec(**overrides))
    assert report.status == "rejected"
    assert code in codes(report)
    assert all(i.message for i in report.issues)


def test_gate_names_the_missing_engine():
    report = check_scenario(spec(comparison__groups=[{"id": "A", "label": "a", "events": [{"step": 2, "kind": "rrr_cut"}]}]))
    message = next(i.message for i in report.issues if i.code == "UNSUPPORTED_MECHANISM")
    assert "银行账本模型" in message and "P3" in message


@pytest.mark.parametrize("overrides, code", [
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "halt"}]}]}, "HALT_UNTIL_END"),
    ({"comparison__groups": [{"id": "A", "label": "a"}]}, "NO_CONTROL"),
    ({"comparison__groups": [{"id": "A", "label": "a"}, {"id": "B", "label": "b"}]}, "IDENTICAL_GROUPS"),
    ({"comparison__seeds": [3]}, "SINGLE_SEED"),
    ({"comparison__groups": [{"id": "A", "label": "a", "events": [{"step": 2, "kind": "tax_change", "value": 0.05}]}]}, "UNUSUAL_VALUE"),
    ({"world__market_regime": "bull"}, "REGIME_LIMITED_EFFECT"),
])
def test_gate_warnings_do_not_block(overrides, code):
    report = check_scenario(spec(**overrides))
    assert report.status == "supported_with_warnings" and code in codes(report)


def test_run_requires_gate_and_teacher_confirmation(tmp_path):
    with pytest.raises(ScenarioRejected):
        run_scenario(spec(world__type="macro_closure"), confirmed_by="t", output_root=tmp_path)
    with pytest.raises(ConfirmationRequired):
        run_scenario(spec(), confirmed_by="  ", output_root=tmp_path)
    assert not any(tmp_path.iterdir())


def test_matched_seed_comparison_is_archived_and_verifiable(tmp_path):
    root = run_scenario(spec(), confirmed_by="teacher", output_root=tmp_path)
    manifest = load_manifest(root)
    assert manifest["status"] == "completed" and manifest["output_origin"] == "simulation"
    assert [(r["group_id"], r["seed"], r["status"]) for r in manifest["runs"]] == [
        ("A", 3, "completed"), ("B", 3, "completed"), ("A", 4, "completed"), ("B", 4, "completed")]
    assert verify_comparison(root)["status"] == "verified"
    for run in manifest["runs"]:
        assert verify_run(root / run["path"])["status"] == "verified"
        assert "question" not in (root / run["path"] / "config.json").read_text(encoding="utf-8")

    steps, outcomes = comparison_frames(root)
    first = outcomes[outcomes.group_id == "A"].sort_values("seed")
    # Matched seeds share the initial world: identical paths before the step-2 shock.
    paired = steps[steps.step < 2].pivot_table(index="seed", columns="group_id", values="price")
    assert (paired["A"] == paired["B"]).all()
    card = evidence_card(root)
    assert card["available"] and card["metric"] == "cumulative_volume"
    assert {v["run_id"] for v in card["values"]} == {r["run_id"] for r in manifest["runs"]}
    assert card["paired_differences"][0]["n"] == 2 and len(first) == 2

    run_dir = root / manifest["runs"][0]["path"]
    with (run_dir / "metrics.csv").open("a") as f:
        f.write("tampered\n")
    assert verify_comparison(root)["status"] == "not_verified"


def test_step_frame_marks_undefined_spread_and_halt(tmp_path):
    s = spec(comparison__groups=[{"id": "H", "label": "halt", "events": [{"step": 2, "kind": "halt"}, {"step": 4, "kind": "resume"}]}],
             comparison__seeds=[5])
    root = run_scenario(s, confirmed_by="teacher", output_root=tmp_path)
    frame = step_frame(root / load_manifest(root)["runs"][0]["path"])
    halted = frame[frame.halted]
    assert list(halted.step) == [2, 3] and (halted.volume == 0).all() and (halted.orders_submitted == 0).all()
    assert frame.loc[frame.spread_defined == False, "spread"].isna().all()  # noqa: E712
    assert (frame[["buy_decisions", "sell_decisions", "hold_decisions"]].sum(axis=1) == 12).all()


def test_paired_differences_and_interaction_are_per_seed():
    rows = [("A", 1, 10), ("B", 1, 13), ("C", 1, 11), ("D", 1, 20),
            ("A", 2, 12), ("B", 2, 12), ("C", 2, 12), ("D", 2, 15),
            ("A", 3, 9), ("B", 3, 10), ("C", 3, 9), ("D", 3, 9)]
    outcomes = pd.DataFrame(rows, columns=["group_id", "seed", "y"])
    diffs = paired_differences(outcomes, "y", "A").set_index("group_id")
    assert diffs.loc["B", "per_seed"] == {1: 3.0, 2: 0.0, 3: 1.0}
    assert (diffs.loc["B", "positive"], diffs.loc["B", "zero"]) == (2, 1)
    s = spec(comparison__groups=[{"id": g, "label": g, "events": [{"step": 2, "kind": "panic", "value": i}]}
                                 for i, g in enumerate("ABCD")],
             comparison__factorial={"factor_a": "p", "factor_b": "t", "neither": "A", "a_only": "B", "b_only": "C", "both": "D"})
    result = factorial_interaction(outcomes, "y", s)
    # [(D-B) - (C-A)] per seed: [(20-13)-(11-10), (15-12)-(12-12), (9-10)-(9-9)]
    assert result["per_seed"] == {1: 6.0, 2: 3.0, 3: -1.0}
    assert result["mean"] == pytest.approx(np.mean([6, 3, -1]))


def test_lab_cli(tmp_path, capsys):
    from main import main
    assert main(["lab", "check", "EXP-05"]) == 0
    assert main(["lab", "catalog", "--course", "107349"]) == 0
    assert main(["lab", "run", "EXP-01"]) == 1  # no teacher confirmation
    assert main(["lab", "check", "EXP-404"]) == 1
    capsys.readouterr()
    assert main(["lab", "run", "EXP-01", "--confirm", "teacher", "--output", str(tmp_path)]) == 0
    root = capsys.readouterr().out.strip()
    assert main(["lab", "verify", root]) == 0


def test_lab_ui_gate_confirmation_run_and_comparison(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import ui.lab as lab_ui
    real_run, real_list = lab_ui.run_scenario, lab_ui.list_comparisons
    monkeypatch.setattr(lab_ui, "run_scenario", lambda s, **kw: real_run(s, **{**kw, "output_root": tmp_path}))
    monkeypatch.setattr(lab_ui, "list_comparisons", lambda root: real_list(tmp_path))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
    assert not app.exception and app.dataframe  # course center lists courses
    next(s for s in app.selectbox if s.label == "查看课程").set_value("107349").run(timeout=30)
    assert not app.exception and len(app.get("download_button")) >= 6  # guide + two worksheets per lesson pack
    app.sidebar.radio[1].set_value("PDF").run(timeout=60)  # handout format switch
    assert not app.exception
    app.sidebar.radio[0].set_value("场景实验室").run(timeout=30)
    assert not app.exception
    run_button = next(b for b in app.button if b.label == "运行对照实验")
    assert run_button.disabled  # no teacher confirmation yet
    next(t for t in app.text_input if t.label == "确认教师").set_value("teacher")
    app.checkbox[0].check()
    app.run(timeout=30)
    next(b for b in app.button if b.label == "运行对照实验").click().run(timeout=60)
    assert not app.exception and app.success
    assert load_manifest(real_list(tmp_path)[0])["confirmed_by"] == "teacher"
    app.sidebar.radio[0].set_value("对照分析").run(timeout=30)
    assert not app.exception and not app.error


def test_handout_pdf_and_pilot_forms():
    pytest.importorskip("reportlab")
    from lab.export import pilot_record, student_feedback, worksheet
    from lab.pdf import SUBSTITUTES, markdown_pdf
    catalog = load_catalog()
    lesson = catalog.lessons["LESSON-MKT-001"]
    feedback = student_feedback(lesson, catalog)
    assert "匿名" in feedback and "保留期限" in feedback and "学号" in feedback  # tells students not to identify themselves
    record = pilot_record(lesson, catalog)
    assert all(stage.name in record for stage in lesson.stages) and "审查记录" in record
    sample = "# 标题 · 测试\n\n- 要点 −1\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```sh\nmain.py lab verify x\n```\n\n&nbsp;\n"
    for text in (sample, worksheet(lesson, catalog, "graduate"), record, feedback):
        assert markdown_pdf(text).startswith(b"%PDF")
    assert "·−".translate(SUBSTITUTES) == "｜-"


def test_calculator_lab_page():
    pytest.importorskip("streamlit")
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run(timeout=30)
    app.sidebar.radio[0].set_value("计算实验室").run(timeout=30)
    assert not app.exception and any("不是多智能体仿真" in i.value for i in app.info)
    next(s for s in app.selectbox if s.label == "计算实验").set_value("EXP-17").run(timeout=30)
    assert [m.value for m in app.metric][0] == "100.0000"  # default 10-year par bond
    next(s for s in app.selectbox if s.label == "计算实验").set_value("EXP-15").run(timeout=30)
    next(s for s in app.selectbox if s.label == "预设").set_value("现金流两次变号：两个 IRR").run(timeout=30)
    assert not app.exception and any("IRR 规则不适用" in w.value for w in app.warning)
