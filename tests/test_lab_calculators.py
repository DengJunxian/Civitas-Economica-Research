"""Formula-level checks for the deterministic teaching calculators (plan 10.1).

Each expected value comes from a closed form or a hand calculation, never from
the implementation under test.
"""
import math

import pytest

from lab.calculators import (BondParams, BudgetParams, bond_lab, bond_measures, bond_price, bond_yield, budget_lab,
                             compute, irr, npv, payback, sign_changes)


@pytest.mark.parametrize("years, frequency, rate", [(1, 1, 0.03), (10, 1, 0.05), (30, 2, 0.07), (5, 4, 0.02)])
def test_par_bond_prices_at_face(years, frequency, rate):
    assert bond_price(BondParams(coupon_rate=rate, yield_rate=rate, years=years, frequency=frequency)) == pytest.approx(100, abs=1e-9)


@pytest.mark.parametrize("years, frequency", [(5, 1), (20, 2)])
def test_zero_coupon_duration_equals_maturity(years, frequency):
    p = BondParams(coupon_rate=0.0, yield_rate=0.06, years=years, frequency=frequency)
    m = bond_measures(p)
    assert m["macaulay_duration"] == pytest.approx(years, abs=1e-12)
    assert m["price"] == pytest.approx(100 / (1 + 0.06 / frequency) ** (years * frequency))


def test_par_bond_duration_closed_form():
    y, n = 0.08, 12
    p = BondParams(coupon_rate=y, yield_rate=y, years=n)
    assert bond_measures(p)["macaulay_duration"] == pytest.approx((1 + y) / y * (1 - (1 + y) ** -n), rel=1e-12)


def test_textbook_three_year_bond():
    # 10/1.12 + 10/1.12^2 + 110/1.12^3, durations by hand.
    p = BondParams(coupon_rate=0.10, yield_rate=0.12, years=3)
    pv = [10 / 1.12, 10 / 1.12 ** 2, 110 / 1.12 ** 3]
    m = bond_measures(p)
    assert m["price"] == pytest.approx(sum(pv), rel=1e-12) and round(m["price"], 4) == 95.1963
    assert m["macaulay_duration"] == pytest.approx((pv[0] + 2 * pv[1] + 3 * pv[2]) / sum(pv), rel=1e-12)
    assert m["modified_duration"] == pytest.approx(m["macaulay_duration"] / 1.12, rel=1e-12)


def test_duration_and_convexity_match_numerical_derivatives():
    p = BondParams(coupon_rate=0.045, yield_rate=0.052, years=15, frequency=2)
    m, price = bond_measures(p), bond_price(p)
    h = 1e-5
    up, down = bond_price(p, p.yield_rate + h), bond_price(p, p.yield_rate - h)
    assert m["modified_duration"] == pytest.approx(-(up - down) / (2 * h) / price, rel=1e-7)
    assert m["convexity"] == pytest.approx((up - 2 * price + down) / h ** 2 / price, rel=1e-4)
    assert m["dv01"] == pytest.approx((down - up) / 2 / (h / 1e-4), rel=1e-6)


def test_yield_inverts_price():
    p = BondParams(coupon_rate=0.03, yield_rate=0.0475, years=7, frequency=2)
    assert bond_yield(p, bond_price(p)) == pytest.approx(0.0475, abs=1e-12)
    with pytest.raises(ValueError):
        bond_yield(p, -1.0)


def test_convexity_improves_duration_estimate():
    result = bond_lab(BondParams(coupon_rate=0.02, yield_rate=0.04, years=20))
    for row in result["shocks"]:
        # Option-free bonds are convex: the duration line always lies below the price curve.
        assert row["exact_change"] >= row["duration_estimate"]
        assert abs(row["duration_convexity_error"]) < abs(row["duration_error"])
    assert all(r["price"] >= r["duration_line"] - 1e-9 for r in result["price_yield_curve"])


def test_npv_irr_profitability_index_by_hand():
    flows = (-100, 60, 60)
    assert npv(0.10, flows) == pytest.approx(-100 + 60 / 1.1 + 60 / 1.21, abs=1e-12)
    x = (-60 + math.sqrt(60 ** 2 + 4 * 60 * 100)) / (2 * 60)  # 60x^2 + 60x - 100 = 0, x = 1/(1+r)
    assert irr(flows) == [pytest.approx(1 / x - 1, abs=1e-10)]
    project = budget_lab(BudgetParams(projects=[{"name": "A", "cash_flows": flows}]))["projects"][0]
    assert project["profitability_index"] == pytest.approx((60 / 1.1 + 60 / 1.21) / 100, rel=1e-12)
    assert project["decision"] == "accept" and project["irr_unique"]


def test_multiple_irrs_are_all_reported():
    flows = (-100, 230, -132)  # -100(1+r)^2 + 230(1+r) - 132 = 0  ->  1+r = 1.1 or 1.2
    assert irr(flows) == [pytest.approx(0.1, abs=1e-10), pytest.approx(0.2, abs=1e-10)]
    assert sign_changes(flows) == 2
    result = budget_lab(BudgetParams(projects=[{"name": "M", "cash_flows": flows}]))
    assert not result["projects"][0]["irr_unique"] and result["best_by_irr"] is None


def test_payback_and_crossover_rate():
    assert payback((-100, 30, 40, 50)) == pytest.approx(2 + 30 / 50)
    assert payback((-100, 10, 10)) is None
    assert payback((-100, 30, 40, 50), 0.10) is None  # discounted inflows sum to 97.9 < 100
    discounted = payback((-100, 30, 40, 50), 0.05)
    assert discounted is not None and discounted > 2.6
    # A - B = (0, -90, 95): -90/(1+r) + 95/(1+r)^2 = 0  ->  r = 95/90 - 1
    result = budget_lab(BudgetParams(discount_rate=0.03, projects=[{"name": "A", "cash_flows": (-100, 10, 110)},
                                                                   {"name": "B", "cash_flows": (-100, 100, 15)}]))
    assert result["crossover_rates"][0]["rates"] == [pytest.approx(95 / 90 - 1, abs=1e-10)]
    # Below the crossover the long project wins on NPV while the short one has the higher IRR.
    assert result["best_by_npv"] == "A" and result["best_by_irr"] == "B"


def test_compute_provenance_and_validation():
    first = compute("bond", {"coupon_rate": 0.04, "yield_rate": 0.05, "years": 5})
    again = compute("bond", {"years": 5, "yield_rate": 0.05, "coupon_rate": 0.04})
    assert first == again and first["origin"] == "deterministic_calculation" and len(first["params_hash"]) == 64
    with pytest.raises(ValueError):
        compute("capital_budgeting", {"projects": [{"name": "bad", "cash_flows": [100, -50]}]})
    with pytest.raises(ValueError):
        compute("bond", {"years": 5, "unknown": 1})


def test_npv_rule_is_indifferent_at_the_irr():
    result = budget_lab(BudgetParams(discount_rate=0.10, projects=[{"name": "A", "cash_flows": (-100, 10, 110)}]))
    assert result["projects"][0]["decision"] == "indifferent"  # NPV is zero up to rounding at r = IRR = 10%
