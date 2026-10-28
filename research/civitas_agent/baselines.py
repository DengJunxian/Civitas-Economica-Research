"""Transparent non-LLM investor policies for S6 matched cognitive-cohort controls.

These policies share the LLM investor's as-of observation, scheduled decision
opportunity, exchange order conversion, market counterparty population, and
clearing constraints. They do NOT call a provider or use private synthetic
ground truth. They are algorithmic controls, not empirical investor models.
"""
from __future__ import annotations

import math
import random
from typing import Any, Mapping


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _visible_verdicts(observation: Mapping[str, Any]) -> dict[str, str]:
    """Get only previously *seen* source evidence, never private truth."""
    results: dict[str, str] = {}
    inbox = observation.get("information_signals", {})
    for row in inbox.get("public_verifications", []):
        results[row["shock_id"]] = row["verdict"]
    for item in observation.get("episodic_memory", []):
        if item.get("event") == "public_verification":
            results[item["shock_id"]] = item["verdict"]
    return results


def _most_recent_claim(observation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    news = observation.get("information_signals", {}).get("received_claims", [])
    claims = list(news)
    for entry in observation.get("episodic_memory", []):
        if entry.get("event") == "received_claim":
            claims.append(entry["claim"])
    if not claims:
        return None
    claims.sort(key=lambda row: (int(row["received_step"]), str(row["shock_id"])))
    latest = claims[-1]
    # A transparent fixed recency window; do not invent timeless knowledge
    # in the no-memory arm or treat very old hints as fresh.
    if int(observation["step"]) - int(latest["received_step"]) > 20:
        return None
    return latest


def deterministic_intent(
    policy: str, observation: Mapping[str, Any], *, seed: int,
) -> dict[str, Any]:
    """Return the same strict intent schema requested of LLM investors."""
    step = int(observation["step"])
    aid = str(observation["agent_id"])
    rng = random.Random(f"{seed}|civitas_rule_v1|{step}|{aid}|{policy}")
    if policy == "random":
        return {
            "action": ("BUY", "SELL", "HOLD")[rng.randrange(3)],
            "fraction": round(rng.uniform(0.02, 0.12), 4),
            "aggressiveness_ticks": rng.randint(-2, 2),
        }

    price = _finite(observation["market"]["price"])
    if price <= 0:
        raise ValueError("Baseline observed an invalid market price")
    account = observation["account"]
    pnl_anchor = _finite(account.get("cost_basis"))
    history = observation.get("price_history", [])
    prior = _finite(history[-2]) if len(history) >= 2 else price
    trend = price / prior - 1 if prior > 0 else 0.0

    if policy == "passive_quote":
        # A transparent counterparty-SUPPLY diagnostic, NOT a calibrated
        # investor model or a promise of market realism. Choose the thinner
        # visible book side and place a genuine account-backed limit order
        # slightly away from the opposite top quote. The unchanged adapter,
        # order book and ClearingHouse may reject, rest or execute the order.
        book = observation["market"].get("depth", {})
        if not isinstance(book, dict):
            raise ValueError("Passive quote policy requires visible order-book depth")
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not isinstance(bids, list) or not isinstance(asks, list):
            raise ValueError("Malformed visible depth levels")
        def qty(rows: list) -> float:
            total = 0.0
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("Malformed visible order-book level")
                value = _finite(row.get("qty", row.get("quantity", 0)))
                if value < 0:
                    raise ValueError("Negative visible order-book depth")
                total += value
            return total
        bid_qty, ask_qty = qty(bids), qty(asks)
        if bid_qty < ask_qty:
            action = "BUY"
        elif ask_qty < bid_qty:
            action = "SELL"
        else:
            action = ("BUY", "SELL")[rng.randrange(2)]
        cash_ok = _finite(account.get("available_cash")) >= price * 1.001
        shares_ok = int(_finite(account.get("sellable_shares"))) >= 1
        if action == "BUY" and not cash_ok:
            action = "SELL" if shares_ok else "HOLD"
        elif action == "SELL" and not shares_ok:
            action = "BUY" if cash_ok else "HOLD"
        return {
            "action": action,
            "fraction": 0.05 if action != "HOLD" else 0.0,
            # The existing adapter interprets negative ticks as a passive
            # bid below the best ask or a passive ask above the best bid.
            "aggressiveness_ticks": -3,
        }

    if policy == "profile_rule":
        # A disclosed deterministic trend/reference-price policy. This is
        # deliberately independent of synthetic news: a pure persona control.
        unrealized_return = price / pnl_anchor - 1 if pnl_anchor > 0 else 0.0
        if unrealized_return < -0.02 or trend < -0.002:
            action = "SELL"
        elif trend > 0.002 or (pnl_anchor > 0 and unrealized_return > 0.025):
            action = "BUY"
        else:
            action = "HOLD"
        loss_aversion = max(0.5, _finite(observation.get("persona", {}).get(
            "loss_aversion", 2.25), 2.25))
        fraction = min(0.12, 0.08 / loss_aversion * 2.25)
        return {
            "action": action,
            "fraction": round(fraction, 5),
            "aggressiveness_ticks": 0,
        }

    if policy == "claim_rule":
        claim = _most_recent_claim(observation)
        if claim is None:
            # No news seen: no manufactured signal about researcher truth.
            return {"action": "HOLD", "fraction": 0.0, "aggressiveness_ticks": 0}
        direction = 1 if _finite(claim["claimed_fractional_value_change"]) > 0 else -1
        verdict = _visible_verdicts(observation).get(claim["shock_id"])
        if verdict == "refuted":
            direction *= -1
        elif verdict not in (None, "supported"):
            raise ValueError("Unknown public claim-verification verdict")
        fraction = min(0.12, max(0.02, 1.5 * abs(_finite(
            claim["claimed_fractional_value_change"]
        ))))
        return {
            "action": "BUY" if direction > 0 else "SELL",
            "fraction": round(fraction, 5),
            "aggressiveness_ticks": 0,
        }

    raise ValueError(f"Unknown deterministic research decision policy: {policy}")
