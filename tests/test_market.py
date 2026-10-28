from dataclasses import asdict
import importlib.util

import pytest

from core.exchange.clearing import Account, ClearingHouse
from core.exchange.market_rules import MarketRuleSchema
from core.exchange.order_book import OrderBook
from core.types import Order, OrderSide, OrderType, OrderStatus


def make(oid, side="buy", price=100.0, qty=1, agent=None, kind="limit", timestamp=1.0):
    return Order("X", price, qty, OrderSide(side), OrderType(kind), agent or oid, timestamp, order_id=oid)


def book(cls=OrderBook, **kwargs):
    return cls("X", 100, market_rules=MarketRuleSchema("X", 100,
        feature_flags={"market_rules_v1": True, "strict_queue_timestamps": True}, **kwargs))


def test_resting_price_and_arrival_priority():
    b = book()
    b.add_order(make("z-first", "sell", 100, 2))
    b.add_order(make("a-second", "sell", 100, 2))
    trades = b.add_order(make("0-buy", price=101, qty=3))
    assert [(t.price, t.quantity, t.seller_agent_id) for t in trades] == [(100, 2, "z-first"), (100, 1, "a-second")]
    assert all(t.timestamp == 1 for t in trades)
    assert b.get_depth()["asks"] == [{"price": 100, "qty": 1}]


@pytest.mark.parametrize("kind", ["market", "ioc"])
def test_immediate_order_remainder_terminates(kind):
    b = book(); o = make("o", kind=kind)
    assert b.add_order(o) == []
    assert o.status == OrderStatus.CANCELLED
    b.add_order(make("seller", "sell", qty=1))
    o = make("buyer", qty=2, kind=kind)
    assert len(b.add_order(o)) == 1
    assert o.status == OrderStatus.CANCELLED and o.filled_qty == 1


@pytest.mark.parametrize("price,qty", [(float("nan"), 1), (float("inf"), 1), (0, 1), (-1, 1), (100, 0), (100, -1), (100, 1.5), (100, True), (120, 1)])
def test_invalid_orders_rejected(price, qty):
    b = book(); o = make("o", price=price, qty=qty)
    assert b.add_order(o) == []
    assert o.status == OrderStatus.REJECTED
    assert b.get_depth() == {"bids": [], "asks": []}


def test_invalid_limit_price_and_symbol_have_auditable_rejection_reasons():
    b = book()
    outside = make("outside", side="sell", price=89.99)
    assert b.add_order(outside) == []
    assert outside.status == OrderStatus.REJECTED
    assert outside.reason == "price outside limit"
    wrong = Order(
        "WRONG", 100.0, 2, OrderSide.BUY, OrderType.LIMIT,
        "agent_wrong", 1.0, order_id="wrong"
    )
    assert b.add_order(wrong) == []
    assert wrong.status == OrderStatus.REJECTED
    assert wrong.reason == "symbol mismatch"
    assert b.get_depth() == {"bids": [], "asks": []}


def test_duplicate_and_cancel_are_safe():
    b = book(); o = make("o")
    b.add_order(o)
    with pytest.raises(ValueError, match="Duplicate"):
        b.add_order(make("o"))
    assert b.cancel_order("o")
    assert not b.cancel_order("o")
    assert b.cancelled_orders == 1


def test_new_day_price_limits_follow_new_close():
    b = book()
    b.update_prev_close(200)
    assert b.get_limit_prices() == (180, 220)
    order = make("new_day", price=200)
    b.add_order(order)
    assert order.status == OrderStatus.PENDING


def test_legacy_market_adapter_preserves_order_status():
    from core.market_engine import MatchingEngine
    b = MatchingEngine("X", 100)
    o = make("bad", price=120)
    assert b.submit_order(o) == []
    assert o.status == OrderStatus.REJECTED
    a = make("ask", "sell", qty=2)
    b.submit_order(a)
    trades = b.submit_order(make("bid", qty=2))
    assert a.status == OrderStatus.FILLED
    assert trades[0].price == 100


def test_fok_post_only_and_lots():
    b = book(enforce_board_lot=True, allow_odd_lots=False, board_lot=100)
    small = make("small", qty=1); b.add_order(small)
    assert small.status == OrderStatus.REJECTED
    b.add_order(make("sell", "sell", qty=100))
    fok = make("fok", qty=200, kind="fok"); b.add_order(fok)
    post = make("post", qty=100, kind="post-only"); b.add_order(post)
    assert fok.status == post.status == OrderStatus.REJECTED
    assert b.total_volume == 0
    assert b.add_order(make("filled-fok", qty=100, kind="fok"))[0].quantity == 100


def test_settlement_reservation_fees_and_t_plus_one():
    c = ClearingHouse(book(), {"buyer": Account(1000, 0), "seller": Account(0, 10)}, t_plus_one=True)
    first = make("bid", qty=8, agent="buyer"); c.submit(first)
    rejected = make("overbid", qty=4, agent="buyer"); c.submit(rejected)
    assert rejected.status == OrderStatus.REJECTED
    trades = c.submit(make("ask", "sell", qty=8, agent="seller"))
    assert len(trades) == 1
    assert c.accounts["buyer"].shares == 8 and c.accounts["seller"].shares == 2
    assert sum(a.cash for a in c.accounts.values()) + c.fees == pytest.approx(1000)
    sell_today = make("sell_today", "sell", qty=8, agent="buyer"); c.submit(sell_today)
    assert sell_today.status == OrderStatus.REJECTED
    c.new_day()
    sell_next = make("sell_next", "sell", qty=8, agent="buyer"); c.submit(sell_next)
    assert sell_next.status == OrderStatus.PENDING
    c.check_invariants()


