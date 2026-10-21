"""Deterministic teaching calculators (plan v1.0 section 10.1, P2 entry).

Bond valuation/duration (EXP-17) and capital budgeting (EXP-15) as closed-form
formulas, verified by identities in tests before any link to the agent market.
They are labelled ``origin = "deterministic_calculation"``: results follow from
the inputs and textbook formulas, not from a simulation, and say nothing about
market dynamics, credit risk or how firms compete.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.reproducibility import stable_hash

CALCULATOR_VERSION = "0.1.0"
ORIGIN = "deterministic_calculation"


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


# --------------------------------------------------------------------------- bonds

class BondParams(Params):
    face: float = Field(100.0, gt=0)
    coupon_rate: float = Field(0.05, ge=0, le=1)
    years: int = Field(10, ge=1, le=100, strict=True)
    frequency: Literal[1, 2, 4] = 1
    yield_rate: float = Field(0.05, gt=-0.5, le=1)
    shocks_bp: tuple[int, ...] = (-200, -100, -50, 50, 100, 200)

    @model_validator(mode="after")
    def validate_shocks(self):
        if any(abs(s) > 1000 for s in self.shocks_bp) or any(self.yield_rate + s / 10_000 <= -self.frequency for s in self.shocks_bp):
            raise ValueError("yield shocks must stay within ±1000 bp and keep 1 + y/frequency positive")
        return self


def _bond_flows(p: BondParams) -> list[tuple[int, float]]:
    n = p.years * p.frequency
    coupon = p.face * p.coupon_rate / p.frequency
    return [(k, coupon + (p.face if k == n else 0.0)) for k in range(1, n + 1)]


def bond_price(p: BondParams, yield_rate: float | None = None) -> float:
    y = p.yield_rate if yield_rate is None else yield_rate
    g = 1 + y / p.frequency
    return math.fsum(cf / g ** k for k, cf in _bond_flows(p))


def bond_measures(p: BondParams) -> dict:
    g = 1 + p.yield_rate / p.frequency
    flows = _bond_flows(p)
    price = bond_price(p)
    macaulay = math.fsum((k / p.frequency) * cf / g ** k for k, cf in flows) / price
    modified = macaulay / g
    convexity = math.fsum(cf * k * (k + 1) / g ** (k + 2) for k, cf in flows) / (price * p.frequency ** 2)
    return {"price": price, "macaulay_duration": macaulay, "modified_duration": modified,
            "convexity": convexity, "dv01": modified * price * 1e-4,
            "current_yield": p.face * p.coupon_rate / price}


def bond_yield(p: BondParams, price: float) -> float:
    """Yield to maturity for a quoted price (bisection; price is strictly decreasing in yield)."""
    low, high = -p.frequency * 0.99, 5.0
    if not bond_price(p, high) <= price <= bond_price(p, low):
        raise ValueError("price outside the range reachable by a yield in (-99% x frequency, 500%)")
    for _ in range(200):
        mid = (low + high) / 2
        low, high = (mid, high) if bond_price(p, mid) > price else (low, mid)
    return (low + high) / 2


def bond_lab(p: BondParams) -> dict:
    m = bond_measures(p)
    shocks = []
    for bp in p.shocks_bp:
        dy = bp / 10_000
        exact = bond_price(p, p.yield_rate + dy) - m["price"]
        duration = -m["modified_duration"] * m["price"] * dy
        with_convexity = duration + 0.5 * m["convexity"] * m["price"] * dy ** 2
        shocks.append({"shock_bp": bp, "new_price": m["price"] + exact, "exact_change": exact,
                       "duration_estimate": duration, "duration_error": duration - exact,
                       "duration_convexity_estimate": with_convexity, "duration_convexity_error": with_convexity - exact})
    grid = [p.yield_rate + bp / 10_000 for bp in range(-300, 301, 25) if p.yield_rate + bp / 10_000 > -p.frequency * 0.9]
    curve = [{"yield": y, "price": bond_price(p, y),
              "duration_line": m["price"] * (1 - m["modified_duration"] * (y - p.yield_rate))} for y in grid]
    return {"measures": m, "shocks": shocks, "price_yield_curve": curve,
            "cash_flows": [{"period": k, "time_years": k / p.frequency, "cash_flow": cf} for k, cf in _bond_flows(p)]}


# --------------------------------------------------------------------------- capital budgeting

class Project(Params):
    name: str = Field(min_length=1, max_length=32)
    cash_flows: tuple[float, ...] = Field(min_length=2, max_length=60)

    @model_validator(mode="after")
    def validate_flows(self):
        if self.cash_flows[0] >= 0:
            raise ValueError(f"{self.name}: the first cash flow must be an outlay (negative)")
        return self


class BudgetParams(Params):
    discount_rate: float = Field(0.10, gt=-0.99, le=1)
    projects: tuple[Project, ...] = Field(min_length=1, max_length=4)


def npv(rate: float, cash_flows) -> float:
    return math.fsum(cf / (1 + rate) ** t for t, cf in enumerate(cash_flows))


def irr(cash_flows, low: float = -0.99, high: float = 10.0, steps: int = 4000) -> list[float]:
    """All rates in (low, high) where NPV changes sign, each refined by bisection."""
    roots, grid = [], [low + (high - low) * i / steps for i in range(steps + 1)]
    values = [npv(r, cash_flows) for r in grid]
    for (a, fa), (b, fb) in zip(zip(grid, values), zip(grid[1:], values[1:])):
        if fa == 0:
            roots.append(a)
        elif fa * fb < 0:
            for _ in range(100):
                mid = (a + b) / 2
                fm = npv(mid, cash_flows)
                a, fa, b = (mid, fm, b) if fa * fm > 0 else (a, fa, mid)
            roots.append((a + b) / 2)
    return roots


def payback(cash_flows, rate: float = 0.0) -> float | None:
    """Periods until cumulative (discounted) cash flow turns non-negative, interpolated within the period."""
    cumulative = 0.0
    for t, cf in enumerate(cash_flows):
        value = cf / (1 + rate) ** t
        if t > 0 and cumulative < 0 <= cumulative + value:
            return t - 1 + (-cumulative / value)
        cumulative += value
    return None


def sign_changes(cash_flows) -> int:
    signs = [cf > 0 for cf in cash_flows if cf != 0]
    return sum(a != b for a, b in zip(signs, signs[1:]))


def decision(value: float, cash_flows) -> str:
    """NPV rule; values within rounding noise of zero (e.g. discounting exactly at the IRR) are indifferent."""
    tolerance = 1e-9 * max(abs(cf) for cf in cash_flows)
    return "accept" if value > tolerance else "reject" if value < -tolerance else "indifferent"


def budget_lab(p: BudgetParams) -> dict:
    projects = []
    for project in p.projects:
        flows = project.cash_flows
        rates = irr(flows)
        value = npv(p.discount_rate, flows)
        projects.append({"name": project.name, "npv": value, "irr": rates,
                         "irr_unique": len(rates) == 1, "sign_changes": sign_changes(flows),
                         "payback": payback(flows), "discounted_payback": payback(flows, p.discount_rate),
                         "profitability_index": npv(p.discount_rate, (0.0, *flows[1:])) / -flows[0],
                         "decision": decision(value, flows)})
    grid = [r / 100 for r in range(0, 41)]
    profile = [{"rate": r, **{pr.name: npv(r, pr.cash_flows) for pr in p.projects}} for r in grid]
    crossovers = []
    for i, a in enumerate(p.projects):
        for b in p.projects[i + 1:]:
            length = max(len(a.cash_flows), len(b.cash_flows))
            diff = [(a.cash_flows[t] if t < len(a.cash_flows) else 0.0) - (b.cash_flows[t] if t < len(b.cash_flows) else 0.0)
                    for t in range(length)]
            crossovers.append({"projects": [a.name, b.name], "rates": irr(diff, low=-0.5, high=1.0) if any(diff) else []})
    best = max(projects, key=lambda x: x["npv"])["name"]
    by_irr = [x for x in projects if x["irr_unique"]]
    return {"projects": projects, "npv_profile": profile, "crossover_rates": crossovers,
            "best_by_npv": best, "best_by_irr": max(by_irr, key=lambda x: x["irr"][0])["name"] if by_irr else None}


# --------------------------------------------------------------------------- registry

CALCULATORS = {"bond": (BondParams, bond_lab), "capital_budgeting": (BudgetParams, budget_lab)}


def compute(kind: str, params: dict) -> dict:
    """Validated parameters -> result with provenance; same inputs always give the same output."""
    model, function = CALCULATORS[kind]
    validated = model.model_validate(params)
    payload = validated.model_dump(mode="json")
    return {"origin": ORIGIN, "calculator": kind, "calculator_version": CALCULATOR_VERSION,
            "params": payload, "params_hash": stable_hash({"calculator": kind, "params": payload}),
            "result": function(validated)}
