"""Deterministic capability gate (no LLM).

Every scenario, whether written by a teacher or drafted by an AI assistant,
passes through this gate before anything runs. It answers three questions:
does the standard engine support what is asked, is the event timeline
coherent, and does the comparison design keep matched arms comparable. A
request that needs another engine (bank, macro, multi-asset...) is rejected
with the engine it needs, never approximated by a sentiment shock.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from lab.observables import OBSERVABLES, OUTCOMES
from lab.specs import EventSpec, ScenarioSpec

Severity = Literal["error", "warning"]

SUPPORTED_WORLD = "single_asset_orderbook"
SUPPORTED_EVENTS = ("panic", "sentiment", "halt", "resume", "tax_change")
SUPPORTED_TOPOLOGIES = ("small_world", "ring", "empty")
SUPPORTED_DECISIONS = ("vectorized", "profile_heuristic")
# Same-step execution order is fixed here, never taken from UI click order:
# reopen the market and apply rule changes first, then shocks, then closures.
EVENT_PRIORITY = {"resume": 0, "tax_change": 1, "sentiment": 2, "panic": 3, "halt": 4}

# World types and event kinds that the plan assigns to future engines.
PLANNED_ENGINES = {
    "multi_asset": ("多资产内核", "P2", "EXP-13/14"),
    "portfolio": ("组合/约束引擎", "P2", "EXP-14"),
    "firm_finance": ("企业模型与财务账本", "P2", "EXP-15/16"),
    "bond_valuation": ("估值计算引擎", "P2", "EXP-17"),
    "derivatives": ("衍生工具模块", "P3", "EXP-18"),
    "bank_ledger": ("银行账本模型", "P3", "EXP-19/20"),
    "macro_closure": ("宏观闭合模型", "P3", "EXP-21"),
    "fiscal": ("财政主体模型", "P3", "EXP-22"),
    "fund_network": ("多资产 + 机构网络", "P3", "EXP-23"),
    "wealth_advisory": ("财富管理场景", "P3", "EXP-24"),
    "platform_two_sided": ("平台双边市场", "P3", "EXP-25"),
    "open_economy": ("开放经济模块", "P3", "EXP-26"),
}
PLANNED_EVENTS = {
    "policy_rate": "macro_closure", "rate_cut": "macro_closure", "rate_hike": "macro_closure",
    "rrr_cut": "bank_ledger", "reserve_requirement": "bank_ledger", "credit_expansion": "bank_ledger",
    "bank_run": "bank_ledger", "deposit_withdrawal": "bank_ledger",
    "fiscal_spending": "fiscal", "income_tax": "fiscal", "corporate_tax": "fiscal",
    "fx_shock": "open_economy", "exchange_rate": "open_economy",
    "coupon_change": "bond_valuation", "yield_shift": "bond_valuation",
    "option_hedge": "derivatives", "rebalance": "portfolio",
}
OTHER_UNSUPPORTED_EVENTS = {
    "rumor_text": "谣言文本到信念变化的语义机制尚未验证；P1 只支持抽象 panic 种子冲击（文本真假与辟谣为 P2 扩展）",
    "debunk": "辟谣/可信度机制属于 P2 独立扩展",
    "market_maker_withdrawal": "标准模型没有做市商主体；EXP-02 不预设做市商撤退",
    "short_selling": "标准账本禁止卖空",
    "margin": "标准账本没有融资融券",
    "price_target": "不能直接设定价格路径；价格只能来自撮合",
}
LIMITS = {"max_runs": 40, "warn_runs": 20, "max_agent_steps": 2_000_000, "warn_agent_steps": 200_000,
          "unusual_tax": 0.01}


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    path: str
    message: str


@dataclass
class CapabilityReport:
    scenario_id: str
    issues: list[Issue] = field(default_factory=list)
    run_count: int = 0
    agent_steps: int = 0

    @property
    def status(self) -> Literal["supported", "supported_with_warnings", "rejected"]:
        if any(i.severity == "error" for i in self.issues):
            return "rejected"
        return "supported_with_warnings" if self.issues else "supported"

    @property
    def ok(self) -> bool:
        return self.status != "rejected"

    def error(self, code: str, path: str, message: str) -> None:
        self.issues.append(Issue("error", code, path, message))

    def warn(self, code: str, path: str, message: str) -> None:
        self.issues.append(Issue("warning", code, path, message))

    def to_dict(self) -> dict:
        return {"scenario_id": self.scenario_id, "status": self.status, "run_count": self.run_count,
                "agent_steps": self.agent_steps, "issues": [asdict(i) for i in self.issues]}


def _engine_message(engine: str) -> str:
    name, phase, scenarios = PLANNED_ENGINES[engine]
    return f"需要{name}（规划 {phase}，{scenarios}）；当前单资产撮合内核不能代替该机制"


def ordered_events(events: tuple[EventSpec, ...]) -> list[EventSpec]:
    return sorted(events, key=lambda e: (e.step, EVENT_PRIORITY.get(e.kind, 99)))


def check_timeline(report: CapabilityReport, path: str, events: tuple[EventSpec, ...], steps: int, agents: int) -> None:
    halted = False
    seen: set[tuple[int, str]] = set()
    for event in ordered_events(events):
        where = f"{path}.events[step={event.step},kind={event.kind}]"
        if event.kind in PLANNED_EVENTS:
            report.error("UNSUPPORTED_MECHANISM", where, _engine_message(PLANNED_EVENTS[event.kind]))
            continue
        if event.kind in OTHER_UNSUPPORTED_EVENTS:
            report.error("UNSUPPORTED_MECHANISM", where, OTHER_UNSUPPORTED_EVENTS[event.kind])
            continue
        if event.kind not in SUPPORTED_EVENTS:
            report.error("UNKNOWN_EVENT", where, f"未知事件类型；P1 仅支持 {', '.join(SUPPORTED_EVENTS)}")
            continue
        if not 1 <= event.step <= steps:
            report.error("STEP_OUT_OF_RANGE", where, f"事件步数须在 1..{steps} 之间")
            continue
        if (event.step, event.kind) in seen:
            report.error("DUPLICATE_EVENT", where, "同一步重复的同类事件会产生覆盖歧义，请合并为一个事件")
            continue
        seen.add((event.step, event.kind))
        if event.kind == "panic" and (event.value < 0 or not float(event.value).is_integer() or event.value > agents):
            report.error("VALUE_OUT_OF_RANGE", where, f"panic 的 value 是恐慌种子人数，须为 0..{agents} 的整数")
        elif event.kind == "sentiment" and not -1 <= event.value <= 1:
            report.error("VALUE_OUT_OF_RANGE", where, "sentiment 冲击须在 [-1, 1]")
        elif event.kind == "tax_change":
            if not 0 <= event.value < 0.1:
                report.error("VALUE_OUT_OF_RANGE", where, "tax_change 是卖方印花税率，须在 [0, 0.1)")
            elif event.value > LIMITS["unusual_tax"]:
                report.warn("UNUSUAL_VALUE", where, f"印花税率 {event.value} 高于 {LIMITS['unusual_tax']}，请确认不是把百分数写成小数")
        elif event.kind in {"halt", "resume"}:
            if event.value:
                report.warn("IGNORED_VALUE", where, f"{event.kind} 不使用 value，将按 0 执行")
            if event.kind == "halt" and halted:
                report.error("DUPLICATE_HALT", where, "市场已处于停牌状态，重复 halt 没有含义")
            if event.kind == "resume" and not halted:
                report.error("UNPAIRED_RESUME", where, "resume 之前没有对应的 halt")
            if event.kind == "halt" and (event.step, "resume") in seen:
                report.error("HALT_RESUME_SAME_STEP", where, "同一步同时停牌与复牌，请拆分到不同步")
            halted = event.kind == "halt"
    if halted:
        report.warn("HALT_UNTIL_END", f"{path}.events", "停牌后未复牌：剩余步数全部零成交，价格沿用最后成交价")


def check_scenario(spec: ScenarioSpec) -> CapabilityReport:
    report = CapabilityReport(spec.scenario_id)
    world, comparison = spec.world, spec.comparison

    if world.type != SUPPORTED_WORLD:
        if world.type in PLANNED_ENGINES:
            report.error("UNSUPPORTED_WORLD", "world.type", _engine_message(world.type))
        else:
            report.error("UNKNOWN_WORLD", "world.type", f"未知世界类型；P1 只支持 {SUPPORTED_WORLD}")
    if world.decision_mode == "llm":
        report.error("UNSUPPORTED_DECISION", "world.decision_mode",
                     "LLM 交易者需要 EXP-11 评价协议、模型预算与调用快照（P2）；P1 课堂实验默认离线规则")
    elif world.decision_mode == "mock":
        report.error("UNSUPPORTED_DECISION", "world.decision_mode", "Mock 决策不是仿真结果，不能用于教学实验")
    elif world.decision_mode not in SUPPORTED_DECISIONS:
        report.error("UNKNOWN_DECISION", "world.decision_mode", f"P1 支持 {', '.join(SUPPORTED_DECISIONS)}")
    if not 2 <= world.agents <= 10_000:
        report.error("VALUE_OUT_OF_RANGE", "world.agents", "人口规模须在 2..10000")
    if not 1 <= world.steps <= 1_000:
        report.error("VALUE_OUT_OF_RANGE", "world.steps", "课堂实验步数须在 1..1000")

    for path, social in [("world.social", world.social)] + [(f"groups[{g.id}].social", g.social) for g in comparison.groups]:
        if social.topology is not None and social.topology not in SUPPORTED_TOPOLOGIES:
            report.error("UNKNOWN_TOPOLOGY", f"{path}.topology", f"P1 支持 {', '.join(SUPPORTED_TOPOLOGIES)}")

    ids = [g.id for g in comparison.groups]
    if len(set(ids)) != len(ids):
        report.error("DUPLICATE_GROUP", "comparison.groups", "实验组 id 不能重复")
    if comparison.reference not in ids:
        report.error("UNKNOWN_GROUP", "comparison.reference_group", "对照参考组不存在")
    if len(set(comparison.seeds)) != len(comparison.seeds):
        report.error("DUPLICATE_SEED", "comparison.seeds", "种子集合不能重复")
    if any(not 0 <= s < 2**32 for s in comparison.seeds):
        report.error("VALUE_OUT_OF_RANGE", "comparison.seeds", "种子须在 [0, 2^32)")
    if len(comparison.groups) == 1:
        report.warn("NO_CONTROL", "comparison.groups", "只有一个组：结果只能描述机制，不能作对照比较")
    elif comparison.reference in ids:
        ref = spec.group(comparison.reference)
        for g in comparison.groups:
            if g.id != ref.id and (g.events, g.market, g.social) == (ref.events, ref.market, ref.social):
                report.warn("IDENTICAL_GROUPS", f"groups[{g.id}]", f"与参考组 {ref.id} 设置完全相同，差异只来自随机性")
    if len(comparison.seeds) == 1 and len(comparison.groups) > 1:
        report.warn("SINGLE_SEED", "comparison.seeds", "只有一个种子：单次路径不能解释为稳健结果")
    if comparison.factorial:
        cells = comparison.factorial.model_dump(exclude={"factor_a", "factor_b"})
        if set(cells.values()) - set(ids) or len(set(cells.values())) != 4:
            report.error("INVALID_FACTORIAL", "comparison.factorial", "2×2 设计须映射到 4 个不同且存在的实验组")

    for g in comparison.groups:
        check_timeline(report, f"groups[{g.id}]", g.events, world.steps, world.agents)

    for name in spec.observables:
        if name not in OBSERVABLES:
            report.error("UNKNOWN_OBSERVABLE", "observables", f"{name} 不能由运行归档计算；可用：{', '.join(OBSERVABLES)}")
    for name in (spec.outcome.primary, *spec.outcome.secondary):
        if name not in OUTCOMES:
            report.error("UNKNOWN_OUTCOME", "outcome", f"{name} 不是已定义的结果指标；可用：{', '.join(OUTCOMES)}")

    if spec.source_policy.required_origin != "simulation":
        report.error("SOURCE_POLICY", "source_policy.required_origin", "教学实验结果必须来自实际仿真运行（simulation）")
    if spec.source_policy.allow_teaching_example_as_run:
        report.error("SOURCE_POLICY", "source_policy.allow_teaching_example_as_run",
                     "预编排教学 CSV 不能作为新仿真结果展示")
    if not spec.approval.require_teacher_confirmation:
        report.error("APPROVAL_REQUIRED", "approval.require_teacher_confirmation", "教师确认是不可省略的执行门槛")

    report.run_count = len(comparison.groups) * len(comparison.seeds)
    report.agent_steps = report.run_count * world.agents * world.steps
    if report.run_count > LIMITS["max_runs"]:
        report.error("BUDGET", "comparison", f"共 {report.run_count} 次运行，超过单次上限 {LIMITS['max_runs']}")
    elif report.run_count > LIMITS["warn_runs"]:
        report.warn("BUDGET", "comparison", f"共 {report.run_count} 次运行，课堂现场等待时间可能较长")
    if report.agent_steps > LIMITS["max_agent_steps"]:
        report.error("BUDGET", "world", f"总计算量 {report.agent_steps} agent-steps 超过上限")
    elif report.agent_steps > LIMITS["warn_agent_steps"]:
        report.warn("BUDGET", "world", f"总计算量 {report.agent_steps} agent-steps，建议课前预运行")

    if report.ok:
        # Final authority is the core schema itself.
        from lab.compiler import compile_scenario
        try:
            compile_scenario(spec)
        except ValueError as exc:
            report.error("CORE_SCHEMA", "compiled_config", f"核心 ExperimentConfig 拒绝：{exc}")
    return report
