"""Civitas Lab teaching workspace: course center, scenario lab, comparison.

Thin Streamlit layer over lab.*; it never computes market values itself. Every
chart reads run archives through lab.observables, and every run goes through
the capability gate and an explicit teacher confirmation, including scenarios
drafted on the AI teaching desk.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from lab.ai import ai_usage, draft_scenario, explain_comparison, load_draft_yaml
from lab.capability import (OTHER_UNSUPPORTED_EVENTS, PLANNED_EVENTS, SUPPORTED_EVENTS, SUPPORTED_TOPOLOGIES,
                            CapabilityReport, check_scenario)
from lab.catalog import load_catalog
from lab.compare import comparison_frames, evidence_card, factorial_interaction, group_summary, load_scenario_of, paired_differences
from lab.export import evidence_report, scenario_card, teacher_guide, worksheet
from lab.observables import OBSERVABLES, OUTCOMES, STEP_COLUMNS
from lab.service import list_comparisons, load_manifest, run_scenario, verify_comparison
from lab.specs import ScenarioSpec
from ui.chart_theme import apply_dark_theme

PROGRAMS = {"undergraduate": "本科", "graduate": "硕士"}
REVIEW = {"draft": "草稿（已修改，未审核）", "pending_teacher_review": "待教师审核", "reviewed": "已审核"}
EVENT_CHOICES = list(SUPPORTED_EVENTS) + sorted(PLANNED_EVENTS) + sorted(OTHER_UNSUPPORTED_EVENTS)
SEVERITY = {"error": "拒绝", "warning": "提示"}
GROUP_COLORS = ("#60a5fa", "#f59e0b", "#34d399", "#f472b6")


def _rgba(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def _goto(page: str, **state):
    """Button callback: switch the sidebar page before the next script run."""
    def go():
        st.session_state.update(state, lab_page=page)
    return go


def _limitations(spec: ScenarioSpec) -> None:
    st.warning("**模型限制**\n\n" + "\n".join(f"- {item}" for item in spec.limitations))


def _gate(report: CapabilityReport) -> None:
    status = {"supported": st.success, "supported_with_warnings": st.warning, "rejected": st.error}[report.status]
    status(f"能力门禁：{ {'supported': '支持', 'supported_with_warnings': '支持（有提示）', 'rejected': '拒绝'}[report.status]}"
           f" · {report.run_count} 次运行 · {report.agent_steps} agent-steps")
    if report.issues:
        st.dataframe(pd.DataFrame([{"结果": SEVERITY[i.severity], "代码": i.code, "位置": i.path, "说明": i.message}
                                   for i in report.issues]), hide_index=True, use_container_width=True)


def _run_confirmed(spec: ScenarioSpec, teacher: str, output_root: Path) -> None:
    st.session_state["lab_teacher"] = teacher
    bar = st.progress(0.0, "准备运行")
    root = run_scenario(spec, confirmed_by=teacher, output_root=output_root,
                        on_progress=lambda i, n, e: bar.progress(i / n, f"{i}/{n} 组 {e['group_id']} 种子 {e['seed']}：{e['status']}"))
    manifest = load_manifest(root)
    failed = [r for r in manifest["runs"] if r["status"] != "completed"]
    (st.error if failed else st.success)(f"{manifest['comparison_id']}：{manifest['status']}，失败 {len(failed)} 次")
    st.session_state["lab_comparison"] = str(root)


def render_course_center() -> None:
    catalog = load_catalog()
    st.caption("课程知识点与实验映射为拟议设计，须由授课教师逐项核对；课程名称与代码来自培养计划。")
    left, right = st.columns([2, 1])
    query = left.text_input("检索课程、代码或知识点", "")
    programs = right.multiselect("层级", list(PROGRAMS), default=list(PROGRAMS), format_func=PROGRAMS.get)
    courses = [c for c in catalog.search(query) if c["program"] in programs]
    rows = [{"代码": c["code"], "课程": c["name"], "层级": PROGRAMS[c["program"]], "类别": c["category"],
             "优先级": c["priority"], "知识点": "；".join(c["knowledge_points"]),
             "可运行实验": sum(e["runnable"] for e in catalog.scenarios_for_course(c["code"])),
             "关联实验": len(c["scenarios"])} for c in courses]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if not courses:
        return
    code = st.selectbox("查看课程", [c["code"] for c in courses],
                        format_func=lambda x: f"{x} {catalog.course(x)['name']}（{PROGRAMS[catalog.course(x)['program']]}）")
    course = catalog.course(code)
    st.subheader(f"{course['code']} {course['name']}")
    st.write("拟选知识点：" + "；".join(course["knowledge_points"]) + "（待教师确认）")
    if course.get("cross_cutting"):
        st.info("贯穿性课程：通过各实验的来源标签、模型限制与 AI 解释责任体现，不对应单独的交易场景。")
    for entry in catalog.scenarios_for_course(code):
        cols = st.columns([1, 4, 1, 2])
        cols[0].markdown(f"**{entry['id']}**")
        cols[1].write(f"{entry['title']}（{entry['module']}）")
        cols[2].write(entry["phase"])
        if entry["runnable"]:
            cols[3].button("在场景实验室打开", key=f"open-{code}-{entry['id']}",
                           on_click=_goto("场景实验室", lab_scenario=entry["id"]))
        else:
            cols[3].caption(f"未开放：需要 {entry['status']}（{entry['engine']}）")
    for lesson in catalog.lessons_for_course(code):
        with st.expander(f"课堂包 {lesson.lesson_id}：{lesson.title}（{lesson.duration_minutes} 分钟，{REVIEW[lesson.review.status]}）"):
            st.write("教学目标：\n" + "\n".join(f"- {o}" for o in lesson.objectives))
            st.dataframe(pd.DataFrame([{"阶段": s.name, "时间": f"{s.start_min}–{s.end_min} 分", "教师活动": s.teacher,
                                        "学生活动与产出": s.student, "实验": s.scenario_id, "组别": "、".join(s.groups)}
                                       for s in lesson.stages]), hide_index=True, use_container_width=True)
            st.markdown("**课堂工作纸**\n\n" + "\n".join(f"- {q.id}：{q.text}" for q in lesson.worksheet))
            st.dataframe(pd.DataFrame([{"评价项": r.dimension, "权重": f"{r.weight}%", "优秀（4）": r.excellent,
                                        "合格（2）": r.passing, "不足（0–1）": r.insufficient} for r in lesson.rubric]),
                         hide_index=True, use_container_width=True)
            st.caption("机制边界：" + lesson.mechanism_boundary)
            st.markdown("**解释规范**\n\n" + "\n".join(f"- {r}" for r in lesson.interpretation_rules))
            downloads = st.columns(3)
            downloads[0].download_button("教师指导卡", teacher_guide(lesson, catalog), f"{lesson.lesson_id}-guide.md",
                                         key=f"guide-{code}-{lesson.lesson_id}")
            for column, level in zip(downloads[1:], PROGRAMS):
                column.download_button(f"{PROGRAMS[level]}工作纸", worksheet(lesson, catalog, level),
                                       f"{lesson.lesson_id}-worksheet-{level}.md", key=f"ws-{code}-{lesson.lesson_id}-{level}")


def _edited_spec(base: ScenarioSpec) -> ScenarioSpec:
    payload = base.model_dump(mode="json")
    c1, c2, c3 = st.columns(3)
    payload["world"]["agents"] = int(c1.number_input("投资者人数", 2, 2000, base.world.agents, step=2))
    payload["world"]["steps"] = int(c2.number_input("时间步数", 1, 500, base.world.steps))
    seeds = c3.text_input("配对种子（逗号分隔）", ",".join(map(str, base.comparison.seeds)))
    payload["comparison"]["seeds"] = [int(s) for s in seeds.replace("，", ",").split(",") if s.strip().lstrip("-").isdigit()]
    st.markdown("**实验组与事件时间线**（同一步事件按固定优先级执行：复牌 → 税率 → 情绪 → 恐慌 → 停牌）")
    for index, group in enumerate(base.comparison.groups):
        st.markdown(f"组 **{group.id}**：{group.label}")
        topology = group.social.topology or base.world.social.topology or "small_world"
        chosen = st.selectbox("社会网络", SUPPORTED_TOPOLOGIES, index=SUPPORTED_TOPOLOGIES.index(topology),
                              key=f"topo-{base.scenario_id}-{group.id}")
        if chosen != (base.world.social.topology or "small_world") or group.social.topology:
            payload["comparison"]["groups"][index]["social"]["topology"] = chosen
        frame = pd.DataFrame([e.model_dump() for e in group.events], columns=["step", "kind", "value", "note"])
        edited = st.data_editor(frame, num_rows="dynamic", use_container_width=True, key=f"events-{base.scenario_id}-{group.id}",
                                column_config={"step": st.column_config.NumberColumn("步", min_value=1, step=1),
                                               "kind": st.column_config.SelectboxColumn("事件", options=EVENT_CHOICES),
                                               "value": st.column_config.NumberColumn("数值", format="%.5f"),
                                               "note": st.column_config.TextColumn("教学注释")})
        payload["comparison"]["groups"][index]["events"] = [
            {"step": int(r.step), "kind": r.kind, "value": 0.0 if pd.isna(r.value) else float(r.value),
             "note": "" if pd.isna(r.note) else str(r.note)}
            for r in edited.itertuples() if pd.notna(r.step) and isinstance(r.kind, str) and r.kind]
    if payload != base.model_dump(mode="json"):
        payload["review"] = {**payload["review"], "status": "draft"}
    return ScenarioSpec.model_validate(payload)


def render_scenario_lab(output_root: Path) -> None:
    catalog = load_catalog()
    ids = list(catalog.scenarios)
    default = st.session_state.get("lab_scenario", ids[0])
    sid = st.selectbox("标准实验", ids, index=ids.index(default) if default in ids else 0,
                       format_func=lambda x: f"{x} {catalog.scenarios[x].title}")
    base = catalog.scenarios[sid]
    st.subheader(base.title)
    st.markdown(f"**研究问题**：{base.question}")
    meta = st.columns(3)
    meta[0].write("课程：" + "、".join(f"{c} {catalog.course(c)['name']}" for c in base.course_codes))
    meta[1].write(f"模块 {base.module} · {base.phase} · v{base.version}")
    meta[2].write(f"审核状态：{REVIEW[base.review.status]}")
    with st.expander("课题卡：教学目标、模型假设与必看证据", expanded=True):
        st.markdown("**教学目标**\n\n" + "\n".join(f"- {o}" for o in base.objectives))
        st.markdown("**模型假设**\n\n" + "\n".join(f"- {a}" for a in base.assumptions))
        st.markdown("**必看证据**\n\n" + "\n".join(f"- {e}" for e in base.evidence))
    _limitations(base)

    try:
        spec = _edited_spec(base)
    except ValueError as exc:
        st.error(f"场景结构无效：{exc}")
        return
    report = check_scenario(spec)
    _gate(report)
    if spec.review.status == "draft":
        st.info("已修改：本次配置与审核版本不同，运行归档会记录新的 scenario_hash 与草稿状态。")
    st.download_button("下载课题卡（当前配置）", scenario_card(spec, catalog), f"{spec.scenario_id}-card.md")

    teacher = st.text_input("确认教师", st.session_state.get("lab_teacher", ""))
    reviewed = st.checkbox("我已阅读模型假设与限制，确认按上述配置运行")
    if st.button("运行对照实验", type="primary", disabled=not (report.ok and teacher.strip() and reviewed)):
        _run_confirmed(spec, teacher, output_root)
    if st.session_state.get("lab_comparison"):
        st.button("查看对照分析", on_click=_goto("对照分析"))


def _band_chart(steps: pd.DataFrame, column: str, title: str, show_seeds: bool) -> go.Figure:
    fig = go.Figure()
    halted = steps[steps["halted"]]["step"].unique() if "halted" in steps else []
    for step in halted:
        fig.add_vrect(x0=step - 0.5, x1=step + 0.5, fillcolor="rgba(148,163,184,0.12)", line_width=0)
    for index, (group, frame) in enumerate(steps.groupby("group_id", sort=False)):
        color = GROUP_COLORS[index % len(GROUP_COLORS)]
        stats = frame.groupby("step")[column].agg(["mean", "min", "max"])
        if show_seeds:
            for seed, path in frame.groupby("seed"):
                fig.add_scatter(x=path["step"], y=path[column], mode="lines", line={"width": 1, "color": color}, opacity=0.35,
                                name=f"{group} 种子 {seed}", legendgroup=group, showlegend=False)
        fig.add_scatter(x=stats.index, y=stats["max"], mode="lines", line={"width": 0, "color": color},
                        showlegend=False, legendgroup=group, hoverinfo="skip")
        fig.add_scatter(x=stats.index, y=stats["min"], mode="lines", line={"width": 0, "color": color}, fill="tonexty",
                        fillcolor=_rgba(color, 0.18), showlegend=False, legendgroup=group, hoverinfo="skip")
        fig.add_scatter(x=stats.index, y=stats["mean"], mode="lines+markers", line={"color": color},
                        name=f"组 {group}（种子均值，阴影为种子间范围）", legendgroup=group)
    return apply_dark_theme(fig, title=title, height=320, xaxis_title="时间步", margin={"t": 40, "b": 30})


def _agent_trace(run_dir: Path) -> None:
    agents = pd.read_csv(run_dir / "agents.csv")
    agent_id = st.selectbox("追溯投资者", sorted(agents["agent_id"].unique()))
    st.dataframe(agents[agents["agent_id"] == agent_id], hide_index=True, use_container_width=True)
    decisions = pd.read_csv(run_dir / "decisions.csv")
    orders = pd.read_csv(run_dir / "orders.csv")
    trades = pd.read_csv(run_dir / "trades.csv")
    st.markdown("**决策（意图）**")
    st.dataframe(decisions[decisions["agent_id"] == agent_id], hide_index=True, use_container_width=True)
    st.markdown("**订单**")
    st.dataframe(orders[orders["agent_id"] == agent_id], hide_index=True, use_container_width=True)
    st.markdown("**成交**")
    st.dataframe(trades[(trades["buyer_agent_id"] == agent_id) | (trades["seller_agent_id"] == agent_id)],
                 hide_index=True, use_container_width=True)


def render_comparison(output_root: Path) -> None:
    comparisons = list_comparisons(output_root)
    if not comparisons:
        st.info("尚无对照实验。先在场景实验室运行一个实验。")
        return
    current = st.session_state.get("lab_comparison")
    names = [str(p) for p in comparisons]
    root = Path(st.selectbox("对照实验", names, index=names.index(current) if current in names else 0,
                             format_func=lambda p: Path(p).name))
    manifest, spec = load_manifest(root), load_scenario_of(root)
    st.subheader(f"{spec.scenario_id} {spec.title}")
    st.caption(f"来源：{manifest['output_origin']}（实际仿真运行） · 场景 v{manifest['scenario_version']} · "
               f"hash {manifest['scenario_hash'][:12]} · {REVIEW[manifest['review_status']]} · "
               f"确认教师 {manifest['confirmed_by']} · 状态 {manifest['status']}")
    failed = [r for r in manifest["runs"] if r["status"] != "completed"]
    if failed:
        st.error("以下运行失败，未纳入比较（不补造数据）：" + "；".join(f"{r['group_id']}/种子{r['seed']} {r.get('error_type', '')}" for r in failed))
    if st.button("校验归档完整性"):
        result = verify_comparison(root)
        (st.success if result["status"] == "verified" else st.error)(f"{result['status']} {result['problems'] or ''}")
    steps, outcomes = comparison_frames(root)
    if outcomes.empty:
        return
    labels = {g["id"]: g["label"] for g in manifest["groups"]}
    st.write("组别：" + "；".join(f"{k} = {v}" for k, v in labels.items()) + f"；参考组 {manifest['reference_group']}")

    show_seeds = st.toggle("显示每个种子的路径", value=False)
    for name in spec.observables:
        for column in STEP_COLUMNS[name]:
            if column == "halted":
                continue
            st.plotly_chart(_band_chart(steps, column, f"{OBSERVABLES[name].label} · {column}", show_seeds), use_container_width=True)
        if OBSERVABLES[name].note:
            st.caption(OBSERVABLES[name].note)

    metric = st.selectbox("结果指标 Y（预先指定）", [spec.outcome.primary, *spec.outcome.secondary],
                          format_func=lambda m: f"{OUTCOMES[m].label}（{m}）")
    st.caption(f"定义：{OUTCOMES[metric].definition}")
    st.dataframe(group_summary(outcomes, metric), hide_index=True, use_container_width=True)
    diffs = paired_differences(outcomes, metric, manifest["reference_group"])
    if not diffs.empty:
        st.markdown("**配对种子差异**（组 − 参考组，逐种子；区间为该种子集合的描述性 bootstrap，不是现实因果估计）")
        st.dataframe(diffs.drop(columns=["per_seed"]), hide_index=True, use_container_width=True)
    interaction = factorial_interaction(outcomes, metric, spec)
    if interaction and interaction.get("available"):
        st.markdown(f"**2×2 交互差异** Δ = {interaction['formula']}")
        st.json({k: interaction[k] for k in ("mean", "sd", "n", "per_seed", "bootstrap_interval")})
    _limitations(spec)

    with st.expander("逐笔追溯：一次运行中的投资者、订单与成交"):
        completed = [r for r in manifest["runs"] if r["status"] == "completed"]
        run = st.selectbox("运行", completed, format_func=lambda r: f"{r['group_id']} 种子 {r['seed']} · {r['run_id']}")
        _agent_trace(root / run["path"])

    card = evidence_card(root, metric)
    exports = st.columns(4)
    exports[0].download_button("证据卡 JSON", json.dumps(card, ensure_ascii=False, indent=2, default=str), f"{manifest['comparison_id']}-evidence.json")
    exports[1].download_button("结果指标 CSV", outcomes.to_csv(index=False), f"{manifest['comparison_id']}-outcomes.csv")
    exports[2].download_button("逐步数据 CSV", steps.to_csv(index=False), f"{manifest['comparison_id']}-steps.csv")
    exports[3].download_button("证据报告 Markdown", evidence_report(root), f"{manifest['comparison_id']}-report.md")


def _render_draft_desk(output_root: Path) -> None:
    catalog = load_catalog()
    code = st.selectbox("课程", [c["code"] for c in catalog.courses], key="ai_course",
                        format_func=lambda x: f"{x} {catalog.course(x)['name']}（{PROGRAMS[catalog.course(x)['program']]}）")
    goal = st.text_area("教学目标", key="ai_goal", height=120,
                        placeholder="例如：让学生比较有无恐慌冲击时卖出决策与成交量的差异，并讨论停牌的作用")
    if st.button("生成场景草案", disabled=not goal.strip()):
        with st.spinner("模型起草中（只生成草案，不运行任何实验）"):
            st.session_state["ai_draft"] = draft_scenario(goal, code, output_root=output_root / "ai_drafts")
    draft = st.session_state.get("ai_draft")
    if draft is None:
        return
    if not draft.ok:
        st.error(f"草案生成失败（{draft.failure}）：{draft.message}")
        return
    st.caption(f"草案 {draft.draft_id} · 审核状态：{REVIEW['draft']} · 已保存到 {draft.path}")
    for item in draft.adjustments:
        st.info(item)
    text = st.text_area("场景草案（YAML，可修改；修改后重新经过能力门禁，仍为草稿）", draft.yaml(), height=420,
                        key=f"ai_yaml_{draft.draft_id}")
    try:
        spec = load_draft_yaml(text)
    except ValueError as exc:
        st.error(f"场景结构无效：{exc}")
        return
    st.markdown(f"**{spec.title}**　研究问题：{spec.question}")
    report = check_scenario(spec)
    _gate(report)
    _limitations(spec)
    teacher = st.text_input("确认教师", st.session_state.get("lab_teacher", ""), key="ai_teacher")
    reviewed = st.checkbox("我已逐项审阅 AI 草案的事件、参数、假设与限制，确认按此运行", key="ai_reviewed")
    if st.button("运行 AI 草案", type="primary", disabled=not (report.ok and teacher.strip() and reviewed)):
        _run_confirmed(spec, teacher, output_root)
    if st.session_state.get("lab_comparison"):
        st.button("查看对照分析", key="ai_goto_comparison", on_click=_goto("对照分析"))


def _render_explain_desk(output_root: Path) -> None:
    comparisons = list_comparisons(output_root)
    if not comparisons:
        st.info("尚无对照实验。先运行一个实验，再请 AI 依据证据卡解释。")
        return
    current = st.session_state.get("lab_comparison")
    names = [str(p) for p in comparisons]
    root = st.selectbox("对照实验", names, index=names.index(current) if current in names else 0,
                        format_func=lambda p: Path(p).name, key="ai_comparison")
    spec = load_scenario_of(root)
    metric = st.selectbox("结果指标 Y", [spec.outcome.primary, *spec.outcome.secondary], key="ai_metric",
                          format_func=lambda m: f"{OUTCOMES[m].label}（{m}）")
    question = st.text_area("问题", key="ai_question", placeholder="例如：恐慌组的卖出决策占比和基准组相比有什么不同？各种子一致吗？")
    if st.button("生成证据解释", disabled=not question.strip()):
        with st.spinner("模型解释中（只能引用证据卡中的数值）"):
            st.session_state["ai_explanation"] = explain_comparison(root, question, metric=metric)
    result = st.session_state.get("ai_explanation")
    if result is None or result.comparison_id != Path(root).name or result.metric != metric:
        return
    if result.ok:
        st.markdown(result.answer)
        return
    st.error(f"解释未通过（{result.failure}）：{result.message}")
    for item in result.violations:
        st.write(f"- {item}")
    if result.rejected_text:
        with st.expander("未通过数值核验的模型原文（不得作为结论使用）"):
            st.text(result.rejected_text)
    if result.limitations:
        st.warning("**模型限制**\n\n" + "\n".join(f"- {item}" for item in result.limitations))


def render_ai_desk(output_root: Path) -> None:
    usage = ai_usage()
    st.caption("AI 只能提议白名单内的参数与事件：草案一律为草稿，必须通过能力门禁并由教师确认后才会运行；"
               "解释只能引用证据卡中的数值，每个数值标注 run_id 与指标，并附模型限制。"
               f"今日 AI 调用 {usage['used']}/{usage['limit']}（CIVITAS_LAB_AI_DAILY_CALLS）。离线规则实验不依赖 AI。")
    draft_tab, explain_tab = st.tabs(["场景草案", "证据解释"])
    with draft_tab:
        _render_draft_desk(output_root)
    with explain_tab:
        _render_explain_desk(output_root)
