"""Standard experiment lifecycle composing existing Civitas mechanisms."""
from __future__ import annotations

import asyncio
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import platform
import random
import subprocess
import traceback
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from agents.persona import PersonaGenerator
from agents.population import StratifiedPopulation
from core.exchange.clearing import Account, ClearingHouse
from core.exchange.market_rules import MarketRuleSchema
from core.exchange.order_book import OrderBook
from core.experiment_manifest import ExperimentManifest, write_experiment_manifest
from core.experiments.config import ExperimentConfig
from core.market_metrics import MarketMetrics
from core.reproducibility import stable_hash, utc_now_iso
from core.society.network import InformationDiffusion, SocialGraph, SentimentState
from core.types import Order, OrderSide, OrderStatus, OrderType

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True,
                              allow_nan=False, default=str), encoding="utf-8")
    tmp.replace(path)


def source_fingerprint() -> dict[str, Any]:
    paths = list(PROJECT_ROOT.glob("*.py")) + list(PROJECT_ROOT.glob("config.*"))
    for directory in ("agents", "core", "engine", "policy", "data_flywheel", "research"):
        paths.extend((PROJECT_ROOT / directory).rglob("*.py"))
    paths.extend((PROJECT_ROOT / "core/exchange/c_core").glob("*"))
    paths.extend((PROJECT_ROOT / "data").glob("market_composition.*"))
    files = {str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(set(paths)) if p.is_file()}
    state = subprocess.run(["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
                           capture_output=True, text=True)
    return {"sha256": stable_hash(files), "files": files, "git_dirty": bool(state.stdout.strip())}


class CsvSink:
    def __init__(self, path: Path, columns: list[str]):
        self.file = path.open("w", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.file, fieldnames=columns)
        self.writer.writeheader()

    def add(self, row: dict[str, Any]) -> None:
        self.writer.writerow(row)

    def flush(self) -> None:
        self.file.flush()

    def close(self) -> None:
        self.file.close()


class ExperimentRunner:
    """One run owns its RNGs, world, ledger and unique output directory.

    The default is the existing vectorized behavioral model. The synthetic
    population is a model initial condition; all output prices are actual LOB
    executions (or the last price carried forward when there are no trades).
    """

    def __init__(self, config: ExperimentConfig, *, parent_run_id: str = ""):
        self.config = config
        self.parent_run_id = parent_run_id
        self.llm_router = None

    def _initialize(self, output: Path):
        cfg = self.config
        self.price = cfg.market.initial_price
        input_info = {"origin": "model_initial_condition", "historical_price_use": "none"}
        if cfg.historical_input:
            source = Path(cfg.historical_input.path)
            raw = source.read_bytes()
            if hashlib.sha256(raw).hexdigest() != cfg.historical_input.sha256:
                raise ValueError("Historical input snapshot hash mismatch")
            with source.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            if not rows or "close" not in rows[0]:
                raise ValueError("Input snapshot requires non-empty CSV with close column")
            prices = [float(row["close"]) for row in rows]
            if any(not math.isfinite(p) or p <= 0 for p in prices):
                raise ValueError("Snapshot close prices must be finite and positive")
            self.price = prices[-1]
            (output / "input_snapshot.csv").write_bytes(raw)
            input_info = {**cfg.historical_input.model_dump(exclude={"path"}), "historical_price_use": "initial_price_only"}
        if self.price < cfg.market.tick_size:
            raise ValueError("Initial snapshot price is smaller than one price tick")
        self.population = StratifiedPopulation(n_smart=0, n_vectorized=cfg.n_agents,
            seed=cfg.seed, market_regime=cfg.market_regime,
            feature_flags={"population_protocol_v1": True})
        if cfg.market_regime not in self.population.market_composition.get("regimes", {}):
            raise ValueError(f"Unknown market_regime: {cfg.market_regime}")
        self.personas = PersonaGenerator.generate_distribution(cfg.n_agents, seed=cfg.seed,
                                                               regime=cfg.market_regime, protocol_v1=True)
        # Financial ledger uses float64. Population matrix remains the existing
        # vectorized behavioral state, synchronized from authoritative accounts.
        self.ids = [f"agent_{i:06d}" for i in range(cfg.n_agents)]
        accounts = {aid: Account(float(row[0]), int(row[1]), cost_basis=float(row[2]))
                    for aid, row in zip(self.ids, self.population.state)}
        rules = MarketRuleSchema(symbol=cfg.market.symbol, prev_close=self.price,
            price_limit_pct=cfg.market.price_limit, min_price_tick=cfg.market.tick_size,
            board_lot=cfg.market.board_lot, enforce_board_lot=True,
            allow_odd_lots=cfg.market.board_lot == 1,
            commission_rate=cfg.market.commission, stamp_duty_rate=cfg.market.stamp_duty,
            t_plus_one=cfg.market.t_plus_one,
            feature_flags={"market_rules_v1": True, "strict_queue_timestamps": True},
            metadata={"session_model": "discrete_continuous_auction", "holiday_calendar": "none"})
        book_cls = OrderBook
        if cfg.market.backend == "cpp":
            from core.exchange.order_book_cpp import OrderBookCPP
            book_cls = OrderBookCPP
        self.book = book_cls(symbol=cfg.market.symbol, prev_close=self.price, market_rules=rules)
        self.clearing = ClearingHouse(self.book, accounts, t_plus_one=cfg.market.t_plus_one)
        k = 0 if cfg.social.topology == "empty" else cfg.social.neighbors
        p = cfg.social.rewire_probability if cfg.social.topology == "small_world" else 0.0
        self.social = SocialGraph(n_agents=cfg.n_agents, k=k, p=p, seed=cfg.seed)
        self.diffusion = InformationDiffusion(self.social, beta=cfg.social.beta,
                                              gamma=cfg.social.gamma, delta=cfg.social.delta)
        for i, persona in enumerate(self.personas):
            node = self.social.agents[i]
            node.agent_id = self.ids[i]
            node.conformity = persona.conformity
            node.base_lambda = node.lambda_coeff = persona.loss_aversion
            node.influence = persona.influence
        self.diffusion.inject_panic(cfg.social.initial_infected)
        # Existing vectorized sentiment updater consumes a rectangular neighbor
        # matrix. Cycle-pad each adjacency list; isolated nodes follow themselves.
        width = max(1, max(dict(self.social.graph.degree()).values(), default=0))
        neighbors = []
        for i in range(cfg.n_agents):
            adjacent = sorted(self.social.get_neighbors(i)) or [i]
            neighbors.append([adjacent[j % len(adjacent)] for j in range(width)])
        self.population.neighbor_network = np.asarray(neighbors)
        self.population.influence_map = np.arange(cfg.n_agents)
        self.halted = False
        self.order_steps: dict[str, int] = {}
        self.previous_price = self.price
        profiles = [{"agent_id": aid, "persona": p.to_dict(), "cognitive_type": int(row[4]),
                     "initial_cash": float(row[0]), "initial_shares": int(row[1]),
                     "initial_cost_anchor": float(row[2])}
                    for aid, p, row in zip(self.ids, self.personas, self.population.state)]
        world = {"initial_price": self.price, "population_origin": "synthetic_model_population",
                 "input": input_info, "market_rules": rules.to_dict(),
                 "social_edges": sorted([list(edge) for edge in self.social.graph.edges()]),
                 "social_profiles": [asdict(node) for node in self.social.agents.values()]}
        if cfg.decision_mode in {"cognitive_llm", "cognitive_rule"}:
            from research.civitas_agent import CognitiveProtocol
            adjacency = {
                aid: [self.ids[j] for j in sorted(self.social.get_neighbors(i))]
                for i, aid in enumerate(self.ids)
            }
            self.cognitive_protocol = CognitiveProtocol(
                config=cfg.cognitive, agent_ids=self.ids, neighbors=adjacency,
                seed=cfg.seed, initial_price=self.price, total_steps=cfg.steps,
            )
            if cfg.scientific_world.enabled:
                from research.civitas_agent.scientific_world import SyntheticInformationWorld
                self.scientific_world = SyntheticInformationWorld(
                    config=cfg.scientific_world,
                    seed=cfg.seed,
                    initial_price=self.price,
                    active_ids=self.cognitive_protocol.active_ids,
                    neighbors=adjacency,
                    social_enabled=cfg.cognitive.social_enabled,
                    social_mode=cfg.cognitive.social_mode,
                    total_steps=cfg.steps,
                )
                world["scientific_world"] = {
                    "enabled": True,
                    "source_type": "synthetic_controlled",
                    "truth_is_agent_private": True,
                    "latent_value_used_for_matching": False,
                }
        write_json(output / "population.json", profiles)
        write_json(output / "world.json", world)
        return {"world": world, "population": profiles}

    async def _decisions(self, step: int):
        cfg = self.config
        for i, aid in enumerate(self.ids):
            cash, shares = self.clearing.available(aid)
            self.population.state[i, 0] = max(0, cash)
            self.population.state[i, 1] = max(0, shares)
            self.population.state[i, 2] = self.clearing.accounts[aid].cost_basis
        if cfg.decision_mode == "vectorized":
            return self.population.generate_tier2_decisions(self.price)
        if cfg.decision_mode in {"profile_heuristic", "mock"}:
            from agents.fast_agent_kernel import FastAgentKernel
            views = [SimpleNamespace(agent_id=aid, persona=p) for aid, p in zip(self.ids, self.personas)]
            actions, _ = FastAgentKernel(min_quantity=cfg.market.board_lot).decide(views,
                current_price=self.price, tick=step,
                macro_state={"sentiment_index": (1 + float(np.mean(self.population.state[:, 3]))) / 2})
            return (np.array([{"BUY": 1, "SELL": -1, "HOLD": 0}[a.action] for a in actions]),
                    np.array([int(a.amount) for a in actions]), np.array([a.target_price for a in actions]))
        # No LLM objects are instantiated in rule-based runs.
        if cfg.decision_mode != "cognitive_rule" and self.llm_router is None:
            from core.llm.router import LLMRouter
            from core.llm.config import LLMSettings
            self.llm_router = LLMRouter(settings=LLMSettings(max_retries=cfg.llm.max_retries, timeout_seconds=cfg.llm.timeout), allow_offline_fallback=False)
        if cfg.decision_mode in {"cognitive_llm", "cognitive_rule"}:
            base = self.population.generate_tier2_decisions(self.price)
            market = {
                "price": float(self.price),
                "best_bid": float(self.book.get_best_bid() or 0.0),
                "best_ask": float(self.book.get_best_ask() or 0.0),
                "depth": self.book.get_depth(5),
            }
            actions, quantities, prices = await self.cognitive_protocol.decide(
                step=step, router=self.llm_router, personas=self.personas,
                accounts=self.clearing.accounts, clearing=self.clearing,
                market=market, rules=cfg.market,
                prev_close=float(self.book.market_rules.prev_close),
                llm=cfg.llm, base_actions=base[0], base_quantities=base[1],
                base_prices=base[2], agent_ids=self.ids,
                scientific_world=getattr(self, "scientific_world", None),
            )
            return (
                np.asarray(actions, dtype=int),
                np.asarray(quantities, dtype=int),
                np.asarray(prices, dtype=float),
            )
        actions, quantities, prices = [], [], []
        for aid, persona in zip(self.ids, self.personas):
            payload = {"step": step, "price": self.price, "persona": persona.to_dict(),
                       "account": asdict(self.clearing.accounts[aid]),
                       "available_cash_shares": self.clearing.available(aid)}
            response = await self.llm_router.complete([
                {"role": "system", "content": 'Return JSON: {"action":"BUY|SELL|HOLD","quantity":integer,"price":number}. No borrowing or short selling.'},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                mode=cfg.llm.provider, model=cfg.llm.model, temperature=cfg.llm.temperature,
                timeout=cfg.llm.timeout, max_tokens=250, json_mode=True, allow_provider_fallback=False)
            result = json.loads(response.text)
            action = {"BUY": 1, "SELL": -1, "HOLD": 0}[result["action"]]
            qty, price = result["quantity"], result["price"]
            if type(qty) is not int or qty < 0 or not math.isfinite(price) or price <= 0:
                raise ValueError("Invalid LLM decision schema")
            actions.append(action); quantities.append(qty); prices.append(price)
        return np.array(actions), np.array(quantities), np.array(prices)

    def _agent_rows(self, step):
        for i, aid in enumerate(self.ids):
            account = self.clearing.accounts[aid]
            yield {"step": step, "agent_id": aid, "cash": account.cash, "shares": account.shares,
                   "cost_basis": account.cost_basis,
                   "wealth": account.cash + account.shares * self.price,
                   "sentiment": float(self.population.state[i, 3]),
                   "sirs_state": self.social.agents[i].sentiment_state.value,
                   "risk_appetite": float(self.population.risk_appetite_state[i]),
                   "trading_intent": float(self.population.trading_intent_state[i])}

    def run(self, output_root: str | Path = "outputs/experiments") -> Path:
        return asyncio.run(self.run_async(output_root))

    async def run_async(self, output_root: str | Path) -> Path:
        if hasattr(self, "output_dir"):
            raise RuntimeError("ExperimentRunner is single-use; create a new runner for each independent run")
        cfg = self.config
        run_id = f"{cfg.name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:10]}"
        output = Path(output_root).resolve() / run_id
        output.mkdir(parents=True, exist_ok=False)
        self.output_dir = output
        manifest = ExperimentManifest.build(module="market_experiment", seed=cfg.seed,
            dataset_snapshot_id="initializing", config=cfg.model_dump(mode="json"), run_id=run_id,
            parent_run_id=self.parent_run_id,
            model_version=cfg.llm.model if cfg.decision_mode in {"llm", "cognitive_llm"} else cfg.decision_mode,
            notes={"status": "running", "output": str(output), "schema_version": 1,
                   "archive_schema_version": 2,
                   "output_origin": "mock" if cfg.decision_mode == "mock" else "simulation",
                   "determinism": "external_model_nondeterministic" if cfg.decision_mode in {"llm", "cognitive_llm"} else "seeded_local_rules"})
        write_experiment_manifest(manifest, output)
        write_json(output / "config.json", cfg.model_dump(mode="json"))
        sinks: dict[str, CsvSink] = {}
        completed = 0
        try:
            source = source_fingerprint()
            write_json(output / "source.json", source)
            packages = ["numpy", "pandas", "networkx", "pydantic", "pyyaml"]
            if cfg.decision_mode in {"llm", "cognitive_llm"}:
                packages.extend(["openai", "httpx"])
            versions = {p: importlib.metadata.version(p) for p in packages}
            installed = dict(sorted((d.metadata["Name"], d.version)
                                    for d in importlib.metadata.distributions() if d.metadata["Name"]))
            (output / "requirements-lock.txt").write_text(
                "".join(f"{name}=={version}\n" for name, version in installed.items()), encoding="utf-8")
            backend = {"name": cfg.market.backend}
            if cfg.market.backend == "cpp":
                spec = importlib.util.find_spec("_civitas_lob")
                if spec is None or not spec.origin:
                    raise ImportError("Requested C++ backend is not built")
                backend.update(binary=Path(spec.origin).name,
                               sha256=hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest())
            write_json(output / "environment.json", {"python": platform.python_version(), "platform": platform.platform(),
                       "packages": versions, "installed_packages": installed, "backend": backend})
            initial = self._initialize(output)
            identity_config = cfg.model_dump(mode="json")
            if identity_config["historical_input"]:
                identity_config["historical_input"]["path"] = "input_snapshot.csv"
            initialized = ExperimentManifest.build(module="market_experiment", seed=cfg.seed,
                dataset_snapshot_id=stable_hash(initial), config=identity_config,
                run_id=run_id, timestamp=manifest.timestamp, git_commit=manifest.git_commit,
                parent_run_id=self.parent_run_id,
                notes={**manifest.notes, "source_hash": source["sha256"], "git_dirty": source["git_dirty"]},
                model_version=manifest.model_version)
            manifest = initialized
            write_experiment_manifest(manifest, output)
            sinks["agents"] = CsvSink(output / "agents.csv", list(next(self._agent_rows(0))))
            sinks["trades"] = CsvSink(output / "trades.csv", ["step", "trade_id", "price", "quantity", "maker_id", "taker_id", "maker_agent_id", "taker_agent_id", "buyer_agent_id", "seller_agent_id", "timestamp", "buyer_fee", "seller_fee", "seller_tax"])
            sinks["order_events"] = CsvSink(output / "order_events.csv", ["step", "order_id", "event", "reason"])
            sinks["decisions"] = CsvSink(output / "decisions.csv", ["step", "agent_id", "action", "quantity", "price", "source"])
            for row in self._agent_rows(0):
                sinks["agents"].add(row)
            with (output / "events.jsonl").open("w", encoding="utf-8") as event_file, (output / "run.log").open("w", encoding="utf-8") as log:
                for step in range(1, cfg.steps + 1):
                    if step > 1 and (step - 1) % cfg.market.steps_per_day == 0:
                        self.clearing.new_day()
                        self.book.update_prev_close(self.price)
                    if hasattr(self, "scientific_world"):
                        self.scientific_world.advance(step)
                    for order in self.clearing.active_orders():
                        if step - self.order_steps[order.order_id] >= cfg.market.order_ttl:
                            self.clearing.cancel(order.order_id)
                            sinks["order_events"].add({"step": step, "order_id": order.order_id, "event": "cancel", "reason": "time_to_live"})
                    for event in cfg.events:
                        if event.step != step:
                            continue
                        affected = []
                        if event.kind == "panic":
                            affected = self.diffusion.inject_panic(int(event.value))
                        elif event.kind == "sentiment":
                            self.population.state[:, 3] = np.clip(self.population.state[:, 3] + event.value, -1, 1)
                        elif event.kind in {"halt", "resume"}:
                            self.halted = event.kind == "halt"
                            if self.halted:
                                for order in self.clearing.active_orders():
                                    self.clearing.cancel(order.order_id)
                                    sinks["order_events"].add({"step": step, "order_id": order.order_id, "event": "cancel", "reason": "halt"})
                        elif event.kind == "tax_change":
                            self.book.market_rules = replace(self.book.market_rules, stamp_duty_rate=event.value)
                            if hasattr(self.book, "_cpp_lob"):
                                self.book._cpp_lob.set_rule_config(self.book._build_cpp_rule_config())
                        event_file.write(json.dumps({**event.model_dump(), "affected_nodes": affected}) + "\n")
                        event_file.flush()
                    for i, aid in enumerate(self.ids):
                        self.social.update_holdings(i, {cfg.market.symbol: self.clearing.accounts[aid].shares})
                    social_stats = self.diffusion.update_sentiment_propagation()
                    signals = [-1 if self.social.agents[i].sentiment_state == SentimentState.INFECTED else 0 for i in range(cfg.n_agents)]
                    trend = (self.price - self.previous_price) / self.previous_price
                    self.population.update_tier2_sentiment(signals, trend)
                    actions, quantities, prices = await self._decisions(step)
                    if any(np.shape(v) != (cfg.n_agents,) for v in (actions, quantities, prices)) or not np.isin(actions, [-1, 0, 1]).all() or not np.isfinite(prices).all() or not np.isfinite(quantities).all() or (quantities < 0).any() or (quantities != np.floor(quantities)).any() or (prices <= 0).any():
                        raise ValueError("Decision batch violates agent-market contract")
                    # Decision CSV retains canonical agent order. Only order
                    # arrival (not perception or LLM scheduling) is shuffled.
                    for i, (action, qty, price) in enumerate(zip(actions, quantities, prices)):
                        sinks["decisions"].add({"step": step, "agent_id": self.ids[i],
                            "action": int(action), "quantity": int(qty),
                            "price": float(price), "source": cfg.decision_mode})
                    submission_indices = list(range(cfg.n_agents))
                    if (cfg.decision_mode in {"cognitive_llm", "cognitive_rule"}
                            and cfg.cognitive.order_schedule == "seeded_per_step"):
                        random.Random(
                            f"{cfg.seed}|order_arrival_v1|{step}"
                        ).shuffle(submission_indices)
                    if hasattr(self, "scientific_world"):
                        self.scientific_world.add_order_schedule(
                            step, [self.ids[i] for i in submission_indices]
                        )
                    trades = []
                    for i in submission_indices:
                        action, qty, price = actions[i], quantities[i], prices[i]
                        if action == 0 or qty <= 0 or self.halted:
                            continue
                        order = Order(cfg.market.symbol, float(price), int(qty),
                            OrderSide.BUY if action == 1 else OrderSide.SELL,
                            OrderType.LIMIT, self.ids[i], float(step),
                            order_id=f"s{step:08d}-a{i:06d}")
                        self.order_steps[order.order_id] = step
                        trades.extend(self.clearing.submit(order))
                        sinks["order_events"].add({"step": step,
                            "order_id": order.order_id, "event": order.status.value,
                            "reason": order.reason})
                    self.previous_price = self.price
                    self.price = self.book.last_price
                    self.clearing.check_invariants()
                    if cfg.decision_mode in {"cognitive_llm", "cognitive_rule"}:
                        await self.cognitive_protocol.after_step(
                            step=step, agent_ids=self.ids, actions=actions,
                            quantities=quantities, trades=trades,
                            accounts=self.clearing.accounts, price=self.price,
                            router=self.llm_router, llm=cfg.llm,
                            scientific_world=getattr(self, "scientific_world", None),
                        )
                    tape = [asdict(t) for t in trades]
                    snapshot = {"last_price": self.price, "best_bid": self.book.get_best_bid(),
                                "best_ask": self.book.get_best_ask(), "depth": self.book.get_depth(5)}
                    metrics = {"step": step, "price": self.price, "return": self.price / self.previous_price - 1,
                        "volume": sum(t.quantity for t in trades), "halted": self.halted,
                        "fees_cumulative": self.clearing.fees, "infected": social_stats["infected"],
                        **MarketMetrics.compute(snapshot=snapshot, trade_tape=tape)}
                    if hasattr(self, "scientific_world"):
                        metrics.update(self.scientific_world.market_metrics(
                            step=step, price=self.price
                        ))
                        # A missing quote is *not* evidence of a zero spread.
                        quoted = bool(
                            snapshot["best_bid"] and snapshot["best_ask"]
                            and snapshot["best_ask"] >= snapshot["best_bid"]
                        )
                        metrics.update(
                            two_sided_quote_valid=int(quoted),
                            spread_pct_when_quoted=(metrics["spread_pct"] if quoted else ""),
                            no_trade=int(metrics["volume"] == 0),
                            stale_price=int(abs(metrics["return"]) <= 1e-12),
                        )
                    if "metrics" not in sinks:
                        sinks["metrics"] = CsvSink(output / "metrics.csv", list(metrics))
                        sinks["social"] = CsvSink(output / "social.csv", list(social_stats))
                    sinks["metrics"].add(metrics)
                    sinks["social"].add(social_stats)
                    for trade in tape:
                        sinks["trades"].add({"step": step, **trade})
                    for row in self._agent_rows(step):
                        sinks["agents"].add(row)
                    for sink in sinks.values():
                        sink.flush()
                    log.write(f"step={step} trades={len(trades)} price={self.price:.8f} invariants=pass\n")
                    log.flush()
                    completed = step
            manifest.notes["status"] = "completed"
        except BaseException as exc:
            manifest.notes.update(status="interrupted" if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "failed",
                                  error_type=type(exc).__name__)
            (output / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
            raise
        finally:
            for sink in sinks.values():
                sink.close()
            if hasattr(self, "clearing"):
                with (output / "orders.csv").open("w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=["order_id", "agent_id", "symbol", "side", "order_type", "price", "quantity", "filled_qty", "timestamp", "status", "reason"])
                    writer.writeheader()
                    for order in self.clearing.orders.values():
                        row = asdict(order); row.pop("metadata")
                        row.update(side=order.side.value, order_type=order.order_type.value, status=order.status.value)
                        writer.writerow(row)
            calls = self.llm_router.get_call_records() if self.llm_router else []
            write_json(output / "model_calls.json", calls)
            if hasattr(self, "cognitive_protocol"):
                self.cognitive_protocol.write_artifacts(output)
            if hasattr(self, "scientific_world"):
                self.scientific_world.write_artifacts(output)
            manifest.notes.update(completed_steps=completed, ended_at=utc_now_iso())
            summary = {"run_id": run_id, "experiment_id": manifest.experiment_id, **manifest.notes,
                       "trade_count": self.book.trade_count if hasattr(self, "book") else 0,
                       "final_price": self.price if hasattr(self, "price") else None}
            write_json(output / "summary.json", summary)
            manifest.artifacts = {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
                                  for p in sorted(output.iterdir()) if p.is_file() and not p.name.endswith(".manifest.json")}
            write_experiment_manifest(manifest, output)
        return output
