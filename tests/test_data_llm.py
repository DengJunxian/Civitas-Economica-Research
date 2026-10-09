import asyncio

import pandas as pd
import pytest

from core.data.market_data_provider import MarketDataProvider, MarketDataQuery


def test_live_source_failure_never_generates_synthetic(tmp_path):
    p = MarketDataProvider(str(tmp_path / "cache"), str(tmp_path / "snap"), ["unavailable"])
    with pytest.raises(RuntimeError):
        p.get_ohlcv(MarketDataQuery("X", market="TEST"))


def test_synthetic_is_explicit_and_cache_cannot_contaminate_live(tmp_path):
    args = dict(cache_dir=str(tmp_path / "cache"), snapshot_dir=str(tmp_path / "snap"))
    p = MarketDataProvider(**args, provider_priority=["synthetic"])
    q = MarketDataQuery("X", market="TEST", period_days=20)
    frame = p.get_ohlcv(q, freeze_snapshot=True)
    assert set(frame.provider) == {"synthetic"}
    assert set(frame.data_origin) == {"synthetic_test"}
    p.get_ohlcv(q, freeze_snapshot=True)  # cache-hit snapshot path
    with pytest.raises(RuntimeError):
        MarketDataProvider(**args, provider_priority=["unavailable"]).get_ohlcv(q)
    with pytest.raises(ValueError):
        MarketDataProvider(**args, provider_priority=["akshare", "synthetic"])


def test_llm_failure_requires_explicit_mock():
    from core.llm.router import LLMRouter
    from core.llm.base import LLMResponse
    class Failing:
        async def complete(self, *args, **kwargs):
            raise TimeoutError("controlled failure")
    kwargs = {"deepseek_client": Failing(), "zhipu_client": Failing()}
    router = LLMRouter(**kwargs)
    with pytest.raises(RuntimeError, match="offline substitution disabled"):
        asyncio.run(router.complete([]))
    assert router.get_call_records() and all(not r["ok"] for r in router.get_call_records())
    mock = LLMRouter(**kwargs, allow_offline_fallback=True)
    response = asyncio.run(mock.complete([]))
    assert response.provider == "offline" and not response.ok


def test_fixed_model_does_not_switch_providers():
    from core.llm.router import LLMRouter
    calls = []
    class Failing:
        async def complete(self, messages, **kwargs):
            calls.append(kwargs["model"])
            raise TimeoutError()
    router = LLMRouter(deepseek_client=Failing(), zhipu_client=Failing())
    with pytest.raises(RuntimeError):
        asyncio.run(router.complete([], model="chosen-model", mode="deepseek", allow_provider_fallback=False))
    assert calls == ["chosen-model"]


def test_no_trade_bar_carries_price(monkeypatch):
    from core.market_engine import MarketDataManager
    manager = MarketDataManager(None, load_real_data=False, seed=42)
    before = manager.engine.last_price
    bar = manager.finalize_step(1, "2024-01-02", trades=[])
    assert bar.open == bar.high == bar.low == bar.close == before
    assert bar.volume == 0
