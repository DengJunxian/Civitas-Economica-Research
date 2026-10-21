"""ScenarioSpec / LessonSpec protocol v0.1.

These documents are Civitas Lab orchestration input, not core ExperimentConfig
input. World types, event kinds, topologies and decision modes are plain
strings here so that the deterministic capability gate (lab.capability) can
reject unsupported requests with a teaching-oriented explanation instead of a
generic schema error. Teaching metadata (question, objectives, observables,
approval, review) never reaches the core experiment schema.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SPEC_VERSION = "0.1"
Level = Literal["undergraduate", "graduate"]
ReviewStatus = Literal["draft", "pending_teacher_review", "reviewed"]


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class MarketOverrides(Spec):
    stamp_duty: float | None = None
    commission: float | None = None


class SocialOverrides(Spec):
    topology: str | None = None
    neighbors: int | None = Field(None, strict=True)
    rewire_probability: float | None = None
    beta: float | None = None
    gamma: float | None = None
    delta: float | None = None
    initial_infected: int | None = Field(None, strict=True)


class WorldSpec(Spec):
    type: str = "single_asset_orderbook"
    agents: int = Field(64, strict=True)
    steps: int = Field(30, strict=True)
    initial_price: float = 3000.0
    decision_mode: str = "vectorized"
    market_regime: str = "default"
    market: MarketOverrides = Field(default_factory=MarketOverrides)
    social: SocialOverrides = Field(default_factory=SocialOverrides)


class EventSpec(Spec):
    step: int = Field(strict=True)
    kind: str = Field(min_length=1)
    value: float = 0.0
    note: str = ""


class GroupSpec(Spec):
    """One experimental arm. Only market/social overrides and events may vary,
    so that population size, horizon, initial price and decision semantics stay
    identical across matched-seed arms."""
    id: str = Field(pattern=r"^[A-Za-z0-9]{1,8}$")
    label: str = Field(min_length=1)
    description: str = ""
    events: tuple[EventSpec, ...] = ()
    market: MarketOverrides = Field(default_factory=MarketOverrides)
    social: SocialOverrides = Field(default_factory=SocialOverrides)


class FactorialSpec(Spec):
    """2x2 design; interaction = [Y(both) - Y(a_only)] - [Y(b_only) - Y(neither)]."""
    factor_a: str = Field(min_length=1)
    factor_b: str = Field(min_length=1)
    neither: str
    a_only: str
    b_only: str
    both: str


class ComparisonSpec(Spec):
    pairing: Literal["matched_seed"] = "matched_seed"
    seeds: tuple[int, ...] = Field(min_length=1)
    groups: tuple[GroupSpec, ...] = Field(min_length=1)
    reference_group: str = ""
    factorial: FactorialSpec | None = None

    @property
    def reference(self) -> str:
        return self.reference_group or self.groups[0].id


class OutcomeSpec(Spec):
    primary: str
    secondary: tuple[str, ...] = ()


class SourcePolicy(Spec):
    required_origin: str = "simulation"
    allow_teaching_example_as_run: bool = False


class Approval(Spec):
    require_teacher_confirmation: bool = True


class Review(Spec):
    status: ReviewStatus = "pending_teacher_review"
    reviewers: tuple[str, ...] = ()
    reviewed_at: str = ""
    notes: str = ""


class ScenarioSpec(Spec):
    spec_version: Literal["0.1"] = SPEC_VERSION
    scenario_id: str = Field(pattern=r"^EXP-\d{2}(-[A-Za-z0-9]+)*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(min_length=1)
    module: str = Field(pattern=r"^E[1-7](/E[1-7])?$")
    phase: Literal["P1", "P2", "P3"]
    course_codes: tuple[str, ...] = Field(min_length=1)
    levels: tuple[Level, ...] = ("undergraduate",)
    question: str = Field(min_length=1)
    objectives: tuple[str, ...] = Field(min_length=1, max_length=5)
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = Field(min_length=1)
    evidence: tuple[str, ...] = ()
    world: WorldSpec = Field(default_factory=WorldSpec)
    comparison: ComparisonSpec
    observables: tuple[str, ...] = Field(min_length=1)
    outcome: OutcomeSpec
    source_policy: SourcePolicy = Field(default_factory=SourcePolicy)
    approval: Approval = Field(default_factory=Approval)
    review: Review = Field(default_factory=Review)

    def group(self, group_id: str) -> GroupSpec:
        return next(g for g in self.comparison.groups if g.id == group_id)


class CourseRef(Spec):
    code: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    level: Level


class LessonStage(Spec):
    name: str
    start_min: int = Field(ge=0, strict=True)
    end_min: int = Field(gt=0, strict=True)
    teacher: str
    student: str
    scenario_id: str = ""
    groups: tuple[str, ...] = ()


class WorksheetQuestion(Spec):
    id: str = Field(pattern=r"^Q\d+$")
    text: str
    rubric_dimension: str = ""


class RubricItem(Spec):
    dimension: str
    weight: int = Field(gt=0, le=100, strict=True)
    excellent: str
    passing: str
    insufficient: str


# Plan v1.0 appendix C; results that contradict a prediction are not penalised.
DEFAULT_RUBRIC = (
    RubricItem(dimension="概念准确", weight=25, excellent="术语准确，能解释金融机制与条件",
               passing="大致正确，有个别混淆", insufficient="核心概念错误或缺失"),
    RubricItem(dimension="实验设计", weight=25, excellent="对照明确、种子及因素控制得当",
               passing="有基本组别，控制不完整", insufficient="未说明对照和变量"),
    RubricItem(dimension="证据推理", weight=35, excellent="使用真实 run 的指标与订单，识别反例",
               passing="能引用部分图表并推理", insufficient="只复述 AI 解释或虚构结果"),
    RubricItem(dimension="模型边界", weight=15, excellent="指出假设、适用范围和后续验证",
               passing="至少指出一个合理局限", insufficient="把模拟结果当成现实定律"),
)


class LevelTask(Spec):
    """Plan section 3.1: one scenario, two cognitive levels."""
    level: Level
    knowledge: str
    design: str
    evidence: str
    deliverable: str


class Guidance(Spec):
    """Teacher guide card (plan 5.1): prediction, operation order, probes, negative results."""
    prediction: str
    steps: tuple[str, ...] = Field(min_length=1)
    probes: tuple[str, ...] = ()
    negative_results: str = ""


class LessonSpec(Spec):
    spec_version: Literal["0.1"] = SPEC_VERSION
    lesson_id: str = Field(pattern=r"^LESSON-[A-Z]+-\d{3}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str
    duration_minutes: int = Field(gt=0, le=240, strict=True)
    primary_courses: tuple[CourseRef, ...] = Field(min_length=1)
    related_courses: tuple[CourseRef, ...] = ()
    objectives: tuple[str, ...] = Field(min_length=1, max_length=5)
    scenarios: tuple[str, ...] = Field(min_length=1)
    stages: tuple[LessonStage, ...] = Field(min_length=1)
    worksheet: tuple[WorksheetQuestion, ...] = Field(min_length=1)
    rubric: tuple[RubricItem, ...] = Field(DEFAULT_RUBRIC, min_length=1)
    level_tasks: tuple[LevelTask, ...] = ()
    guidance: Guidance | None = None
    interpretation_rules: tuple[str, ...] = ()
    mechanism_boundary: str = ""
    review: Review = Field(default_factory=Review)

    def task(self, level: Level) -> LevelTask | None:
        return next((t for t in self.level_tasks if t.level == level), None)

    @model_validator(mode="after")
    def validate_plan(self):
        if len({t.level for t in self.level_tasks}) != len(self.level_tasks):
            raise ValueError("at most one task per level")
        dimensions = {item.dimension for item in self.rubric}
        for question in self.worksheet:
            if question.rubric_dimension and question.rubric_dimension not in dimensions:
                raise ValueError(f"{question.id} maps to unknown rubric dimension {question.rubric_dimension}")
        cursor = 0
        for stage in self.stages:
            if stage.start_min != cursor or stage.end_min <= stage.start_min:
                raise ValueError(f"lesson stages must be contiguous; '{stage.name}' starts at {stage.start_min}, expected {cursor}")
            if stage.scenario_id and stage.scenario_id not in self.scenarios:
                raise ValueError(f"stage '{stage.name}' references undeclared scenario {stage.scenario_id}")
            cursor = stage.end_min
        if cursor != self.duration_minutes:
            raise ValueError(f"lesson stages cover {cursor} minutes, declared {self.duration_minutes}")
        if sum(item.weight for item in self.rubric) != 100:
            raise ValueError("rubric weights must sum to 100")
        return self


def _load_yaml(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def load_scenario(path: str | Path) -> ScenarioSpec:
    return ScenarioSpec.model_validate(_load_yaml(path))


def load_lesson(path: str | Path) -> LessonSpec:
    return LessonSpec.model_validate(_load_yaml(path))


class CalculatorPreset(Spec):
    name: str = Field(min_length=1)
    params: dict


class CalculatorSpec(Spec):
    """A deterministic calculation lab (plan 10.1): formulas, not an agent simulation."""
    spec_version: Literal["0.1"] = SPEC_VERSION
    lab_id: str = Field(pattern=r"^EXP-\d{2}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str = Field(min_length=1)
    module: str = Field(pattern=r"^E[1-7](/E[1-7])?$")
    phase: Literal["P1", "P2", "P3"]
    calculator: Literal["bond", "capital_budgeting"]
    course_codes: tuple[str, ...] = Field(min_length=1)
    levels: tuple[Level, ...] = ("undergraduate",)
    question: str = Field(min_length=1)
    objectives: tuple[str, ...] = Field(min_length=1, max_length=5)
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = Field(min_length=1)
    defaults: dict = Field(default_factory=dict)
    presets: tuple[CalculatorPreset, ...] = ()
    review: Review = Field(default_factory=Review)

    @model_validator(mode="after")
    def validate_params(self):
        from lab.calculators import CALCULATORS
        model = CALCULATORS[self.calculator][0]
        model.model_validate(self.defaults)
        for preset in self.presets:
            model.model_validate({**self.defaults, **preset.params})
        return self

    @property
    def scenario_id(self) -> str:
        return self.lab_id


def load_calculator(path: str | Path) -> CalculatorSpec:
    return CalculatorSpec.model_validate(_load_yaml(path))


def json_schemas() -> dict[str, dict]:
    """Machine-readable protocol v0.1 (written to docs/lab/schemas by scripts/export_lab_schemas.py)."""
    return {"scenario_spec": ScenarioSpec.model_json_schema(), "lesson_spec": LessonSpec.model_json_schema(),
            "calculator_spec": CalculatorSpec.model_json_schema()}
