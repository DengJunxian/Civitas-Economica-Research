
import time
import random
import hashlib
import json
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any, TYPE_CHECKING
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
try:
    import akshare as ak
except Exception:
    ak = None
import uuid
import os

from core.types import Order, Trade, Candle, OrderSide, OrderType, OrderStatus, ExecutionPlan
from core.time_manager import SimulationClock
from core.policy import PolicyManager
from core.regulation.risk_control import RiskEngine
from core.exchange.a_share_session import call_auction_match, find_price_maximizing_volume_and_minimizing_imbalance
from core.exchange.bar_builder import TradeTapeBarBuilder, TradeTapeEntry

if TYPE_CHECKING:
    from agents.base_agent import MarketSnapshot


try:
    from core.exchange.order_book_cpp import OrderBookCPP, _civitas_lob
    from core.exchange.order_book import Order as OrderModel
    if _civitas_lob is None:
        raise ImportError("C++ extension _civitas_lob not available")
    USE_CPP_LOB = True
    print("[*] High-Performance C++ OrderBook Activated")
except ImportError as e:
    USE_CPP_LOB = False
    print(f"[!] Falling back to Python: {e}")


from config import GLOBAL_CONFIG
from core.data.market_data_provider import MarketDataProvider, MarketDataQuery

# ==========================================


# ==========================================

class ChinaTradingCalendar:
    """
    管理 A 股交易日。

    规则包含周末与法定节假日，调休工作日不自动视为交易日。
    """
    

    HOLIDAYS_WEEKDAY_2025 = [
        "2025-01-01",
        "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31", "2025-02-03", "2025-02-04",
        "2025-04-04",
        "2025-05-01", "2025-05-02", "2025-05-05",
        "2025-06-02",
        "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08"
    ]
    
    HOLIDAYS_WEEKDAY_2026 = [
        "2026-01-01", "2026-01-02",
        "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23",
        "2026-04-06",
        "2026-05-01", "2026-05-04", "2026-05-05",
        "2026-06-19",
        "2026-09-25",
        "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"
    ]
    
    ALL_HOLIDAYS = set(HOLIDAYS_WEEKDAY_2025 + HOLIDAYS_WEEKDAY_2026)

    @staticmethod
    def get_next_trading_day(date_str: str) -> str:
        """Returns the next valid A-share trading day."""
        try:
            curr = datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            curr = datetime.now()
            
        while True:
            curr += timedelta(days=1)
            d_str = curr.strftime("%Y-%m-%d")
            

            if curr.weekday() >= 5:
                continue
                

            if d_str in ChinaTradingCalendar.ALL_HOLIDAYS:
                continue
            
            return d_str

# ==========================================


# ==========================================

@dataclass
class PolicyState:
    tax_rate: float = GLOBAL_CONFIG.TAX_RATE_STAMP       
    risk_free_rate: float = 0.02  
    liquidity_injection: float = 0.0 
    description: str = "Initial State" 


@dataclass
class ExogenousLiquidityPoint:
    """External market backdrop point used by hybrid replay mode."""

    step: int
    price: float
    volume: float


def blend_price_with_backdrop(
    old_price: float,
    endogenous_price: float,
    *,
    exogenous_price: Optional[float] = None,
    exogenous_volume: float = 0.0,
    backdrop_weight: float = 0.35,
) -> float:
    """
    Blend endogenous simulated price with exogenous backdrop series.
    Agents still generate endogenous orders; backdrop acts as liquidity anchor.
    """
    old_p = float(max(old_price, 1e-6))
    endo_p = float(max(endogenous_price, 1e-6))
    if exogenous_price is None:
        return endo_p

    exo_p = float(max(exogenous_price, 1e-6))
    w = max(0.0, min(1.0, float(backdrop_weight)))
    raw = (1.0 - w) * endo_p + w * exo_p


    vol = max(0.0, float(exogenous_volume))
    damp = 1.0 + min(3.0, vol / 1_000_000.0) * 0.20
    return float(old_p + (raw - old_p) / damp)


