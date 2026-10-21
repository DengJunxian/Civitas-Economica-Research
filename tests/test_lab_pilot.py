import pandas as pd
import pytest

from lab.pilot import MIN_CELL, load_responses, report_markdown, summarize, template_csv


def responses(n_pairs=6, extra_pre=0) -> str:
    rows = []
    for i in range(n_pairs):
        rows.append({"anon_id": f"S{i:03d}", "lesson_id": "LESSON-MKT-001", "phase": "pre", "concept_score": 50 + i, "transfer_score": 40})
        rows.append({"anon_id": f"S{i:03d}", "lesson_id": "LESSON-MKT-001", "phase": "post", "concept_score": 50 + i + (i % 3) - 1,
                     "transfer_score": 45, "likert_1": 4, "likert_3": 5})
    for i in range(extra_pre):
        rows.append({"anon_id": f"X{i:03d}", "lesson_id": "LESSON-MKT-001", "phase": "pre", "concept_score": 70, "transfer_score": 50})
    return pd.DataFrame(rows).to_csv(index=False)


def test_paired_changes_report_improved_same_and_worse():
    summary = summarize(load_responses(responses(n_pairs=6, extra_pre=2)))
    lesson = summary["lessons"]["LESSON-MKT-001"]
    assert lesson["respondents"] == {"pre": 8, "post": 6}
    paired = lesson["scores"]["concept_score"]["paired"]
    # change = (i % 3) - 1 for i = 0..5 -> -1, 0, 1, -1, 0, 1
    assert (paired["n"], paired["improved"], paired["same"], paired["worse"]) == (6, 2, 2, 2)
    assert paired["mean_change"] == pytest.approx(0.0)
    assert lesson["likert"]["likert_1"]["distribution"][4] == 6
    report = report_markdown(summary)
    assert "不作“平台导致成绩提升”的因果声明" in report and "下降 2 人" in report


def test_small_cells_are_suppressed():
    summary = summarize(load_responses(responses(n_pairs=MIN_CELL - 1)))
    scores = summary["lessons"]["LESSON-MKT-001"]["scores"]["concept_score"]
    assert scores["pre"]["suppressed"] and scores["paired"]["suppressed"]
    assert "未显示" in report_markdown(summary)


@pytest.mark.parametrize("bad, message", [
    ({"anon_id": "2026123456"}, "学号"), ({"anon_id": "a@b.edu"}, "学号"), ({"phase": "middle"}, "phase"),
    ({"concept_score": 120}, "0–100"), ({"likert_1": 7}, "1–5"),
])
def test_identifiers_and_ranges_are_checked(bad, message):
    row = {"anon_id": "S1", "lesson_id": "L", "phase": "pre", "concept_score": 50, "likert_1": 3, **bad}
    with pytest.raises(ValueError, match=message):
        load_responses(pd.DataFrame([row]).to_csv(index=False))


def test_template_parses():
    assert len(load_responses(template_csv())) == 1
