"""Validated experiment settings; no hidden environment overrides."""
from __future__ import annotations

import json
from pathlib import Path
from math import ceil
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class MarketConfig(Settings):
    symbol: str = Field("A_SHARE_IDX", min_length=1, pattern=r"^\S+$")
    initial_price: float = Field(3000.0, gt=0)
    price_limit: float = Field(0.1, gt=0, lt=1)
    tick_size: float = Field(0.01, gt=0)
    board_lot: int = Field(1, ge=1, strict=True)
    commission: float = Field(0.00025, ge=0, lt=0.1)
    stamp_duty: float = Field(0.0005, ge=0, lt=0.1)
    t_plus_one: bool = False
    steps_per_day: int = Field(100, ge=1, strict=True)
    order_ttl: int = Field(1, ge=1, strict=True)
    backend: Literal["python", "cpp"] = "python"

    @model_validator(mode="after")
    def validate_tick(self):
        if self.initial_price < self.tick_size:
            raise ValueError("initial_price must be at least one price tick")
        return self


class SocialConfig(Settings):
    topology: Literal["small_world", "ring", "empty"] = "small_world"
    neighbors: int = Field(6, ge=0, strict=True)
    rewire_probability: float = Field(0.1, ge=0, le=1)
    beta: float = Field(0.3, ge=0, le=1)
    gamma: float = Field(0.1, ge=0, le=1)
    delta: float = Field(0.05, ge=0, le=1)
    initial_infected: int = Field(0, ge=0, strict=True)


class EventConfig(Settings):
    step: int = Field(ge=1, strict=True)
    kind: Literal["panic", "sentiment", "halt", "resume", "tax_change"]
    value: float = 0.0

    @model_validator(mode="after")
    def validate_value(self):
        if self.kind == "panic" and (self.value < 0 or not self.value.is_integer()):
            raise ValueError("panic value must be an integer seed count")
        if self.kind == "sentiment" and not -1 <= self.value <= 1:
            raise ValueError("sentiment shock must be in [-1, 1]")
        if self.kind == "tax_change" and not 0 <= self.value < 0.1:
            raise ValueError("tax_change rate must be in [0, 0.1)")
        return self


class HistoricalInput(Settings):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin: Literal["historical", "synthetic_test", "teaching_example", "mock"]
    description: str = Field(min_length=1)


class LLMConfig(Settings):
    model: str = ""
    provider: Literal["deepseek", "zhipu"] = "deepseek"
    temperature: float = Field(0.0, ge=0, le=2)
    timeout: float = Field(20.0, gt=0, le=300)
    max_calls: int = Field(0, ge=0, strict=True)
    max_retries: int = Field(0, ge=0, le=5, strict=True)


class CognitiveConfig(Settings):
    """Controlled cognition study configuration, inactive in default runs."""

    decision_policy: Literal["llm", "random", "profile_rule", "claim_rule"] = "llm"
    llm_share: float = Field(0.25, gt=0, le=1)
    decision_interval: int = Field(5, ge=1, strict=True)
    memory_enabled: bool = True
    social_enabled: bool = True
    reflection_enabled: bool = False
    reflection_interval: int = Field(20, ge=1, strict=True)
    memory_limit: int = Field(20, ge=1, le=100, strict=True)
    social_window: int = Field(3, ge=1, le=30, strict=True)
    max_social_messages: int = Field(8, ge=1, le=100, strict=True)
    history_window: int = Field(10, ge=1, le=100, strict=True)
    social_mode: Literal["intent", "news", "both"] = "intent"
    order_schedule: Literal["fixed", "seeded_per_step"] = "fixed"


class InformationShock(Settings):
    """Synthetic claim: its truth value is private until the reveal step."""

    shock_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    step: int = Field(ge=1, strict=True)
    reveal_step: int = Field(ge=2, strict=True)
    polarity: Literal[-1, 1]
    magnitude: float = Field(gt=0, le=0.20)
    truthful: bool
    direct_fraction: float = Field(0.25, gt=0, le=1)

    @model_validator(mode="after")
    def validate_timing(self):
        if self.reveal_step <= self.step:
            raise ValueError("reveal_step must follow the claim step")
        return self


class ScientificWorldConfig(Settings):
    """Controlled latent-value benchmark; opt-in, not historical replay."""

    enabled: bool = False
    initial_fundamental: float | None = Field(None, gt=0)
    shocks: tuple[InformationShock, ...] = ()

    @model_validator(mode="after")
    def validate_shocks(self):
        ids = [shock.shock_id for shock in self.shocks]
        if len(ids) != len(set(ids)):
            raise ValueError("Scientific shock_id values must be unique")
        return self


