"""Validated experiment settings; no hidden environment overrides."""
from __future__ import annotations

import json
from pathlib import Path
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


class ExperimentConfig(Settings):
    schema_version: Literal[1] = 1
    name: str = Field("minimal_market", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    mode: Literal["research", "test", "example"] = "research"
    seed: int = Field(42, ge=0, lt=2**32, strict=True)
    steps: int = Field(30, ge=1, le=100000, strict=True)
    n_agents: int = Field(64, ge=2, le=100000, strict=True)
    decision_mode: Literal["vectorized", "profile_heuristic", "llm", "mock"] = "vectorized"
    market_regime: str = "default"
    market: MarketConfig = Field(default_factory=MarketConfig)
    social: SocialConfig = Field(default_factory=SocialConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
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
        if self.mode == "research" and (self.decision_mode == "mock" or
                (self.historical_input and self.historical_input.origin != "historical")):
            raise ValueError("research mode does not accept Mock decisions or non-historical input snapshots")
        if self.decision_mode == "llm" and (not self.llm.model or self.llm.max_calls < self.n_agents * self.steps):
            raise ValueError("LLM mode requires an explicit model and sufficient explicit max_calls budget")
        if self.decision_mode == "llm" and ((self.llm.provider == "zhipu") != self.llm.model.startswith("glm-")):
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
