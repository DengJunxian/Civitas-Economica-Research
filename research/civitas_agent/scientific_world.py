"""S5: controlled synthetic fundamental values and time-gated information exposure.

IMPORTANT: Ground truth lives in simulator state and audit artifacts only.
The agent-facing `observation_for` NEVER reveals a shock's truth label, latent
fundamental value or future revelation before its scheduled reveal step.
The simulator does not set transaction prices; orders and clearing remain the
sole determinants of realized market prices.
"""
from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path
from typing import Any, Mapping


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")


class SyntheticInformationWorld:
    """Deterministic synthetic *world* coupled to a possibly stochastic LLM.

    Truthful claims permanently shift a latent benchmark at their publication
    step. False claims have no latent-value effect. The benchmark is a
    researcher-specified construct, not a calibrated intrinsic stock value.
    Only the cognitive cohort transmits news; the rule-based background
    population does not have a synthetic rumor-understanding policy.
    """

    def __init__(
        self,
        *,
        config: Any,
        seed: int,
        initial_price: float,
        active_ids: list[str],
        neighbors: Mapping[str, list[str]],
        social_enabled: bool,
        social_mode: str,
        total_steps: int,
    ) -> None:
        if not config.enabled:
            raise ValueError("SyntheticInformationWorld requires enabled config")
        self.config = config
        self.seed = int(seed)
        self.total_steps = int(total_steps)
        self.social_enabled = bool(social_enabled)
        self.social_mode = str(social_mode)
        self.active_ids = sorted(active_ids)
        self.active_set = set(active_ids)
        self.neighbors = {
            aid: set(nodes) & self.active_set for aid, nodes in neighbors.items()
            if aid in self.active_set
        }
        self.initial_fundamental = float(config.initial_fundamental or initial_price)
        if not math.isfinite(self.initial_fundamental) or self.initial_fundamental <= 0:
            raise ValueError("Invalid initial latent fundamental")
        self.fundamental = self.initial_fundamental
        self.previous_fundamental = self.initial_fundamental
        self.step = 0
        self.shocks = {s.shock_id: s for s in config.shocks}
        self.exposure_steps: dict[str, dict[str, int]] = {
            key: {} for key in self.shocks
        }
        self.deliveries: list[dict[str, Any]] = []
        self.revelations: list[dict[str, Any]] = []
        self.fundamental_rows: list[dict[str, Any]] = []
        self.order_schedules: list[dict[str, Any]] = []
        # An unread-inbox cursor is separate from the optional long-term
        # cognitive memory. Once read, a bulletin is not replayed forever.
        self.last_observed_step: dict[str, int] = {
            aid: 0 for aid in self.active_ids
        }
        self.new_deliveries = 0

    @staticmethod
    def _visible_claim(shock: Any) -> dict[str, Any]:
        """Only content that a synthetic source could have claimed at publication."""
        return {
            "shock_id": shock.shock_id,
            "source_id": shock.source_id,
            "publication_step": shock.step,
            "claimed_direction": "positive" if shock.polarity == 1 else "negative",
            "claimed_fractional_value_change": round(shock.polarity * shock.magnitude, 8),
        }

    def _deliver(
        self, shock: Any, agent_id: str, step: int, *,
        channel: str, from_agent_id: str | None,
    ) -> None:
        if agent_id not in self.active_set:
            raise ValueError("Unknown or non-cognitive information recipient")
        if agent_id in self.exposure_steps[shock.shock_id]:
            return
        if channel == "social":
            if step <= shock.step or from_agent_id not in self.neighbors.get(agent_id, set()):
                raise ValueError("Illegal social information transmission")
            if self.exposure_steps[shock.shock_id].get(from_agent_id) != step - 1:
                raise ValueError("Social information must originate one step earlier")
        row = {
            **self._visible_claim(shock),
            "agent_id": agent_id,
            "received_step": int(step),
            "channel": channel,
            "from_agent_id": from_agent_id,
        }
        self.exposure_steps[shock.shock_id][agent_id] = step
        self.deliveries.append(row)
        self.new_deliveries += 1

    def advance(self, step: int) -> None:
        if step != self.step + 1 or step > self.total_steps:
            raise ValueError("World must advance exactly once per sequential step")
        self.step = int(step)
        self.new_deliveries = 0
        self.previous_fundamental = self.fundamental

        for shock in sorted(self.shocks.values(), key=lambda item: (item.step, item.shock_id)):
            if shock.step == step:
                # Latent value does not imply any automatic market-price movement.
                if shock.truthful:
                    self.fundamental *= 1.0 + shock.polarity * shock.magnitude
                population = list(self.active_ids)
                count = max(1, math.ceil(len(population) * shock.direct_fraction))
                chooser = random.Random(f"{self.seed}|direct-exposure-v1|{shock.shock_id}")
                seeds = sorted(chooser.sample(population, count))
                for aid in seeds:
                    self._deliver(shock, aid, step, channel="direct", from_agent_id=None)

            if (
                shock.step < step < shock.reveal_step
                and self.social_enabled and self.social_mode in {"news", "both"}
            ):
                # Take a frozen previous-step sender set: no same-step cascades.
                previous_senders = sorted(
                    aid for aid, received in self.exposure_steps[shock.shock_id].items()
                    if received == step - 1
                )
                candidates: dict[str, str] = {}
                for sender in previous_senders:
                    for recipient in sorted(self.neighbors.get(sender, set())):
                        if recipient not in self.exposure_steps[shock.shock_id]:
                            candidates.setdefault(recipient, sender)
                for recipient, sender in sorted(candidates.items()):
                    self._deliver(
                        shock, recipient, step, channel="social",
                        from_agent_id=sender,
                    )

            if shock.reveal_step == step:
                self.revelations.append({
                    **self._visible_claim(shock),
                    "reveal_step": step,
                    "verdict": "supported" if shock.truthful else "refuted",
                })

        if not math.isfinite(self.fundamental) or self.fundamental <= 0:
            raise RuntimeError("Latent fundamental became invalid")

    def observation_for(self, agent_id: str, step: int) -> dict[str, Any]:
        """Construct only information available to a specified Agent as of now."""
        if step != self.step:
            raise ValueError("As-of observation requested for incorrect step")
        if agent_id not in self.active_set:
            raise ValueError("Unknown cognitive investor")
        last_read = self.last_observed_step[agent_id]
        claims = [
            {key: value for key, value in row.items() if key != "agent_id"}
            for row in self.deliveries
            if row["agent_id"] == agent_id
            and last_read < row["received_step"] <= step
        ]
        verified = [
            dict(row) for row in self.revelations
            if last_read < row["reveal_step"] <= step
        ]
        # The observation is a NEW-information inbox, not a permanent
        # history store. Otherwise M0 would secretly retain public memory.
        # All source labels here are derived from currently unread verdicts.
        record: dict[str, dict[str, int]] = {}
        for row in verified:
            stats = record.setdefault(
                row["source_id"], {"supported": 0, "refuted": 0}
            )
            stats[row["verdict"]] += 1
        return {
            "received_claims": claims,
            "public_verifications": verified,
            "observed_source_track_records": record,
        }

    def acknowledge_observation(self, agent_id: str, step: int) -> None:
        """Advance the inbox cursor only after a successful model decision."""
        if agent_id not in self.active_set or step != self.step:
            raise ValueError("Cannot acknowledge an unknown or future observation")
        if step <= self.last_observed_step[agent_id]:
            raise ValueError("Duplicate or regressed observation acknowledgment")
        self.last_observed_step[agent_id] = step

    def add_order_schedule(self, step: int, ordered_agent_ids: list[str]) -> None:
        if step != self.step or len(ordered_agent_ids) != len(set(ordered_agent_ids)):
            raise ValueError("Invalid order-submission schedule")
        self.order_schedules.append({
            "step": step, "ordered_agent_ids": list(ordered_agent_ids)
        })

    def market_metrics(self, *, step: int, price: float) -> dict[str, Any]:
        if step != self.step or not math.isfinite(price) or price <= 0:
            raise ValueError("Invalid step/price in fundamental metric")
        value = self.fundamental
        deviation = (float(price) - value) / value
        row = {
            "step": step,
            "fundamental_value": value,
            "fundamental_return": value / self.previous_fundamental - 1.0,
            "signed_price_deviation": deviation,
            "absolute_price_deviation": abs(deviation),
            "cumulative_news_exposures": len(self.deliveries),
            "new_news_exposures": self.new_deliveries,
            "cumulative_public_verifications": len(self.revelations),
        }
        self.fundamental_rows.append(row)
        return {key: val for key, val in row.items() if key != "step"}

    def write_artifacts(self, output: Path) -> None:
        with (output / "study_fundamentals.csv").open("w", newline="", encoding="utf-8") as f:
            fields = [
                "step", "fundamental_value", "fundamental_return",
                "signed_price_deviation", "absolute_price_deviation",
                "cumulative_news_exposures", "new_news_exposures",
                "cumulative_public_verifications",
            ]
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.fundamental_rows)
        _write_jsonl(output / "study_news_exposures.jsonl", self.deliveries)
        _write_jsonl(output / "study_news_verifications.jsonl", self.revelations)
        _write_jsonl(output / "study_order_schedule.jsonl", self.order_schedules)
        (output / "study_world_private.json").write_text(
            json.dumps({
                "schema_version": 1,
                "description": "Researcher-held synthetic truth; NEVER agent-facing",
                "source_type": "synthetic_controlled",
                "initial_fundamental": self.initial_fundamental,
                "shocks_with_truth_labels": [
                    x.model_dump(mode="json") for x in self.config.shocks
                ],
                "active_cognitive_agents": list(self.active_ids),
                "information_flow": "direct seeds + previous-step cognitive-neighbor relay",
                "observation_semantics": "new-information inbox; acknowledgment on successful model decision",
                "latent_value_affects_order_matching": False,
                "social_news_relay_enabled": (
                    self.social_enabled and self.social_mode in {"news", "both"}
                ),
            }, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
