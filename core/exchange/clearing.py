"""Closed, single-asset cash market settlement around the existing order book.

No credit, short selling, hidden liquidity or external cash injections. Fees go
to an explicit sink, so cash + fees and total shares are conserved separately.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from core.types import Order, OrderSide, OrderStatus, Trade


@dataclass
class Account:
    cash: float
    shares: int
    acquired_today: int = 0
    cost_basis: float = 0.0


class ClearingHouse:
    def __init__(self, book, accounts: dict[str, Account], *, t_plus_one: bool = False):
        self.book = book
        self.accounts = accounts
        if not accounts or any(not math.isfinite(a.cash) or a.cash < 0 or
                               not isinstance(a.shares, int) or isinstance(a.shares, bool) or a.shares < 0 or
                               not math.isfinite(a.cost_basis) or a.cost_basis < 0 or
                               not isinstance(a.acquired_today, int) or a.acquired_today < 0
                               for a in accounts.values()):
            raise ValueError("Accounts require finite non-negative cash/cost and integer shares")
        self.t_plus_one = t_plus_one
        self.fees = 0.0
        self.orders: dict[str, Order] = {}
        self.initial_cash = math.fsum(a.cash for a in accounts.values())
        self.initial_shares = sum(a.shares for a in accounts.values())

    def active_orders(self, agent_id: str | None = None):
        return [o for o in self.orders.values() if
                o.status in {OrderStatus.PENDING, OrderStatus.PARTIAL} and o.remaining_qty > 0
                and (agent_id is None or o.agent_id == agent_id)]

    def available(self, agent_id: str) -> tuple[float, int]:
        account = self.accounts[agent_id]
        orders = self.active_orders(agent_id)
        reserved_cash = math.fsum(o.remaining_qty * o.price * (1 + self.book.market_rules.commission_rate)
                                  for o in orders if o.side == OrderSide.BUY)
        reserved_shares = sum(o.remaining_qty for o in orders if o.side == OrderSide.SELL)
        locked = account.acquired_today if self.t_plus_one else 0
        return account.cash - reserved_cash, account.shares - reserved_shares - locked

    def submit(self, order: Order) -> list[Trade]:
        if order.order_id in self.orders:
            raise ValueError(f"Duplicate order_id: {order.order_id}")
        # Normalize once before reserving cash: MARKET prices become price-limit bounds.
        if not self.book._preprocess_order(order):
            self.orders[order.order_id] = order
            return []
        reason = ""
        if order.agent_id not in self.accounts:
            reason = "unknown account"
        else:
            cash, shares = self.available(order.agent_id)
            if order.side == OrderSide.BUY and order.value * (1 + self.book.market_rules.commission_rate) > cash + 1e-8:
                reason = "insufficient unreserved cash"
            elif order.side == OrderSide.SELL and order.quantity > shares:
                reason = "insufficient sellable unreserved shares"
            for other in self.active_orders(order.agent_id):
                if other.side != order.side and ((order.side == OrderSide.BUY and order.price >= other.price)
                                                or (order.side == OrderSide.SELL and order.price <= other.price)):
                    reason = "self-cross prevention"
        if reason:
            order.status, order.reason = OrderStatus.REJECTED, reason
            self.orders[order.order_id] = order
            return []
        self.orders[order.order_id] = order
        trades = self.book.add_order(order)
        for trade in trades:
            buyer = self.accounts[trade.buyer_agent_id]
            seller = self.accounts[trade.seller_agent_id]
            cost, proceeds = trade.buyer_total_cost, trade.seller_net_proceeds
            if buyer is seller or buyer.cash + 1e-8 < cost or seller.shares < trade.quantity:
                raise RuntimeError("Settlement invariant violated before account mutation")
            buyer.cash -= cost
            buyer.cost_basis = (buyer.cost_basis * buyer.shares + cost) / (buyer.shares + trade.quantity)
            buyer.shares += trade.quantity
            buyer.acquired_today += trade.quantity
            seller.cash += proceeds
            seller.shares -= trade.quantity
            if seller.shares == 0:
                seller.cost_basis = 0.0
            self.fees += trade.buyer_fee + trade.seller_fee + trade.seller_tax
        self.check_invariants()
        return trades

    def cancel(self, order_id: str) -> bool:
        return self.book.cancel_order(order_id)

    def new_day(self) -> None:
        for account in self.accounts.values():
            account.acquired_today = 0

    def check_invariants(self) -> None:
        if any(not math.isfinite(a.cash) or a.cash < -1e-7 or a.shares < 0 or
               not math.isfinite(a.cost_basis) or a.cost_basis < 0 for a in self.accounts.values()):
            raise RuntimeError("Negative or non-finite account balance")
        if sum(a.shares for a in self.accounts.values()) != self.initial_shares:
            raise RuntimeError("Share conservation violated")
        if not math.isclose(math.fsum(a.cash for a in self.accounts.values()) + self.fees,
                            self.initial_cash, rel_tol=1e-12, abs_tol=1e-7):
            raise RuntimeError("Cash plus fee-sink conservation violated")
        for agent_id in self.accounts:
            cash, shares = self.available(agent_id)
            if cash < -1e-7 or shares < 0:
                raise RuntimeError(f"Over-reserved account: {agent_id}")