def _resolve_bool_flag(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _resolve_feature_flags(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    flags = {
        "market_kernel_v1": _resolve_bool_flag(os.getenv("CIVITAS_MARKET_KERNEL_V1"), False),
    }
    for key, value in (overrides or {}).items():
        flags[str(key)] = _resolve_bool_flag(value, flags.get(str(key), False))
    return flags

# ==========================================


# ==========================================

class MatchingEngine:
    """
    A-Share High Fidelity Matching Engine.
    
    Responsibilities:
    1. Order Book Maintenance (Heapq)
    2. Price Limit Checks
    3. Continuous Matching Logic
    4. Fee & Tax Calculation
    5. National Team Intervention
    """

    def __init__(self, symbol: str = "A_SHARE_IDX", prev_close: float = 3000.0, clock: Optional[SimulationClock] = None):
        self.symbol = symbol
        self.prev_close = prev_close
        self.clock = clock
        


        if USE_CPP_LOB:
            self.lob = OrderBookCPP(symbol, prev_close)
            print("[MatchingEngine] Using high-performance C++ OrderBook")
        else:


            from core.exchange.order_book import OrderBook
            self.lob = OrderBook(symbol, prev_close)
            print("[MatchingEngine] Using Pure Python OrderBook (Fallback)")
        

        self.last_price = prev_close
        self.total_volume = 0
        self.trades_history: List[Trade] = []
        

        self.step_trades_buffer: List[Trade] = []

    def update_prev_close(self, close_price: float):
        """Update prev_close after market close for next day's limit calculation."""
        self.prev_close = close_price

    def _execution_child_schedule(self, plan: ExecutionPlan) -> List[int]:
        return plan.resolved_child_schedule(self.last_price)

    def _execution_child_price(self, plan: ExecutionPlan, child_index: int) -> float:
        reference_price = plan.resolved_reference_price(self.last_price)
        if plan.order_type == OrderType.MARKET:
            return float(reference_price)

        slip = max(0.0, float(plan.max_slippage))
        if plan.order_type == OrderType.POST_ONLY:
            slip *= 0.5

        if plan.is_buy:
            factor = 1.0 - slip if plan.order_type == OrderType.POST_ONLY else 1.0 + slip
        else:
            factor = 1.0 + slip if plan.order_type == OrderType.POST_ONLY else 1.0 - slip

        if str(plan.slicing_rule).lower() in {"twap", "twap-like", "twap_like"} and plan.time_horizon > 1:
            step_adjust = 0.01 * child_index
            factor = factor + step_adjust if plan.is_buy else factor - step_adjust

        return float(round(max(0.01, reference_price * factor), 2))

    def submit_execution_plan(self, plan: ExecutionPlan, liquidity_injection_prob: float = 0.0) -> List[Trade]:
        """Execute a structured plan by splitting it into child orders."""
        child_schedule = self._execution_child_schedule(plan)
        if not child_schedule:
            return []

        remaining_qty = plan.resolved_qty(self.last_price)
        if remaining_qty <= 0:
            return []

        generated_trades: List[Trade] = []
        for child_index, child_qty in enumerate(child_schedule):
            if remaining_qty <= 0:
                break

            current_qty = min(int(child_qty), remaining_qty)
            if current_qty <= 0:
                continue

            child_price = self._execution_child_price(plan, child_index)
            child_order = plan.to_order(
                price=child_price,
                quantity=current_qty,
                timestamp=plan.timestamp + (child_index * 0.001),
                child_index=child_index,
                extra_metadata={"child_schedule": list(child_schedule)},
            )
            child_trades = self.submit_order(child_order, liquidity_injection_prob)
            generated_trades.extend(child_trades)

            filled_qty = sum(int(trade.quantity) for trade in child_trades)
            remaining_qty -= filled_qty if filled_qty > 0 else current_qty

            if (
                child_order.remaining_qty > 0
                and str(plan.cancel_replace_policy).lower() in {"cancel-replace", "reprice", "aggressive"}
                and child_order.order_type in {OrderType.LIMIT, OrderType.POST_ONLY}
            ):
                reprice = self._execution_child_price(plan, child_index + 1)
                replacement_qty = int(child_order.remaining_qty)
                if replacement_qty > 0:
                    replacement_order = plan.to_order(
                        price=reprice,
                        quantity=replacement_qty,
                        timestamp=child_order.timestamp + 0.0001,
                        child_index=child_index,
                        extra_metadata={
                            "replacement_for": child_order.order_id,
                            "replacement_round": 1,
                        },
                    )
                    replacement_trades = self.submit_order(replacement_order, liquidity_injection_prob)
                    generated_trades.extend(replacement_trades)
                    remaining_qty -= sum(int(trade.quantity) for trade in replacement_trades)

        return generated_trades

    def _check_price_limit(self, price: float) -> bool:
        """
        娑ㄨ穼鍋滄鏌?(Delegated to OrderBook)
        """
        if hasattr(self, 'lob'):
            return self.lob._check_price_limit(price)
            

        limit = GLOBAL_CONFIG.PRICE_LIMIT 
        upper = self.prev_close * (1 + limit)
        lower = self.prev_close * (1 - limit)
        return round(lower, 2) <= round(price, 2) <= round(upper, 2)

    def submit_order(self, order: Order | ExecutionPlan, liquidity_injection_prob: float = 0.0) -> List[Trade]:
        """
        Submit an order and attempt immediate matching.
        
        Args:
            order: The Order object.
            liquidity_injection_prob: Probability (0-1) of National Team intervention if selling pressure is high.
            
        Returns:
            List[Trade]: Trades generated by this order.
        """
        if isinstance(order, ExecutionPlan):
            return self.submit_execution_plan(order, liquidity_injection_prob)


        if not 0 <= liquidity_injection_prob <= 1:
            raise ValueError("liquidity_injection_prob must be in [0, 1]")
        generated_trades = []
        if order.side == OrderSide.SELL and liquidity_injection_prob > 0:
            if random.random() < liquidity_injection_prob:
                team_order = Order(
                    symbol=self.symbol, price=order.price, quantity=order.quantity,
                    agent_id="NATIONAL_TEAM", side=OrderSide.BUY, order_type=OrderType.LIMIT,
                    timestamp=self.clock.timestamp if self.clock else time.time(),
                    metadata={"source": "explicit_external_liquidity_model", "funded_account": False},
                )
                generated_trades.extend(self.lob.add_order(team_order))
        # Both kernels consume the same Order contract; status, fees and metadata
        # survive without constructing a second order/trade representation.
        generated_trades.extend(self.lob.add_order(order))
        for trade in generated_trades:
            self.last_price = trade.price
            self.total_volume += trade.quantity
            self.trades_history.append(trade)
        self.step_trades_buffer.extend(generated_trades)
        return generated_trades


    def get_order_book_depth(self, level=5) -> Dict:
        """获取五档市场深度。"""

        return self.lob.get_depth(level)

    def flush_step_trades(self) -> List[Trade]:
        """返回当前步成交并清空缓冲区。"""
        trades = self.step_trades_buffer[:]
        self.step_trades_buffer = []
        return trades

    def run_call_auction(self, orders: List[Order], market_time: float = None) -> Tuple[float, List[Trade]]:
        """
        模拟 A 股集合竞价阶段。

        通过最大化可成交量、最小化不平衡量来确定开盘价。
        """
        if not orders:
            return self.prev_close, []
        
        lower, upper = self.lob.get_limit_prices() if hasattr(self, "lob") and hasattr(self.lob, "get_limit_prices") else (None, None)
        opening_price, auction_meta = find_price_maximizing_volume_and_minimizing_imbalance(
            orders,
            prev_close=float(self.prev_close),
            lower_limit=lower,
            upper_limit=upper,
        )
        if int(auction_meta.get("match_volume", 0)) <= 0:
            return float(opening_price), []

        trades = call_auction_match(
            orders,
            price=float(opening_price),
            timestamp=float(market_time if market_time is not None else (self.clock.timestamp if self.clock else time.time())),
            commission_rate=GLOBAL_CONFIG.TAX_RATE_COMMISSION,
            stamp_duty_rate=GLOBAL_CONFIG.TAX_RATE_STAMP,
            seller_only_stamp_duty=True,
        )
        for idx, trade in enumerate(trades):
            trade.trade_id = f"auction_{hashlib.sha256(f'{self.symbol}|{market_time}|{opening_price}|{idx}|{trade.buyer_agent_id}|{trade.seller_agent_id}|{trade.quantity}'.encode('utf-8')).hexdigest()[:20]}"

        if trades:
            self.last_price = opening_price
            self.total_volume += sum(t.quantity for t in trades)
            self.trades_history.extend(trades)
            self.step_trades_buffer.extend(trades)
        
        return opening_price, trades

# ==========================================


# ==========================================

class RealMarketLoader:
    """Real market history loader with provider fallback and deterministic caching."""

    _provider = MarketDataProvider()

    @staticmethod
    def _to_candles(df: pd.DataFrame, symbol: str) -> List[Candle]:
        if df is None or df.empty:
            return []
        out: List[Candle] = []
        cnt = len(df)
        for i, row in df.iterrows():
            o = float(row.get("open", row.get("Open", 3000.0)))
            h = float(row.get("high", row.get("High", 3000.0)))
            low_price = float(row.get("low", row.get("Low", 3000.0)))
            c = float(row.get("close", row.get("Close", 3000.0)))
            v = int(float(row.get("volume", row.get("Volume", 0.0))))
            d = str(row.get("date", row.get("Date", "2024-01-01")))
            out.append(
                Candle(
                    symbol=symbol,
                    step=-(cnt - i),
                    timestamp=d,
                    open=o,
                    high=h,
                    low=low_price,
                    close=c,
                    volume=v,
                    is_simulated=False,
                )
            )
        return out

    @staticmethod
    def load_history(symbol="sh000001", period="365") -> List[Candle]:
        period_int = int(period)
        if period_int <= 0:
            raise ValueError("period must be positive")
        frame = RealMarketLoader._provider.get_ohlcv(
            MarketDataQuery(symbol=symbol, interval="1d", period_days=period_int, adjust="", market="CN"),
            use_cache=True,
        )
        if frame.empty or not set(frame["provider"]).issubset({"akshare", "yfinance", "ashare"}):
            raise RuntimeError("Historical data unavailable or lacks real-provider provenance")
        return RealMarketLoader._to_candles(frame.tail(period_int).reset_index(drop=True), symbol)

class PolicyInterpreter:
    """
    政策解释器。

    通过 DeepSeek 或 GLM 分析政策文本，量化其对市场的影响，并支持多模型路由。
    """
    
    def __init__(self, api_key_or_router, *, allow_rule_fallback: bool = False):
        self.allow_rule_fallback = allow_rule_fallback

        if hasattr(api_key_or_router, 'call_with_fallback'):
            self.router = api_key_or_router
            self.api_key = self.router.deepseek_key if self.router.deepseek_key else "dummy"
        else:
            self.router = None
            self.api_key = api_key_or_router
            
        self.last_reasoning = None

    async def interpret(self, policy_text: str) -> Dict:
        """
        分析政策文本并返回量化参数。
        """
        if not self.api_key:
            if not self.allow_rule_fallback:
                raise RuntimeError("Policy model unavailable; rule defaults require explicit opt-in")
            return self._default_policy()

        # 构造政策分析提示词
        prompt = f"""你是一位资深 A 股市场政策分析师。请分析以下政策对市场的影响，并给出量化参数。

【待分析政策】
{policy_text}

【当前市场基准】
- 印花税率: {GLOBAL_CONFIG.TAX_RATE_STAMP:.4%}
- 涨跌停限制: {GLOBAL_CONFIG.PRICE_LIMIT:.0%}

【分析要求】
1. 直接效应：流动性和交易成本影响
2. 信号效应：政策的隐含信号和市场解读
3. 二阶认知：投资者预期的自我实现
4. 时效性：短期情绪与中期基本面

【输出格式】
严格返回 JSON 格式，不要包含 Markdown 标记：
{{
    "tax_rate": <新的印花税率，float>,
    "liquidity_injection": <流动性注入概率，0.0-1.0>,
    "fear_factor": <恐慌因子，0.0-1.0>,
    "sentiment_shift": <情绪偏移量，-1.0到1.0>,
    "initial_news": "<简短新闻标题>",
    "market_impact": "<一句话总结>",
    "reasoning_summary": "<分析过程摘要>"
}}
"""
        try:


            
            content = ""
            reasoning = ""
            
            if not self.router:
                from core.model_router import ModelRouter
                self.router = ModelRouter(
                    deepseek_key=self.api_key,
                    zhipu_key=GLOBAL_CONFIG.ZHIPU_API_KEY
                )
            

            priority = ["deepseek-reasoner", "glm-4-flashx", "deepseek-chat"]
            
            response = await self.router.call_with_fallback(
                [{"role": "user", "content": prompt}],
                priority_models=priority,
                timeout_budget=60.0,
                fallback_response='{"tax_rate": 0.0005, "fear_factor": 0, "liquidity_injection": 0}'
            )
            

            content = response[0]
            reasoning = response[1]

            self.last_reasoning = reasoning
            
            # 解析 JSON
            import json
            import re
            
            # 清理 Markdown 包裹
            if "```" in content: 
                content = re.sub(r"```json|```", "", content).strip()
            

            try:
                result = json.loads(content)
            except json.JSONDecodeError:

                match = re.search(r'\{.*\}', content, re.DOTALL)
                if match:
                    result = json.loads(match.group())
                else:
                    raise ValueError("无法提取 JSON")

            result['reasoning'] = self.last_reasoning
            print(f"[OK] 政策分析完成: {result.get('initial_news', '未知')}")
            return result

        except Exception as e:
            if not self.allow_rule_fallback:
                raise
            print(f"[!] 政策分析失败: {e}")
            return self._default_policy()

    def _default_policy(self) -> Dict:
        """Fallback policy values when LLM policy parsing is unavailable."""
        return {
            "tax_rate": GLOBAL_CONFIG.TAX_RATE_STAMP,
            "liquidity_injection": 0.0,
            "fear_factor": 0.0,
            "sentiment_shift": 0.0,
            "initial_news": "Policy published",
            "market_impact": "Impact pending assessment",
            "reasoning": "explicit rule defaults",
            "data_origin": "rule_approximation",
            "llm_inference": False,
        }

# ==========================================


# ==========================================

class MarketDataManager:
    def __init__(
        self,
        api_key_or_router,
        load_real_data=False,
        clock: Optional[SimulationClock] = None,
        regulatory_module: Optional[Any] = None,
        *,
        seed: Optional[int] = None,
        initial_price: float = 3000.0,
        feature_flags: Optional[Dict[str, Any]] = None,
    ):
        self.policy = PolicyState()
        self.interpreter = PolicyInterpreter(api_key_or_router)
        self.clock = clock
        self.regulatory_module = regulatory_module
        self.seed = int(seed if seed is not None else os.getenv("CIVITAS_SEED", "42"))
        self.feature_flags = _resolve_feature_flags(feature_flags)
        self.config_hash = hashlib.sha256(
            json.dumps(
                {
                    "seed": self.seed,
                    "feature_flags": self.feature_flags,
                    "load_real_data": bool(load_real_data),
                    "symbol": "sh000001",
                },
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        self.rng = random.Random(self.seed)
        
        # 保留旧版政策管理器，用于兼容历史调用路径
        self.policy_manager = PolicyManager()
        

        self.risk_engine = RiskEngine(
            stamp_duty_rate=GLOBAL_CONFIG.TAX_RATE_STAMP,
            commission_rate=GLOBAL_CONFIG.TAX_RATE_COMMISSION
        )
        

        self.history_candles = RealMarketLoader.load_history() if load_real_data else []
        self.sim_candles = []
        self.trade_tape: List[Any] = []
        self.replay_metrics: Dict[str, Any] = {}
        
        initial_price = self.history_candles[-1].close if self.history_candles else float(initial_price)
        

        self.engine = MatchingEngine(prev_close=initial_price, clock=self.clock)
        self.bar_builder = TradeTapeBarBuilder(
            seed=self.seed,
            config_hash=self.config_hash,
            feature_flags=self.feature_flags,
            snapshot_info={"initial_price": initial_price, "load_real_data": bool(load_real_data)},
        )
        self.kernel = None
        if self.feature_flags.get("market_kernel_v1", False):
            from core.exchange.market_kernel import MarketKernel, MarketKernelConfig

            self.kernel = MarketKernel(
                symbol=self.engine.symbol,
                prev_close=initial_price,
                clock=self.clock,
                matching_engine=self.engine,
                regulatory_module=self.regulatory_module,
                config=MarketKernelConfig(seed=self.seed, feature_flags=self.feature_flags),
            )
        

        self.current_news = "等待市场开盘"
        self.panic_level = 0.0 
        self.csad_history = []
        self.text_factor_state: Dict[str, Any] = {
            "dominant_topic": "uncategorized",
            "sentiment_score": 0.0,
            "panic_index": 0.0,
            "greed_index": 0.0,
            "policy_shock": 0.0,
            "regime_bias": "neutral",
        }
        self.latest_impact_paths: List[Dict[str, Any]] = []
        
    @property
    def candles(self) -> List[Candle]:
        """返回历史与仿真合并后的 K 线。"""
        return self.history_candles + self.sim_candles

    def clear_simulation(self):
        """重置仿真状态。"""
        self.sim_candles = []
        self.csad_history = []
        self.trade_tape = []
        self.replay_metrics = {}
        self.panic_level = 0.0
        self.text_factor_state = {
            "dominant_topic": "uncategorized",
            "sentiment_score": 0.0,
            "panic_index": 0.0,
            "greed_index": 0.0,
            "policy_shock": 0.0,
            "regime_bias": "neutral",
        }
        self.latest_impact_paths = []


        initial_price = self.history_candles[-1].close if self.history_candles else 3000.0
        self.engine = MatchingEngine(prev_close=initial_price, clock=self.clock)
        if self.kernel is not None:
            self.kernel.clear()
            self.kernel.engine = self.engine
            self.kernel.prev_close = initial_price

    def apply_policy(self, text: str):
        params = self.interpreter.interpret(text)
        self.policy.liquidity_injection = params.get("liquidity_injection", 0.0)
        self.policy.tax_rate = params.get("tax_rate", GLOBAL_CONFIG.TAX_RATE_STAMP)
        self.policy.description = text
        self.current_news = params.get("initial_news", "政策已执行")
        self.panic_level = params.get("fear_factor", 0.0)
        

        self.policy_manager.set_policy_param("tax", "rate", self.policy.tax_rate)



    def ingest_seed_event(self, seed_event: Any) -> None:
        """将种子事件映射为市场文本因子。"""
        factors = getattr(seed_event, "text_factors", None)
        if not isinstance(factors, dict):
            return
        headline = getattr(seed_event, "summary", "") or getattr(seed_event, "title", "")
        self.ingest_text_factors(factors, headline=headline)

    def ingest_text_factors(self, factors: Dict[str, Any], headline: str = "") -> None:
        if not isinstance(factors, dict):
            return

        financial = factors.get("financial_factors", {}) or {}
        sentiment = self._safe_float(factors.get("sentiment_score"), 0.0)
        panic = self._safe_float(financial.get("panic_index"), 0.0)
        greed = self._safe_float(financial.get("greed_index"), 0.0)
        shock = self._safe_float(financial.get("policy_shock"), 0.0)
        regime = str(financial.get("regime_bias", "neutral"))
        dominant_topic = str(factors.get("dominant_topic", "uncategorized"))

        target_panic = self._clamp(
            (0.65 * panic) + (0.25 * max(-sentiment, 0.0)) + (0.10 * shock) - (0.20 * greed),
            0.0,
            1.0,
        )
        self.panic_level = self._clamp((0.60 * self.panic_level) + (0.40 * target_panic), 0.0, 1.0)

        if regime == "risk_off" and shock > 0.55:
            self.policy.liquidity_injection = max(self.policy.liquidity_injection, min(1.0, shock * 0.6))
        elif regime == "risk_on" and self.policy.liquidity_injection > 0:
            self.policy.liquidity_injection = max(0.0, self.policy.liquidity_injection - 0.05)

        self.text_factor_state = {
            "dominant_topic": dominant_topic,
            "sentiment_score": self._clamp(sentiment, -1.0, 1.0),
            "panic_index": self._clamp(panic, 0.0, 1.0),
            "greed_index": self._clamp(greed, 0.0, 1.0),
            "policy_shock": self._clamp(shock, 0.0, 1.0),
            "regime_bias": regime,
        }
        self.latest_impact_paths = factors.get("impact_paths", []) or []

        if headline:
            self.current_news = headline

    def calculate_csad(self, agent_returns):
        """计算横截面绝对偏差，用于识别羊群行为。"""
        if agent_returns is None or len(agent_returns) == 0:
            return
        rm = np.mean(agent_returns)
        csad = np.mean(np.abs(agent_returns - rm))
        self.csad_history.append(csad)
        

        if rm < -0.02 and csad < 0.02: 
            self.panic_level = min(1.0, self.panic_level + 0.1)
        elif self.policy.liquidity_injection > 0.5:
            self.panic_level = max(0.0, self.panic_level - 0.05)
    




    def submit_agent_order(self, order: Order):
        """将智能体订单提交给集中风控后的撮合引擎。"""
        

        if self.regulatory_module:

            if self.regulatory_module.circuit_breaker.is_halted:
                 order.status = OrderStatus.REJECTED
                 order.reason = "Market Halted (Circuit Breaker)"
                 return []
                 

            allowed, reg_reason = self.regulatory_module.trading_regulator.register_order(
                agent_id=order.agent_id,
                order_type=order.order_type.value,
                price=order.price,
                qty=order.quantity
            )
            
            if not allowed:
                order.status = OrderStatus.REJECTED
                order.reason = f"Regulatory Reject: {reg_reason}"
                print(f"[Regulatory] Order REJECTED for Agent {order.agent_id}: {reg_reason}")
                return []
        

        market_data = {
            "last_price": self.engine.last_price,
            "best_bid": None,
            "best_ask": None
        }
        
        allowed, penalty, reason = self.risk_engine.check_order_compliance(
            order.agent_id, order, market_data
        )
        
        if not allowed:

            print(f"[Risk Control] Order REJECTED for Agent {order.agent_id}: {reason}")
            order.status = OrderStatus.REJECTED
            order.reason = reason
            return []
            

        self.risk_engine.hft_monitor.register_order(order.agent_id)


        market_state = {"last_price": self.engine.last_price}
        policy_res = self.policy_manager.check_order(order, market_state)
        
        if not policy_res.is_allowed:

            order.status = OrderStatus.REJECTED
            order.reason = policy_res.reason
            return []
            

        if self.kernel is not None:
            current_ts = self.clock.timestamp if self.clock else time.time()
            trades = self.kernel.submit_order(
                order,
                current_timestamp=current_ts,
                liquidity_injection_prob=self.policy.liquidity_injection,
            )
            self.trade_tape.extend(self.kernel.flush_step_trade_tape())
        else:
            trades = self.engine.submit_order(order, self.policy.liquidity_injection)
            if trades:
                current_ts = self.clock.timestamp if self.clock else time.time()
                self.trade_tape.extend(
                    [
                        TradeTapeEntry(
                            trade=t,
                            tick=self.clock.ticks if self.clock else len(self.trade_tape),
                            phase="continuous",
                            event_type="trade",
                            queue_position=0,
                            latency_ticks=0,
                            market_timestamp=current_ts,
                            metadata={"source": "legacy_submit"},
                        )
                        for t in trades
                    ]
                )
        

        if trades:
            self.risk_engine.hft_monitor.register_trade(order.agent_id)
            
        return trades

    def get_market_snapshot(self) -> "MarketSnapshot":
        """Generate a MarketSnapshot for agents."""
        from agents.base_agent import MarketSnapshot
        

        depth = self.engine.get_order_book_depth(5)
        

        vol = 0.0
        if len(self.candles) > 20:
             closes = [c.close for c in self.candles[-20:]]
             vol = float(np.std(closes))
             

        trend = 0.0
        if len(self.candles) > 5:
            start_p = self.candles[-5].close
            if start_p > 0:
                trend = (self.candles[-1].close - start_p) / start_p


        best_bid = depth['bids'][0]['price'] if depth['bids'] else None
        best_ask = depth['asks'][0]['price'] if depth['asks'] else None
        
        mid = self.engine.last_price
        spread = 0.0
        if best_bid and best_ask:
            mid = (best_bid + best_ask) / 2
            spread = best_ask - best_bid
            
        return MarketSnapshot(
            symbol=self.engine.symbol,
            last_price=self.engine.last_price,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid,
            bid_ask_spread=spread,
            depth=depth,
            total_volume=self.engine.total_volume,
            volatility=vol,
            market_trend=trend,
            panic_level=self.panic_level,
            timestamp=self.clock.timestamp if self.clock else time.time(),
            # 鏀跨瓥瀛楁
            policy_description=self.policy.description,
            policy_tax_rate=self.policy.tax_rate,
            policy_news=self.current_news,
            text_dominant_topic=self.text_factor_state.get("dominant_topic", "uncategorized"),
            text_sentiment_score=float(self.text_factor_state.get("sentiment_score", 0.0)),
            text_panic_score=float(self.text_factor_state.get("panic_index", 0.0)),
            text_greed_score=float(self.text_factor_state.get("greed_index", 0.0)),
            text_policy_shock=float(self.text_factor_state.get("policy_shock", 0.0)),
            text_regime_bias=str(self.text_factor_state.get("regime_bias", "neutral")),
            text_impact_paths=self.latest_impact_paths[:8],
        )

    def get_order_book_depth(self, level=5) -> Dict:
        """Get L5 Market Depth."""
        return self.engine.get_order_book_depth(level)

    def finalize_step(self, step_idx, last_date_str, trades: List[Trade] = None) -> Candle:
        """
        Close the current simulation step, generate a Candle, 
        and prepare the engine for the next step.
        """
        open_p = self.engine.last_price
        current_ts = self.clock.timestamp if self.clock else time.time()
        

        new_date_str = ChinaTradingCalendar.get_next_trading_day(last_date_str)
        

        if trades is None:
            if self.kernel is not None:
                self.kernel.advance_to(current_ts)
                step_trades = self.kernel.flush_step_trades()
                step_trade_tape = self.kernel.flush_step_trade_tape()
            else:
                step_trades = self.engine.flush_step_trades()
                step_trade_tape = []
        else:
            step_trades = list(trades)
            step_trade_tape = []
        if not step_trade_tape and step_trades:
            step_trade_tape = [
                TradeTapeEntry(
                    trade=t,
                    tick=step_idx,
                    phase="continuous",
                    event_type="trade",
                    queue_position=0,
                    latency_ticks=0,
                    market_timestamp=current_ts,
                    metadata={"step_idx": step_idx, "source": "legacy_finalize"},
                )
                for t in step_trades
            ]
        self.trade_tape.extend(step_trade_tape)
        

        if not step_trades:

            c = self.bar_builder.build_bar(
                [], symbol=self.engine.symbol, step=step_idx, timestamp=new_date_str,
                prev_close=self.engine.prev_close, open_price=open_p, is_simulated=True,
                extra_metadata={"mode": "last_trade_carried_forward", "step_idx": step_idx},
            )
            c.open = c.high = c.low = c.close = open_p
            c.volume = 0
        else:

            for t in step_trades:
                t.seller_tax = self.policy_manager.calculate_total_tax(t)
            
            c = self.bar_builder.build_bar(
                step_trade_tape or step_trades,
                symbol=self.engine.symbol,
                step=step_idx,
                timestamp=new_date_str,
                prev_close=self.engine.prev_close,
                open_price=step_trades[0].price,
                is_simulated=True,
                extra_metadata={
                    "mode": "trade_tape",
                    "step_idx": step_idx,
                    "trade_count": len(step_trades),
                },
            )
            self.replay_metrics = self.bar_builder.build_replay_metrics(self.trade_tape, self.sim_candles + [c])


        self.sim_candles.append(c)
        self.engine.update_prev_close(c.close)
        if self.kernel is not None:
            self.kernel.prev_close = c.close
        
        return c

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))


if __name__ == "__main__":

    manager = MarketDataManager(api_key=None, load_real_data=False)
    

    manager.apply_policy("Reduce stamp tax to boost liquidity.")
    print(f"Policy: {manager.policy}")
    

    orders = [
        Order(price=3010.0, quantity=100, agent_id="alice", side="buy", timestamp=time.time()),
        Order(price=3005.0, quantity=100, agent_id="bob", side="sell", timestamp=time.time()+1),
        Order(price=3000.0, quantity=500, agent_id="charlie", side="sell", timestamp=time.time()+2)
    ]
    

    for o in orders:
        trades = manager.submit_agent_order(o)
        for t in trades:
            print(f"Trade Executed: Price {t.price}, Qty {t.quantity} | Buyer pays: {t.buyer_pay_amount:.2f}")
            

    candle = manager.finalize_step(1, "2024-01-01")
    print(f"Daily Candle Generated: {candle}")
