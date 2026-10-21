"""Anonymous classroom pilot summaries (plan v1.0 sections 9.2 and 10.3).

Input is one CSV row per anonymous student and phase (pre/post). Output is
descriptive only: distributions, paired pre/post differences for students who
answered both, and the feedback-form Likert items. Cells with fewer than
MIN_CELL respondents are suppressed so individuals cannot be singled out.
Without random assignment the report states that no causal claim about the
platform is made, and results that did not improve are reported the same way.
"""
from __future__ import annotations

import io
import math
import re

import pandas as pd

MIN_CELL = 5
REQUIRED = ("anon_id", "lesson_id", "phase")
SCORES = {"concept_score": "概念测验得分（0–100）", "transfer_score": "迁移题得分（0–100）"}
# Same order as lab.export.student_feedback.
LIKERT = {"likert_1": "我清楚本课的研究问题", "likert_2": "我能把价格变化对应到具体的订单或成交",
          "likert_3": "我能区分模型结果与真实市场", "likert_4": "我能说出至少一条模型限制",
          "likert_5": "平台操作没有妨碍我理解内容", "likert_6": "课堂时间安排合适"}
LOOKS_IDENTIFYING = re.compile(r"^\d{8,}$|@|^\d{17}[\dXx]$")


def template_csv() -> str:
    columns = [*REQUIRED, *SCORES, *LIKERT]
    example = ["S001", "LESSON-MKT-001", "pre", 60, 40, *[""] * len(LIKERT)]
    return pd.DataFrame([example], columns=columns).to_csv(index=False)


def load_responses(text: str) -> pd.DataFrame:
    frame = pd.read_csv(io.StringIO(text), dtype={"anon_id": str, "lesson_id": str, "phase": str})
    missing = [c for c in REQUIRED if c not in frame]
    if missing:
        raise ValueError(f"缺少必需列：{', '.join(missing)}")
    if frame["anon_id"].astype(str).str.contains(LOOKS_IDENTIFYING).any():
        raise ValueError("anon_id 中有疑似学号、身份证号或邮箱；请改用课程内匿名编号后再汇总")
    frame["phase"] = frame["phase"].str.strip().str.lower()
    if not frame["phase"].isin(["pre", "post"]).all():
        raise ValueError("phase 只能是 pre 或 post")
    for column in SCORES:
        if column in frame and not frame[column].dropna().between(0, 100).all():
            raise ValueError(f"{column} 须在 0–100 之间")
    for column in LIKERT:
        if column in frame and not frame[column].dropna().isin([1, 2, 3, 4, 5]).all():
            raise ValueError(f"{column} 须为 1–5 的整数")
    if frame.duplicated(["anon_id", "lesson_id", "phase"]).any():
        raise ValueError("同一 anon_id 在同一课堂包和阶段出现多行")
    return frame


def _describe(values: pd.Series) -> dict:
    values = values.dropna().astype(float)
    n = len(values)
    if n < MIN_CELL:
        return {"n": n, "suppressed": True}
    return {"n": n, "suppressed": False, "mean": values.mean(), "sd": values.std(ddof=1), "median": values.median(),
            "min": values.min(), "max": values.max()}


def summarize(frame: pd.DataFrame) -> dict:
    lessons = {}
    for lesson_id, rows in frame.groupby("lesson_id"):
        entry = {"respondents": {p: int((rows["phase"] == p).sum()) for p in ("pre", "post")}, "scores": {}, "likert": {}}
        for column in (c for c in SCORES if c in rows):
            entry["scores"][column] = {p: _describe(rows.loc[rows["phase"] == p, column]) for p in ("pre", "post")}
            paired = rows.pivot_table(index="anon_id", columns="phase", values=column).dropna()
            if {"pre", "post"} <= set(paired.columns) and len(paired) >= MIN_CELL:
                diff = paired["post"] - paired["pre"]
                entry["scores"][column]["paired"] = {
                    "n": len(diff), "suppressed": False, "mean_change": diff.mean(), "sd": diff.std(ddof=1),
                    "improved": int((diff > 0).sum()), "same": int((diff == 0).sum()), "worse": int((diff < 0).sum())}
            else:
                entry["scores"][column]["paired"] = {"n": len(paired), "suppressed": True}
        post = rows[rows["phase"] == "post"]
        for column in (c for c in LIKERT if c in rows):
            answers = post[column].dropna()
            entry["likert"][column] = ({"n": len(answers), "suppressed": True} if len(answers) < MIN_CELL else
                                       {"n": len(answers), "suppressed": False, "mean": answers.mean(),
                                        "distribution": {k: int((answers == k).sum()) for k in range(1, 6)}})
        lessons[lesson_id] = entry
    return {"min_cell": MIN_CELL, "lessons": lessons}


def _fmt(value) -> str:
    return "—" if value is None or (isinstance(value, float) and math.isnan(value)) else f"{value:.3g}"


def report_markdown(summary: dict) -> str:
    parts = ["# 课堂试点匿名汇总",
             f"只做描述统计；少于 {summary['min_cell']} 人的单元格不显示。没有随机分组时，本报告不作“平台导致成绩提升”的因果声明；"
             "成绩未提高或下降同样如实报告。"]
    for lesson_id, entry in summary["lessons"].items():
        parts.append(f"## {lesson_id}\n\n课前 {entry['respondents']['pre']} 份，课后 {entry['respondents']['post']} 份。")
        for column, stats in entry["scores"].items():
            rows = []
            for phase, label in (("pre", "课前"), ("post", "课后")):
                d = stats[phase]
                rows.append(f"| {label} | {d['n']} | " + ("（不足最小人数，未显示）| | | |" if d["suppressed"] else
                                                       f"{_fmt(d['mean'])} | {_fmt(d['sd'])} | {_fmt(d['median'])} | {_fmt(d['min'])}–{_fmt(d['max'])} |"))
            paired = stats["paired"]
            change = ("配对变化：不足最小人数，未显示。" if paired["suppressed"] else
                      f"配对变化（{paired['n']} 人同时有课前、课后）：平均 {_fmt(paired['mean_change'])}，标准差 {_fmt(paired['sd'])}；"
                      f"提高 {paired['improved']} 人，不变 {paired['same']} 人，下降 {paired['worse']} 人。")
            parts.append(f"### {SCORES[column]}\n\n| 阶段 | n | 均值 | 标准差 | 中位数 | 范围 |\n|---|---|---|---|---|---|\n"
                         + "\n".join(rows) + f"\n\n{change}")
        if entry["likert"]:
            lines = ["| 陈述（课后） | n | 均值 | 1 | 2 | 3 | 4 | 5 |", "|---|---|---|---|---|---|---|---|"]
            for column, d in entry["likert"].items():
                lines.append(f"| {LIKERT[column]} | {d['n']} | " + ("（不足最小人数）| | | | | |" if d["suppressed"] else
                             f"{_fmt(d['mean'])} | " + " | ".join(str(d["distribution"][k]) for k in range(1, 6)) + " |"))
            parts.append("### 课堂体验\n\n" + "\n".join(lines))
    return "\n\n".join(parts) + "\n"