class ExperimentConfig(Settings):
    schema_version: Literal[1] = 1
    name: str = Field("minimal_market", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    mode: Literal["research", "test", "example"] = "research"
    seed: int = Field(42, ge=0, lt=2**32, strict=True)
    steps: int = Field(30, ge=1, le=100000, strict=True)
    n_agents: int = Field(64, ge=2, le=100000, strict=True)
    decision_mode: Literal["vectorized", "profile_heuristic", "llm", "cognitive_llm", "cognitive_rule", "mock"] = "vectorized"
    market_regime: str = "default"
    market: MarketConfig = Field(default_factory=MarketConfig)
    social: SocialConfig = Field(default_factory=SocialConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    cognitive: CognitiveConfig = Field(default_factory=CognitiveConfig)
    scientific_world: ScientificWorldConfig = Field(default_factory=ScientificWorldConfig)
    events: tuple[EventConfig, ...] = ()
    historical_input: HistoricalInput | None = None

    @model_validator(mode="after")
    def validate_world(self):
        if self.social.neighbors >= self.n_agents or self.social.neighbors % 2:
            raise ValueError("social.neighbors must be even and smaller than n_agents")
        if self.social.initial_infected > self.n_agents:
            raise ValueError("initial_infected exceeds population")
        if any(e.step > self.steps or (e.kind == "panic" and e.value > self.n_agents) for e in self.events):
            raise ValueError("event exceeds experiment step/population bounds")
        if self.scientific_world.enabled:
            if self.decision_mode not in {"cognitive_llm", "cognitive_rule"}:
                raise ValueError("Scientific world requires a cognitive decision mode")
            if self.historical_input is not None:
                raise ValueError("Synthetic scientific world cannot use historical_input")
            if self.events:
                raise ValueError("Scientific world cannot mix uncontrolled legacy market events")
            if self.cognitive.social_mode == "intent":
                raise ValueError("Scientific news experiment requires social_mode=news or both")
            if any(shock.reveal_step > self.steps for shock in self.scientific_world.shocks):
                raise ValueError("Scientific shock reveal_step exceeds experiment steps")
        elif self.cognitive.social_mode in {"news", "both"} and self.decision_mode in {"cognitive_llm", "cognitive_rule"}:
            raise ValueError("News social_mode requires scientific_world.enabled")
        if self.mode == "research" and (self.decision_mode == "mock" or
                (self.historical_input and self.historical_input.origin != "historical")):
            raise ValueError("research mode does not accept Mock decisions or non-historical input snapshots")
        if self.decision_mode in {"cognitive_llm", "cognitive_rule"}:
            if self.decision_mode == "cognitive_llm" and self.cognitive.decision_policy != "llm":
                raise ValueError("cognitive_llm requires cognitive.decision_policy=llm")
            if self.decision_mode == "cognitive_rule" and self.cognitive.decision_policy == "llm":
                raise ValueError("cognitive_rule requires a named deterministic decision_policy")
            if self.decision_mode == "cognitive_rule" and self.cognitive.reflection_enabled:
                raise ValueError("LLM reflection is unavailable for deterministic cognitive rules")
        if self.decision_mode in {"llm", "cognitive_llm"}:
            if self.cognitive.reflection_enabled and not self.cognitive.memory_enabled:
                raise ValueError("Cognitive reflection requires memory_enabled=true")
            if self.decision_mode == "llm":
                required_calls = self.n_agents * self.steps
            else:
                cohort = max(1, ceil(self.n_agents * self.cognitive.llm_share))
                decisions = 1 + (self.steps - 1) // self.cognitive.decision_interval
                reflections = ((self.steps - 1) // self.cognitive.reflection_interval
                               if self.cognitive.reflection_enabled else 0)
                required_calls = cohort * (decisions + reflections)
            if not self.llm.model or self.llm.max_calls < required_calls:
                raise ValueError(
                    f"LLM mode requires explicit model and max_calls >= {required_calls}"
                )
            if (self.llm.provider == "zhipu") != self.llm.model.startswith("glm-"):
                raise ValueError("model prefix and provider disagree")
        return self


def load_config(path: str | Path) -> ExperimentConfig:
    path = Path(path).resolve()
    content = path.read_text(encoding="utf-8")
    payload = json.loads(content) if path.suffix.lower() == ".json" else yaml.safe_load(content)
    config = ExperimentConfig.model_validate(payload)
    if config.historical_input:
        snapshot = config.historical_input.model_copy(update={
            "path": str((path.parent / config.historical_input.path).resolve())})
        config = config.model_copy(update={"historical_input": snapshot})
    return config
