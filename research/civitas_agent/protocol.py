"""Auditable, opt-in LLM investor protocol for controlled memory/social studies.

Messages are prior-step *intent broadcasts*, not executed trade observations.
Memory is written only after settlement, and is only visible at later steps.
The rule-based background population is left unchanged in the market runner.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from typing import Any, Mapping


SYSTEM_PROMPT = (
    "You are a simulated investor, not a financial adviser. Use only the supplied "
    "as-of-step observation. Reply with a JSON object containing exactly: "
    "'action' (BUY, SELL, HOLD), 'fraction' (number in [0,1] of available "
    "cash or sellable shares), and 'aggressiveness_ticks' (integer -3..3). "
    "Never assume future information or access to other agents' private accounts."
)


def _jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def _strict_intent(raw: str) -> tuple[int, float, int]:
    obj = json.loads(raw)
    if type(obj) is not dict or set(obj) != {"action", "fraction", "aggressiveness_ticks"}:
        raise ValueError("Invalid cognitive LLM decision schema: expected three exact fields")
    action = obj["action"]
    frac = obj["fraction"]
    ticks = obj["aggressiveness_ticks"]
    if action not in ("BUY", "SELL", "HOLD") or type(frac) not in (int, float):
        raise ValueError("Invalid cognitive LLM decision schema: action/fraction")
    if not math.isfinite(frac) or not 0 <= frac <= 1 or type(ticks) is not int or not -3 <= ticks <= 3:
        raise ValueError("Invalid cognitive LLM decision schema: fraction/aggressiveness_ticks")
    return {"BUY": 1, "SELL": -1, "HOLD": 0}[action], float(frac), ticks


class CognitiveProtocol:
    """Long-lived state for one independent experimental world.

    The sampled LLM cohort remains identical across the four factorial arms
    when the population seed, size, and share are identical.
    """

    def __init__(
        self,
        *,
        config: Any,
        agent_ids: list[str],
        neighbors: Mapping[str, list[str]],
        seed: int,
        initial_price: float,
        total_steps: int,
    ) -> None:
        self.total_steps = int(total_steps)
        self.seed = int(seed)
        self.config = config
        cohort_size = max(1, math.ceil(len(agent_ids) * config.llm_share))
        self.active_ids = sorted(random.Random(seed ^ 0xC1A57A).sample(agent_ids, cohort_size))
        self.active_set = set(self.active_ids)
        self.neighbors = {agent: set(adjacent) for agent, adjacent in neighbors.items()}
        self.price_history: list[float] = [float(initial_price)]
        self.memory: dict[str, list[dict[str, Any]]] = {agent: [] for agent in self.active_ids}
        self.intent_messages: list[dict[str, Any]] = []
        self.social_trace: list[dict[str, Any]] = []
        self.observations: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.outcomes: list[dict[str, Any]] = []
        self.reflections: list[dict[str, Any]] = []
        self.latest_information_inbox: dict[str, dict[str, Any]] = {}
        self.logical_calls = 0
        self.rule_decisions = 0

    def _social_context(self, agent_id: str, step: int) -> list[dict[str, Any]]:
        if not self.config.social_enabled or self.config.social_mode not in {"intent", "both"}:
            return []
        adjacent = self.neighbors.get(agent_id, set())
        rows = [
            msg for msg in self.intent_messages
            if msg["source_agent_id"] in adjacent
            and step - self.config.social_window <= msg["step"] < step
        ]
        return rows[-self.config.max_social_messages:]

    def _observation(
        self, *, step: int, agent_id: str, persona: Any, account: Any,
        available: tuple[float, int], market: dict[str, Any],
        scientific_world: Any | None = None,
    ) -> dict[str, Any]:
        memory = self.memory[agent_id][-self.config.memory_limit:] if self.config.memory_enabled else []
        observation = {
            "step": step,
            "agent_id": agent_id,
            "persona": persona.to_dict(),
            "account": {
                "cash": float(account.cash), "shares": int(account.shares),
                "cost_basis": float(account.cost_basis),
                "available_cash": float(available[0]), "sellable_shares": int(available[1]),
            },
            "market": market,
            "price_history": self.price_history[-self.config.history_window:],
            "episodic_memory": memory,
            "neighbor_intentions": self._social_context(agent_id, step),
        }
        # Treatment flags must not be disclosed to the model: doing so can
        # confound comparisons even when the factual inputs are the same.
        if scientific_world is not None:
            observation["information_signals"] = scientific_world.observation_for(
                agent_id, step
            )
        return observation

    @staticmethod
    def _convert_intent(
        *, action: int, fraction: float, ticks: int, available: tuple[float, int],
        price: float, best_bid: float, best_ask: float, rules: Any, prev_close: float,
    ) -> tuple[int, int, float]:
        tick = rules.tick_size
        anchor = (best_ask if action == 1 else best_bid) or price
        proposed = anchor + (ticks * tick if action == 1 else -ticks * tick)
        lower = max(tick, prev_close * (1.0 - rules.price_limit))
        upper = prev_close * (1.0 + rules.price_limit)
        lower_tick = math.ceil(lower / tick - 1e-9)
        upper_tick = math.floor(upper / tick + 1e-9)
        if lower_tick > upper_tick:
            raise ValueError("No valid price tick within exchange limits")
        price_ticks = max(lower_tick, min(upper_tick, round(proposed / tick)))
        limit = round(price_ticks * tick, 10)
        lot = rules.board_lot
        if action == 1:
            capacity = max(0, int(max(0.0, available[0]) / (limit * (1 + rules.commission))))
        elif action == -1:
            capacity = max(0, available[1])
        else:
            capacity = 0
        quantity = int(math.floor(capacity * fraction / lot)) * lot
        if action == 0 or quantity <= 0:
            return 0, 0, limit
        return action, quantity, limit

    async def decide(
        self, *, step: int, router: Any, personas: list[Any], accounts: Mapping[str, Any],
        clearing: Any, market: dict[str, Any], rules: Any, prev_close: float, llm: Any,
        base_actions: Any, base_quantities: Any, base_prices: Any,
        agent_ids: list[str], scientific_world: Any | None = None,
    ) -> tuple[list[int], list[int], list[float]]:
        actions = [int(x) for x in base_actions]
        quantities = [int(x) for x in base_quantities]
        prices = [float(x) for x in base_prices]
        due = (step - 1) % self.config.decision_interval == 0
        for i, aid in enumerate(agent_ids):
            if aid not in self.active_set:
                continue
            # No repeated blind orders between cognition ticks.
            actions[i], quantities[i], prices[i] = 0, 0, float(market["price"])
            if not due:
                continue
            obs = self._observation(
                step=step, agent_id=aid, persona=personas[i], account=accounts[aid],
                available=clearing.available(aid), market=market,
                scientific_world=scientific_world,
            )
            self.observations.append(obs)
            if self.config.decision_policy == "llm":
                if self.logical_calls >= llm.max_calls:
                    raise RuntimeError("Cognitive LLM logical call budget exceeded")
                self.logical_calls += 1
                response = await router.complete(
                    [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": json.dumps(
                         obs, sort_keys=True, ensure_ascii=False
                     )}],
                    mode=llm.provider, model=llm.model,
                    temperature=llm.temperature,
                    timeout=llm.timeout, max_tokens=160, json_mode=True,
                    allow_provider_fallback=False,
                )
                raw = response.text
            else:
                from research.civitas_agent.baselines import deterministic_intent
                raw = json.dumps(deterministic_intent(
                    self.config.decision_policy, obs, seed=self.seed,
                ), allow_nan=False)
                self.rule_decisions += 1
            action, fraction, ticks = _strict_intent(raw)
            action, quantity, limit = self._convert_intent(
                action=action, fraction=fraction, ticks=ticks,
                available=clearing.available(aid),
                price=market["price"], best_bid=market["best_bid"],
                best_ask=market["best_ask"], rules=rules, prev_close=prev_close,
            )
            actions[i], quantities[i], prices[i] = action, quantity, limit
            if scientific_world is not None:
                scientific_world.acknowledge_observation(aid, step)
                self.latest_information_inbox[aid] = obs["information_signals"]
            self.decisions.append({
                "step": step, "agent_id": aid, "action": action, "quantity": quantity,
                "limit_price": limit, "fraction": fraction,
                "aggressiveness_ticks": ticks,
                "decision_policy": self.config.decision_policy,
                "observation_sha256": hashlib.sha256(
                    json.dumps(obs, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest(),
            })
        return actions, quantities, prices

    async def after_step(
        self, *, step: int, agent_ids: list[str], actions: Any, quantities: Any,
        trades: list[Any], accounts: Mapping[str, Any], price: float,
        router: Any, llm: Any, scientific_world: Any | None = None,
    ) -> None:
        # Every emitted message is labeled as *intent*, not a filled order.
        broadcasts = [
            {"step": step, "source_agent_id": aid,
             "action": {1: "BUY", -1: "SELL", 0: "HOLD"}[int(actions[i])]}
            for i, aid in enumerate(agent_ids)
        ]
        self.social_trace.extend(broadcasts)
        self.intent_messages.extend(broadcasts)
        self.intent_messages = [
            msg for msg in self.intent_messages
            if msg["step"] >= step - self.config.social_window + 1
        ]
        self.price_history.append(float(price))
        due = (step - 1) % self.config.decision_interval == 0
        if due:
            for i, aid in enumerate(agent_ids):
                if aid not in self.active_set:
                    continue
                fills = sum(
                    int(t.quantity) for t in trades
                    if t.buyer_agent_id == aid or t.seller_agent_id == aid
                )
                account = accounts[aid]
                outcome = {
                    "step": step, "agent_id": aid,
                    "action": {1: "BUY", -1: "SELL", 0: "HOLD"}[int(actions[i])],
                    "requested_quantity": int(quantities[i]),
                    "filled_quantity": fills,
                    "observed_price": float(price),
                    "marked_wealth": float(account.cash + account.shares * price),
                }
                self.outcomes.append(outcome)
                inbox = self.latest_information_inbox.pop(aid, None)
                if self.config.memory_enabled:
                    self.memory[aid].append(outcome)
                    if inbox is not None:
                        for claim in inbox["received_claims"]:
                            self.memory[aid].append({
                                "step": step, "event": "received_claim",
                                "claim": dict(claim),
                            })
                        for verification in inbox["public_verifications"]:
                            self.memory[aid].append({
                                "step": step, "event": "public_verification",
                                "shock_id": verification["shock_id"],
                                "source_id": verification["source_id"],
                                "verdict": verification["verdict"],
                            })
                    self.memory[aid] = self.memory[aid][-self.config.memory_limit:]
        if not (self.config.memory_enabled and self.config.reflection_enabled):
            return
        if step >= self.total_steps or step % self.config.reflection_interval:
            return
        for aid in self.active_ids:
            if self.logical_calls >= llm.max_calls:
                raise RuntimeError("Cognitive reflection logical call budget exceeded")
            self.logical_calls += 1
            memory = self.memory[aid][-min(self.config.memory_limit, 6):]
            response = await router.complete(
                [{"role": "system", "content":
                  "Summarize lessons only from past recorded outcomes. "
                  "Return JSON with exactly one string field 'reflection' (at most 400 characters)."},
                 {"role": "user", "content": json.dumps(memory, sort_keys=True)}],
                mode=llm.provider, model=llm.model, temperature=llm.temperature,
                timeout=llm.timeout, max_tokens=140, json_mode=True,
                allow_provider_fallback=False,
            )
            result = json.loads(response.text)
            if type(result) is not dict or set(result) != {"reflection"} or (
                type(result["reflection"]) is not str or
                not 1 <= len(result["reflection"]) <= 400
            ):
                raise ValueError("Invalid cognitive reflection schema")
            reflection = {"step": step, "agent_id": aid,
                          "event": "reflection", "reflection": result["reflection"]}
            self.reflections.append(reflection)
            self.memory[aid].append(reflection)
            self.memory[aid] = self.memory[aid][-self.config.memory_limit:]

    def write_artifacts(self, output: Path) -> None:
        _jsonl(output / "cognitive_observations.jsonl", self.observations)
        _jsonl(output / "cognitive_decisions.jsonl", self.decisions)
        _jsonl(output / "cognitive_outcomes.jsonl", self.outcomes)
        _jsonl(output / "cognitive_social_messages.jsonl", self.social_trace)
        _jsonl(output / "cognitive_reflections.jsonl", self.reflections)
        (output / "cognitive_protocol.json").write_text(
            json.dumps({
                "active_llm_agent_ids": self.active_ids,
                "cognitive_config": self.config.model_dump(mode="json"),
                "logical_model_calls": self.logical_calls,
                "deterministic_rule_decisions": self.rule_decisions,
                "decision_policy": self.config.decision_policy,
                "social_message_semantics": "prior_step_intent_broadcast",
                "memory_write_semantics": "post_settlement_only",
            }, sort_keys=True, indent=2) + "\n", encoding="utf-8",
        )
