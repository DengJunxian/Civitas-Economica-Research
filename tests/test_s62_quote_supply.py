"""S6.2 genuine-account liquidity-provider mechanism: offline-only tests."""
import json
from pathlib import Path

import pandas as pd
import pytest

from core.experiments.verify import reproduce_run, verify_run
from research.civitas_agent.baselines import deterministic_intent
from research.civitas_agent.quote_supply import (
    _config, run_development, POLICIES, COMPARE,
)


def observation(*, bid_qty: float, ask_qty: float,
                cash: float = 100000.0, shares: int = 100):
    bid_rows = [{"price": 2999.9, "qty": bid_qty}] if bid_qty > 0 else []
    ask_rows = [{"price": 3000.1, "qty": ask_qty}] if ask_qty > 0 else []
    return {
        "step": 2,
        "agent_id": "agent_001",
        "persona": {"loss_aversion": 2.25},
        "market": {
            "price": 3000.0, "best_bid": 2999.9 if bid_rows else 0.0,
            "best_ask": 3000.1 if ask_rows else 0.0,
            "depth": {"bids": bid_rows, "asks": ask_rows},
        },
        "account": {
            "cash": cash, "shares": shares,
            "available_cash": cash, "sellable_shares": shares,
            "cost_basis": 2950.0,
        },
        "price_history": [3000.0],
        "episodic_memory": [],
    }


def test_passive_quote_replenishes_thin_side_using_only_visible_book():
    ask_heavy = observation(bid_qty=0, ask_qty=500)
    assert deterministic_intent("passive_quote", ask_heavy, seed=41) == {
        "action": "BUY", "fraction": 0.05, "aggressiveness_ticks": -3,
    }
    bid_heavy = observation(bid_qty=600, ask_qty=0)
    assert deterministic_intent("passive_quote", bid_heavy, seed=41) == {
        "action": "SELL", "fraction": 0.05, "aggressiveness_ticks": -3,
    }
    # Private simulator truth, if accidentally attached, cannot affect policy.
    ask_heavy["researcher_private_truth"] = {"fundamental": 10, "truthful": True}
    assert deterministic_intent("passive_quote", ask_heavy, seed=41)["action"] == "BUY"


def test_passive_quote_never_proposes_unfunded_side():
    empty = observation(bid_qty=0, ask_qty=100, cash=0, shares=0)
    output = deterministic_intent("passive_quote", empty, seed=41)
    assert output["action"] == "HOLD" and output["fraction"] == 0
    ask_heavy_without_cash = observation(bid_qty=0, ask_qty=100, cash=0, shares=10)
    assert deterministic_intent("passive_quote", ask_heavy_without_cash, seed=41)["action"] == "SELL"
    bid_heavy_without_inventory = observation(bid_qty=100, ask_qty=0, cash=100000, shares=0)
    assert deterministic_intent("passive_quote", bid_heavy_without_inventory, seed=41)["action"] == "BUY"


def test_quote_and_random_policies_share_economic_initialization():
    base = Path("examples/experiments/scientific_s5_no_claim_m1_s1.yaml")
    a = _config(base, policy="random", ttl=3, seed=41, steps=8, n_agents=12)
    b = _config(base, policy="passive_quote", ttl=3, seed=41, steps=8, n_agents=12)
    x, y = a.model_dump(mode="json"), b.model_dump(mode="json")
    x.pop("name"); y.pop("name")
    x["cognitive"].pop("decision_policy")
    y["cognitive"].pop("decision_policy")
    assert x == y
    assert a.cognitive.decision_interval == b.cognitive.decision_interval == 1
    assert a.llm.max_calls == b.llm.max_calls == 0
    assert a.decision_mode == b.decision_mode == "cognitive_rule"


def test_actual_quote_supply_worlds_and_guardrail_outputs(tmp_path):
    root = tmp_path / "quote-study"
    result = run_development(
        root,
        template_path=Path("examples/experiments/scientific_s5_no_claim_m1_s1.yaml"),
        ttls=[1, 3], seeds=[41], steps=8, n_agents=12,
    )
    assert result["finished_runs"] == 4
    assert result["measured_worlds"] == 1
    assert result["model_api_calls"] == 0
    assert result["paid_llm_ready"] is False
    assert result["market_realism_verified"] is False
    assert result["heldout_seeds_used"] is False
    assert result["candidate_ttl_with_passive_quotes"] in (None, 1, 3)
    runs = pd.read_csv(root / "quote_supply_per_run.csv")
    assert set(runs.policy) == set(POLICIES)
    assert set(runs.ttl) == {1, 3}
    assert len(runs) == 4
    assert runs.reported_llm_calls.eq(0).all()
    assert runs.orders_recorded.gt(0).all()
    for row in runs.itertuples(index=False):
        assert verify_run(row.path)["status"] == "verified"
        assert row.two_sided_quote_share >= 0
        assert row.order_rejection_share >= 0
    diffs = pd.read_csv(root / "paired_policy_differences.csv")
    assert len(diffs) == 2 * len(COMPARE)
    assert set(diffs.metric) == set(COMPARE)
    assert set(pd.read_csv(root / "policy_effect_summary.csv").inference) == {"descriptive_only"}
    assert json.loads((root / "decision.json").read_text()) == result
    assert len(pd.read_csv(root / "policy_guardrails_by_world.csv")) == 2
    with pytest.raises(ValueError, match="overwrite"):
        run_development(
            root, template_path=Path("examples/experiments/scientific_s5_no_claim_m1_s1.yaml"),
            ttls=[1, 3], seeds=[41], steps=8, n_agents=12,
        )


def test_passive_quote_run_is_deterministically_reproducible(tmp_path):
    from core.experiments.runner import ExperimentRunner
    config = _config(
        Path("examples/experiments/scientific_s5_no_claim_m1_s1.yaml"),
        policy="passive_quote", ttl=3, seed=41, steps=8, n_agents=12,
    )
    run = ExperimentRunner(config).run(tmp_path / "run")
    result = reproduce_run(run, tmp_path / "replayed")
    assert result["status"] == "matched" and result["compared_files"] == 22


def test_invalid_policy_is_rejected_before_creating_market():
    with pytest.raises(ValueError, match="Unsupported"):
        _config(
            Path("examples/experiments/scientific_s5_no_claim_m1_s1.yaml"),
            policy="magic_market_price", ttl=1, seed=41,
            steps=8, n_agents=12,
        )
