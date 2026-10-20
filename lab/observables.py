"""Observable and outcome registry.

Every value shown in Civitas Lab is computed from files inside a run archive;
nothing is interpolated or filled in. Where the core metric uses a numeric
placeholder (spread is 0.0 when one side of the book is empty), the teaching
view marks the value as undefined instead of re-labelling it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Observable:
    label: str
    source: str
    note: str = ""


@dataclass(frozen=True)
class Outcome:
    label: str
    definition: str
    unit: str = ""


OBSERVABLES: dict[str, Observable] = {
    "price": Observable("最后成交价", "metrics.csv:price", "无成交时沿用最后成交价；平坦曲线不代表风险消失"),
    "volume": Observable("成交量", "metrics.csv:volume"),
    "trade_count": Observable("成交笔数", "metrics.csv:trade_count"),
    "halted": Observable("停牌状态", "metrics.csv:halted"),
    "infected": Observable("恐慌状态人数（SIRS I）", "metrics.csv:infected", "抽象 panic 种子与 SIRS 传播，不是谣言文本语义"),
    "spread": Observable("买卖价差", "metrics.csv:spread,depth_bid_total,depth_ask_total", "任一侧无挂单时价差不可定义（空值），不记为 0"),
    "depth": Observable("买卖盘深度（前 5 档）", "metrics.csv:depth_bid_total,depth_ask_total"),
    "fees": Observable("累计佣金与印花税", "metrics.csv:fees_cumulative"),
    "sentiment": Observable("平均情绪", "agents.csv:sentiment"),
    "decisions": Observable("买/卖/持有决策数", "decisions.csv:action", "决策是意图；停牌期间不提交订单"),
    "orders": Observable("订单提交、拒绝与撤销", "order_events.csv:event,reason"),
    "sirs": Observable("SIRS 状态分布", "social.csv:susceptible,infected,recovered"),
}

# Columns of step_frame() that display each observable.
STEP_COLUMNS: dict[str, tuple[str, ...]] = {
    "price": ("price",), "volume": ("volume",), "trade_count": ("trade_count",), "halted": ("halted",),
    "infected": ("infected",), "spread": ("spread",), "depth": ("depth_bid", "depth_ask"), "fees": ("fees",),
    "sentiment": ("mean_sentiment",), "decisions": ("buy_decisions", "sell_decisions", "hold_decisions"),
    "orders": ("orders_submitted", "orders_rejected", "orders_cancelled_ttl", "orders_cancelled_halt"),
    "sirs": ("susceptible", "infected", "recovered"),
}

OUTCOMES: dict[str, Outcome] = {
    "final_return": Outcome("期末收益率", "最后一步最后成交价 / 初始价格 - 1", "比例"),
    "max_drawdown": Outcome("最大回撤", "以初始价格为起点的运行峰值到最后成交价的最大跌幅", "比例"),
    "cumulative_volume": Outcome("累计成交量", "各步 volume 之和", "股"),
    "trade_count_total": Outcome("累计成交笔数", "各步 trade_count 之和", "笔"),
    "zero_volume_steps": Outcome("零成交步数", "volume == 0 的步数", "步"),
    "undefined_spread_steps": Outcome("价差不可定义步数", "任一侧前 5 档深度为 0 的步数", "步"),
    "peak_infected": Outcome("恐慌峰值人数", "infected 的最大值", "人"),
    "sell_decision_rate": Outcome("卖出决策占比", "decisions.csv 中 action == -1 的比例（含停牌期间的意图）", "比例"),
    "return_volatility": Outcome("单步收益率标准差", "metrics.csv return 列的样本标准差", "比例"),
    "fees_total": Outcome("累计费用", "最后一步 fees_cumulative", "元"),
    "rejected_orders": Outcome("被拒订单数", "order_events.csv 中 event == rejected 的行数", "笔"),
}


def step_frame(run_dir: str | Path) -> pd.DataFrame:
    """Per-step teaching table assembled only from archived files."""
    root = Path(run_dir)
    metrics = pd.read_csv(root / "metrics.csv")
    frame = pd.DataFrame({
        "step": metrics["step"],
        "price": metrics["price"],
        "return": metrics["return"],
        "volume": metrics["volume"],
        "trade_count": metrics["trade_count"],
        "halted": metrics["halted"].astype(str).str.lower() == "true",
        "infected": metrics["infected"],
        "depth_bid": metrics["depth_bid_total"],
        "depth_ask": metrics["depth_ask_total"],
        "fees": metrics["fees_cumulative"],
    })
    two_sided = (metrics["depth_bid_total"] > 0) & (metrics["depth_ask_total"] > 0)
    frame["spread_defined"] = two_sided
    frame["spread"] = metrics["spread"].where(two_sided)

    agents = pd.read_csv(root / "agents.csv", usecols=["step", "sentiment"])
    frame["mean_sentiment"] = frame["step"].map(agents.groupby("step")["sentiment"].mean())

    decisions = pd.read_csv(root / "decisions.csv", usecols=["step", "action"])
    counts = decisions.pivot_table(index="step", columns="action", aggfunc="size", fill_value=0)
    for action, name in ((1, "buy_decisions"), (-1, "sell_decisions"), (0, "hold_decisions")):
        frame[name] = frame["step"].map(counts[action] if action in counts else {}).fillna(0).astype(int)

    events = pd.read_csv(root / "order_events.csv", usecols=["step", "event", "reason"], keep_default_na=False)
    submitted = events[events["event"] != "cancel"]
    cancelled = events[events["event"] == "cancel"]
    for name, rows in (("orders_submitted", submitted),
                       ("orders_rejected", submitted[submitted["event"] == "rejected"]),
                       ("orders_cancelled_ttl", cancelled[cancelled["reason"] == "time_to_live"]),
                       ("orders_cancelled_halt", cancelled[cancelled["reason"] == "halt"])):
        frame[name] = frame["step"].map(rows.groupby("step").size()).fillna(0).astype(int)

    social = pd.read_csv(root / "social.csv", usecols=["tick", "susceptible", "recovered"])
    frame["susceptible"] = social["susceptible"].to_numpy()
    frame["recovered"] = social["recovered"].to_numpy()
    return frame


def initial_price(run_dir: str | Path) -> float:
    return float(json.loads((Path(run_dir) / "world.json").read_text(encoding="utf-8"))["initial_price"])


def run_outcomes(run_dir: str | Path, frame: pd.DataFrame | None = None) -> dict[str, float]:
    frame = step_frame(run_dir) if frame is None else frame
    start = initial_price(run_dir)
    prices = frame["price"].to_numpy(dtype=float)
    peaks = np.maximum.accumulate(np.concatenate([[start], prices]))[1:]
    decisions = frame[["buy_decisions", "sell_decisions", "hold_decisions"]].to_numpy().sum()
    return {
        "final_return": float(prices[-1] / start - 1),
        "max_drawdown": float(np.max((peaks - prices) / peaks)),
        "cumulative_volume": float(frame["volume"].sum()),
        "trade_count_total": float(frame["trade_count"].sum()),
        "zero_volume_steps": float((frame["volume"] == 0).sum()),
        "undefined_spread_steps": float((~frame["spread_defined"]).sum()),
        "peak_infected": float(frame["infected"].max()),
        "sell_decision_rate": float(frame["sell_decisions"].sum() / decisions) if decisions else float("nan"),
        "return_volatility": float(frame["return"].std(ddof=1)) if len(frame) > 1 else float("nan"),
        "fees_total": float(frame["fees"].iloc[-1]),
        "rejected_orders": float(frame["orders_rejected"].sum()),
    }
