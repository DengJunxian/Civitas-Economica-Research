"""Microstructure sensitivity regression tests; zero external model calls."""
import json
import math

import pandas as pd
import pytest

from core.experiments.verify import verify_run
from research.civitas_agent.liquidity_sweep import (
    GATES, METRICS, _build_control, _check_values, _passes,
    diagnose, run_development,
)


@pytest.fixture
def no_news_template():
    from pathlib import Path
    return Path("examples/experiments/scientific_s5_no_claim_m1_s1.yaml")


def test_ttl_is_only_market_setting_varied_and_no_provider_is_enabled(no_news_template):
    a = _build_control(no_news_template, ttl=1, seed=41, steps=8, n_agents=12)
    b = _build_control(no_news_template, ttl=3, seed=41, steps=8, n_agents=12)
    assert a.market.order_ttl == 1 and b.market.order_ttl == 3
    first, second = a.model_dump(mode="json"), b.model_dump(mode="json")
    first.pop("name"); second.pop("name")
    first["market"].pop("order_ttl"); second["market"].pop("order_ttl")
    assert first == second
    assert a.decision_mode == "cognitive_rule"
    assert a.cognitive.decision_policy == "random"
    assert not a.cognitive.memory_enabled and not a.cognitive.social_enabled
    assert a.scientific_world.enabled and not a.scientific_world.shocks
    assert a.llm.model == "" and a.llm.max_calls == 0


def test_paired_ttl_engineering_smoke_preserves_accounts_and_archives(
    tmp_path, no_news_template,
):
    out = tmp_path / "diagnostic"
    result = run_development(
        out, template_path=no_news_template,
        ttls=[1, 3], seeds=[41], steps=8, n_agents=12,
    )
    assert result["recorded_runs"] == 2
    assert result["evaluated_independent_worlds"] == 1
    assert result["paid_llm_ready"] is False
    assert result["heldout_touched"] is False
    assert result["development_status"] in {
        "DEVELOPMENT_CANDIDATE_UNVALIDATED",
        "NO_ACCEPTABLE_TTL_IN_DEVELOPMENT",
    }
    table = pd.read_csv(out / "liquidity_per_run.csv")
    assert len(table) == 2 and set(table.ttl) == {1, 3}
    assert table.reported_llm_calls.eq(0).all()
    assert table.orders_recorded.gt(0).all()
    assert (table.total_volume >= 0).all()
    for x in table.itertuples(index=False):
        assert (
            x.two_sided_quote_share + x.bid_only_share +
            x.ask_only_share + x.empty_book_share
        ) == pytest.approx(1)
        assert (x.mean_spread_pct_when_quoted is None
                or math.isnan(x.mean_spread_pct_when_quoted)
                or x.mean_spread_pct_when_quoted >= 0)
        assert x.order_rejection_share >= 0
        assert x.ttl_cancellation_share >= 0
        assert verify_run(x.run_dir)["status"] == "verified"
        assert diagnose(__import__("pathlib").Path(x.run_dir))["reported_llm_calls"] == 0
    effects = pd.read_csv(out / "paired_ttl_differences.csv")
    assert len(effects) == len(METRICS)
    assert set(effects.ttl) == {3}
    assert set(effects.metric) == set(METRICS)
    assert set(pd.read_csv(out / "ttl_effect_summary.csv").status) == {"descriptive_only"}
    assert (out / "decision.json").exists()
    assert json.loads((out / "decision.json").read_text()) == result
    with pytest.raises(ValueError, match="existing"):
        run_development(
            out, template_path=no_news_template,
            ttls=[1, 3], seeds=[41], steps=8, n_agents=12,
        )


def test_quality_gates_do_not_grant_llm_readiness():
    very_bad = {
        "two_sided_quote_share": 0.04,
        "active_step_share": 0.56,
        "stale_price_share": 0.44,
    }
    passed, failures = _passes(very_bad)
    assert not passed and failures == ["LOW_TWO_SIDED_QUOTES"]
    good = {
        "two_sided_quote_share": GATES["min_two_sided_quote_share"],
        "active_step_share": GATES["min_active_step_share"],
        "stale_price_share": GATES["max_stale_price_share"],
    }
    assert _passes(good) == (True, [])


@pytest.mark.parametrize("ttls,seeds", [
    ([1, 1], [41]),
    ([3, 1], [41]),
    ([1, 0], [41]),
    ([1, 101], [41]),
    ([1, 3], [41, 41]),
    ([1, 3], [-1]),
    ([], [41]),
    ([1], []),
])
def test_sweep_refuses_invalid_or_duplicate_controlled_conditions(ttls, seeds):
    with pytest.raises(ValueError):
        _check_values(ttls, seeds, steps=8, n_agents=12)


def test_experiment_refuses_non_no_news_template(tmp_path):
    from pathlib import Path
    with pytest.raises(ValueError, match="no-news"):
        run_development(
            tmp_path / "invalid",
            template_path=Path("examples/experiments/scientific_s5_m1_s1.yaml"),
            ttls=[1, 3], seeds=[41], steps=8, n_agents=12,
        )
