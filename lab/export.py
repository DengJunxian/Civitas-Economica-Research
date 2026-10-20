"""Printable teaching handouts as Markdown (plan 5.5 deliverables 1, 2, 5, 6).

Topic card and teacher guide come from the reviewed documents; the evidence
report reads only a comparison archive, so every number in it carries the
run_id it came from. Worksheets ask for an anonymous course number, never
personal identifiers.
"""
from __future__ import annotations

import math
from pathlib import Path

from lab.catalog import Catalog
from lab.compare import comparison_frames, factorial_interaction, group_summary, load_scenario_of, paired_differences
from lab.compiler import scenario_hash
from lab.observables import OBSERVABLES, OUTCOMES
from lab.service import load_manifest
from lab.specs import Level, LessonSpec, ScenarioSpec

LEVELS = {"undergraduate": "本科", "graduate": "硕士"}
REVIEW = {"draft": "草稿（未审核）", "pending_teacher_review": "待教师审核", "reviewed": "已审核"}
FOOTER = "> 仿真教学用途：结果是该模型、参数与种子条件下的输出，不是市场预测、现实政策因果结论或投资建议。"


def _num(value) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "—"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_num(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _bullets(items) -> str:
    return "\n".join(f"- {item}" for item in items)


def _courses(catalog: Catalog, codes) -> str:
    return "、".join(f"{c} {catalog.course(c)['name']}" for c in codes)


def _events(group) -> str:
    return "；".join(f"第 {e.step} 步 {e.kind}" + (f"={e.value:g}" if e.kind not in {"halt", "resume"} else "")
                    for e in group.events) or "无事件"


def scenario_card(spec: ScenarioSpec, catalog: Catalog) -> str:
    groups = [[g.id, g.label, _events(g), g.social.topology or spec.world.social.topology or "small_world"]
              for g in spec.comparison.groups]
    outcomes = [[m, OUTCOMES[m].label, OUTCOMES[m].definition] for m in (spec.outcome.primary, *spec.outcome.secondary)]
    return "\n\n".join([
        f"# 课题卡 · {spec.scenario_id} {spec.title}",
        f"版本 {spec.version} · scenario_hash `{scenario_hash(spec)[:16]}` · {REVIEW[spec.review.status]}",
        f"**研究问题**：{spec.question}",
        f"**课程**：{_courses(catalog, spec.course_codes)}　**层级**：{'、'.join(LEVELS[l] for l in spec.levels)}　**模块** {spec.module} · {spec.phase}",
        "## 教学目标\n\n" + _bullets(spec.objectives),
        "## 实验设计\n\n" + _table(["组", "名称", "事件", "社会网络"], groups)
        + f"\n\n人口 {spec.world.agents} · 步数 {spec.world.steps} · 初始价格 {spec.world.initial_price:g} · "
          f"决策 {spec.world.decision_mode} · 配对种子 {', '.join(map(str, spec.comparison.seeds))} · 参考组 {spec.comparison.reference}",
        "## 观测量\n\n" + _bullets(f"{OBSERVABLES[o].label}（{o}）" + (f"：{OBSERVABLES[o].note}" if OBSERVABLES[o].note else "")
                                for o in spec.observables),
        "## 预先指定的结果指标\n\n" + _table(["指标", "名称", "定义"], outcomes),
        "## 模型假设\n\n" + _bullets(spec.assumptions),
        "## 模型限制\n\n" + _bullets(spec.limitations),
        "## 必看证据\n\n" + _bullets(spec.evidence),
        FOOTER,
    ])


def teacher_guide(lesson: LessonSpec, catalog: Catalog) -> str:
    stages = [[s.name, f"{s.start_min}–{s.end_min} 分", s.teacher, s.student,
               f"{s.scenario_id} {'/'.join(s.groups)}".strip()] for s in lesson.stages]
    parts = [
        f"# 教师指导卡 · {lesson.lesson_id} {lesson.title}",
        f"版本 {lesson.version} · {lesson.duration_minutes} 分钟 · {REVIEW[lesson.review.status]}",
        f"**主课程**：{'、'.join(f'{c.code} {c.name}（{LEVELS[c.level]}）' for c in lesson.primary_courses)}",
        "## 教学目标\n\n" + _bullets(lesson.objectives),
        "## 课堂流程\n\n" + _table(["阶段", "时间", "教师活动", "学生活动与产出", "实验/组别"], stages),
    ]
    if lesson.guidance:
        g = lesson.guidance
        parts += [f"## 引导策略\n\n**课前预测**：{g.prediction}", "**操作顺序**\n\n" + _bullets(g.steps)]
        if g.probes:
            parts.append("**异常现象追问**\n\n" + _bullets(g.probes))
        if g.negative_results:
            parts.append(f"**负结果讨论**：{g.negative_results}")
    if lesson.level_tasks:
        parts.append("## 分层任务\n\n" + _table(["层级", "知识要求", "实验设计", "证据呈现", "产出"],
                                            [[LEVELS[t.level], t.knowledge, t.design, t.evidence, t.deliverable] for t in lesson.level_tasks]))
    parts += ["## 解释规范\n\n" + _bullets(lesson.interpretation_rules)] if lesson.interpretation_rules else []
    parts += [f"## 机制边界\n\n{lesson.mechanism_boundary}"] if lesson.mechanism_boundary else []
    for sid in lesson.scenarios:
        parts.append(f"## {sid} 模型限制\n\n" + _bullets(catalog.scenarios[sid].limitations))
    parts += ["## 评分量表\n\n" + _rubric(lesson), FOOTER]
    return "\n\n".join(parts)


def _rubric(lesson: LessonSpec) -> str:
    return _table(["评价项", "权重", "优秀（4）", "合格（2）", "不足（0–1）"],
                  [[r.dimension, f"{r.weight}%", r.excellent, r.passing, r.insufficient] for r in lesson.rubric]) + \
        "\n\n每项 0–4 级并按权重折算，满分 100。结果与预测不一致不扣分；AI 只提供评分建议，最终评分由教师负责。"


def worksheet(lesson: LessonSpec, catalog: Catalog, level: Level) -> str:
    task = lesson.task(level)
    questions = []
    for q in lesson.worksheet:
        tag = f"（{q.rubric_dimension}）" if q.rubric_dimension else ""
        questions.append(f"**{q.id}{tag}** {q.text}\n\n答：\n\n&nbsp;\n")
    parts = [
        f"# 学生工作纸 · {lesson.title}（{LEVELS[level]}）",
        "课程内匿名编号：________　小组：________　日期：________",
        "## 研究问题\n\n" + _bullets(f"{sid}：{catalog.scenarios[sid].question}" for sid in lesson.scenarios),
        "## 课前预测（运行前填写）\n\n" + (lesson.guidance.prediction if lesson.guidance else "写下你对结果的预测及理由。")
        + "\n\n我的预测：\n\n&nbsp;\n",
    ]
    if task:
        parts.append("## 本层级任务\n\n" + _table(["要求", "内容"], [["知识要求", task.knowledge], ["实验设计", task.design],
                                                            ["证据呈现", task.evidence], ["产出", task.deliverable]]))
    parts += [
        "## 问题\n\n" + "\n".join(questions),
        "## 证据记录（必须来自实际运行）\n\n" + _table(["run_id", "组/种子", "指标或记录", "数值", "说明"], [["", "", "", "", ""]] * 3),
        "## 模型限制（至少写一条）\n\n&nbsp;\n",
        "## 评分标准\n\n" + _rubric(lesson),
        FOOTER,
    ]
    return "\n\n".join(parts)


def evidence_report(root: str | Path, metrics: list[str] | None = None) -> str:
    root = Path(root)
    manifest, spec = load_manifest(root), load_scenario_of(root)
    metrics = metrics or [spec.outcome.primary, *spec.outcome.secondary]
    _, outcomes = comparison_frames(root)
    runs = [[r["group_id"], r["seed"], r.get("run_id", "—"), r["status"]] for r in manifest["runs"]]
    parts = [
        f"# 证据报告 · {spec.scenario_id} {spec.title}",
        _table(["项目", "内容"], [["comparison_id", manifest["comparison_id"]], ["场景版本", manifest["scenario_version"]],
                                ["scenario_hash", manifest["scenario_hash"]], ["来源", f"{manifest['output_origin']}（实际仿真运行）"],
                                ["审核状态", REVIEW[manifest["review_status"]]], ["确认教师", manifest["confirmed_by"]],
                                ["运行状态", manifest["status"]], ["创建时间", manifest["created_at"]]]),
        f"**研究问题**：{spec.question}",
        "## 实验组\n\n" + _table(["组", "名称", "事件"], [[g.id, g.label, _events(g)] for g in spec.comparison.groups])
        + f"\n\n参考组 {manifest['reference_group']}；配对种子 {', '.join(map(str, manifest['seeds']))}。",
        "## 运行清单\n\n" + _table(["组", "种子", "run_id", "状态"], runs),
    ]
    failed = [r for r in manifest["runs"] if r["status"] != "completed"]
    if failed:
        parts.append("**失败运行未纳入比较，也没有补造数据。**")
    if outcomes.empty:
        parts.append("没有完成的运行，无法比较。")
    for metric in ([] if outcomes.empty else metrics):
        section = [f"## {OUTCOMES[metric].label}（{metric}）", f"定义：{OUTCOMES[metric].definition}",
                   _table(["组", "n", "均值", "标准差", "最小", "最大"],
                          [[r["group_id"], r["n"], r["mean"], r["sd"], r["min"], r["max"]]
                           for r in group_summary(outcomes, metric).to_dict(orient="records")])]
        diffs = paired_differences(outcomes, metric, manifest["reference_group"])
        if not diffs.empty:
            section.append("配对差异（组 − 参考组，逐种子）：\n\n" + _table(
                ["组", "均值", "标准差", "正/负/零", "描述性区间", "逐种子"],
                [[d["group_id"], d["mean"], d["sd"], f"{d['positive']}/{d['negative']}/{d['zero']}",
                  f"[{_num(d['bootstrap_low'])}, {_num(d['bootstrap_high'])}]",
                  "；".join(f"{s}: {_num(v)}" for s, v in d["per_seed"].items())] for d in diffs.to_dict(orient="records")]))
        interaction = factorial_interaction(outcomes, metric, spec)
        if interaction and interaction.get("available"):
            section.append(f"2×2 交互差异 Δ = {interaction['formula']}：均值 {_num(interaction['mean'])}，逐种子 "
                           + "；".join(f"{s}: {_num(v)}" for s, v in interaction["per_seed"].items()))
        parts.append("\n\n".join(section))
    parts += [
        "## 模型假设\n\n" + _bullets(spec.assumptions),
        "## 模型限制\n\n" + _bullets(spec.limitations),
        "## 复核方法\n\n```sh\n" + f".venv/bin/python main.py lab verify {root}\n"
        + "".join(f".venv/bin/python main.py reproduce {root / r['path']}\n" for r in manifest["runs"][:2] if r["status"] == "completed")
        + "```",
        FOOTER,
    ]
    return "\n\n".join(parts)


def pilot_record(lesson: LessonSpec, catalog: Catalog) -> str:
    """Classroom pilot record (plan 9.2): timing, failures, outputs and teacher review (gate G3)."""
    stages = [[s.name, f"{s.end_min - s.start_min} 分", "", "", ""] for s in lesson.stages]
    return "\n\n".join([
        f"# 课堂试点记录表 · {lesson.lesson_id} {lesson.title}",
        f"课堂包版本 {lesson.version} · 计划 {lesson.duration_minutes} 分钟 · {REVIEW[lesson.review.status]}",
        _table(["项目", "记录"], [["课程与层级", ""], ["日期", ""], ["班级匿名编号", ""], ["参与学生数", ""],
                                ["授课教师", ""], ["观察员", ""], ["课前准备时间（分钟）", ""]]),
        "## 实验运行\n\n" + _table(["实验", "comparison_id", "运行成功/总数", "归档校验", "备注"],
                                  [[sid, "", "", "", ""] for sid in lesson.scenarios]),
        "## 课堂用时与中断\n\n" + _table(["阶段", "计划用时", "实际用时", "系统异常或中断", "处理方式"], stages),
        "## 学生产出统计（匿名汇总）\n\n" + _table(["指标", "人数/份数", "说明"], [
            ["提交课前预测", "", ""], ["选择了关键变量或干预", "", ""], ["引用实际运行证据（含 run_id）", "", ""],
            ["给出机制解释", "", ""], ["写出至少一条模型局限", "", ""], ["结果与预测不一致但能用证据解释", "", "不应扣分"]]),
        "## 教师评价\n\n" + _table(["维度", "评价（1–5）", "说明"], [
            ["与本课教学目标的匹配", "", ""], ["术语与难度是否合适", "", ""], ["图表与解释是否准确", "", ""],
            ["课堂时间是否可控", "", ""], ["再次使用的意愿", "", ""]]),
        "## 需要修改的内容\n\n&nbsp;\n",
        "## 审查记录\n\n" + _table(["审查人", "角色", "结论（通过/修改后通过/不通过）", "日期", "签字"], [["", "", "", "", ""]] * 2),
        "> 记录只汇总匿名计数，不填写学生姓名或学号；无法随机分组时，不作“平台导致成绩提升”的因果声明。",
    ])


def student_feedback(lesson: LessonSpec, catalog: Catalog) -> str:
    """Anonymous student form (plan 9.2, 10.3); concept test items are left to the course teacher."""
    likert = ["我清楚本课的研究问题", "我能把价格变化对应到具体的订单或成交", "我能区分模型结果与真实市场",
              "我能说出至少一条模型限制", "平台操作没有妨碍我理解内容", "课堂时间安排合适"]
    return "\n\n".join([
        f"# 学生反馈问卷（匿名） · {lesson.title}",
        "**告知**：本问卷用于改进课程与评估教学效果，只以匿名汇总形式呈现给授课教师和课程团队，"
        "不影响成绩。请不要填写姓名、学号或其他可识别信息。保留期限：________（由课程负责人填写）。",
        "课程内匿名编号：________　□ 课前　□ 课后",
        "## 概念测验\n\n由授课教师按本课教学目标编写；课前与课后使用同构但不同题面的题目，并包含至少一道迁移题。\n\n"
        + _bullets(f"目标 {i}：{objective}" for i, objective in enumerate(lesson.objectives, start=1)),
        "## 课堂体验（1 = 非常不同意，5 = 非常同意）\n\n" + _table(["陈述", "1", "2", "3", "4", "5"],
                                                         [[item, "□", "□", "□", "□", "□"] for item in likert]),
        "## 开放问题\n\n**本课中对你最有帮助的一条证据是什么？**\n\n&nbsp;\n\n**你仍然困惑的地方是什么？**\n\n&nbsp;\n",
        FOOTER,
    ])