def test_cancel_releases_resources_and_no_short_sell():
    c = ClearingHouse(book(), {"a": Account(1000, 10)})
    c.submit(make("o", qty=5, agent="a"))
    assert c.available("a")[0] < 500
    c.cancel("o")
    assert c.available("a") == (1000, 10)
    c.submit(make("s", "sell", qty=10, agent="a"))
    bad = make("s2", "sell", qty=1, agent="a"); c.submit(bad)
    assert bad.status == OrderStatus.REJECTED
    own = make("own", qty=1, agent="a"); c.submit(own)
    assert own.reason == "self-cross prevention"
    c.check_invariants()


@pytest.mark.skipif(importlib.util.find_spec("_civitas_lob") is None, reason="optional C++ kernel not built")
def test_cpp_python_parity():
    from core.exchange.order_book_cpp import OrderBookCPP
    results = []
    for cls in (OrderBook, OrderBookCPP):
        b = book(cls)
        trace = []
        orders = [make("z", "sell", qty=2), make("a", "sell", qty=3),
                  make("buy", price=101, qty=4), make("ioc", price=100, qty=3, kind="ioc"),
                  make("post", price=99, qty=5, kind="post-only")]
        for o in orders:
            trace.append([asdict(t) for t in b.add_order(o)])
        assert b.cancel_order("post")
        results.append((trace, [(o.status, o.filled_qty) for o in orders], b.get_depth(), b.get_activity_stats()))
    assert results[0] == results[1]


@pytest.mark.skipif(importlib.util.find_spec("_civitas_lob") is None, reason="optional C++ kernel not built")
def test_cpp_python_randomized_order_and_cancel_trace():
    import random
    from core.exchange.order_book_cpp import OrderBookCPP
    rng = random.Random(1984)
    books = [book(OrderBook), book(OrderBookCPP)]
    for i in range(200):
        params = dict(side=rng.choice(["buy", "sell"]), price=rng.choice([99, 100, 101]),
                      qty=rng.randint(1, 12), timestamp=float(rng.randint(1, 8)),
                      kind=rng.choice(["limit", "ioc", "fok", "post-only"]))
        outputs = []
        for b in books:
            o = make(str(i), **params)
            outputs.append(([asdict(t) for t in b.add_order(o)], o.status, o.filled_qty, b.get_depth()))
        assert outputs[0] == outputs[1]
        if i % 7 == 0:
            target = str(rng.randrange(i + 1))
            assert books[0].cancel_order(target) == books[1].cancel_order(target)


def test_rejected_amendment_preserves_resting_order():
    b = book()
    resting = make("resting", qty=10)
    b.add_order(resting)
    for kwargs in ({"quantity": 1.5}, {"price": 200}, {"quantity": 0}):
        amendment = b.modify_order("resting", timestamp=2, **kwargs)
        assert amendment.status == OrderStatus.REJECTED
        assert resting.status == OrderStatus.PENDING
        assert b.get_depth()["bids"] == [{"price": 100, "qty": 10}]
    amendment = b.modify_order("resting", price=99, quantity=4, timestamp=2)
    assert resting.status == OrderStatus.CANCELLED
    b.add_order(amendment)
    assert b.get_depth()["bids"] == [{"price": 99, "qty": 4}]


@pytest.mark.skipif(importlib.util.find_spec("_civitas_lob") is None, reason="optional C++ kernel not built")
@pytest.mark.parametrize("tick,previous,ask,bid", [(0.05, 100.03, 100.025, 100.075),
                                                   (1e-7, 0.001, 0.00100015, 0.00100105)])
def test_cpp_price_precision_and_queue_parity(tick, previous, ask, bid):
    from core.exchange.order_book_cpp import OrderBookCPP
    traces = []
    for cls in (OrderBook, OrderBookCPP):
        rules = MarketRuleSchema("X", previous, min_price_tick=tick,
            feature_flags={"market_rules_v1": True, "strict_queue_timestamps": True})
        b = cls("X", previous, market_rules=rules)
        first = make("z-first", "sell", ask, 3, timestamp=1.2345675)
        second = make("a-second", "sell", ask, 2, timestamp=1.2345675)
        b.add_order(first); b.add_order(second)
        assert b.estimate_queue_position(first) == 0
        assert b.estimate_queue_position(second) == 3
        trades = b.add_order(make("buy", price=bid, qty=4, timestamp=2))
        traces.append(([asdict(t) for t in trades], b.get_depth(), b.get_limit_prices()))
        assert sum(t.quantity for t in trades) == 4
        assert all(t.price == rules.quantize_price(ask) for t in trades)
    assert traces[0] == traces[1]


def test_cash_reservations_update_weighted_cost_basis():
    c = ClearingHouse(book(), {"buyer": Account(1000, 2, cost_basis=50), "seller": Account(0, 8, cost_basis=60)})
    c.submit(make("ask", "sell", qty=8, agent="seller"))
    trade = c.submit(make("buy", qty=4, agent="buyer"))[0]
    assert c.accounts["buyer"].cost_basis == pytest.approx((100 + trade.buyer_total_cost) / 6)
    assert c.accounts["seller"].cost_basis == 60
    c.check_invariants()
