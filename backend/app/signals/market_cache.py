"""M23 Step 0 market_context 4h in-process cache (A3).

Why:
    M23 regenerate 由使用者按鈕觸發；同一日多人 / 同人多次按下時，VIX / 美股 /
    台指期 / USD-TWD 等外部市場資訊 4 小時內幾乎不變，沒理由每次都重打 OpenAI
    web search。Cron 一日一次 fresh、使用者按鈕 hits cache 即可。

Scope:
    - 4 小時 TTL（盤前判讀後足夠覆蓋整個交易日 + 收盤後幾小時）
    - In-process dict（Render web service 預設單 worker for FastAPI；BackgroundTasks
      在同 process 內跑，市場 cache 100% hit-rate）
    - 多 worker / 多 instance 部署若要共享 cache，改 Redis 或開 DB table
      `signal_market_context_cache`（schema: cached_at, payload JSON, expires_at）
    - cache key 僅看「VIX / 美股 / 台指期」這類外部宏觀變數，與個股無關，
      所以全站共用一個 key

Caller:
    `assemble_market_context()` 包 `get_cached_or_compute()` 即可；
    cache miss → 跑 OpenAI → 命中後 4h 內全部 reuse。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 4 小時 TTL；盤中盤後共用
DEFAULT_TTL_SECONDS = 4 * 60 * 60

# 全站共用單一 cache slot（market 只有一個，不需要 stock-level key）
_CACHE_LOCK = threading.Lock()
_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}  # key -> (expires_at, payload)
_GLOBAL_KEY = "m23:market_context"


def get_cached(key: str = _GLOBAL_KEY) -> Optional[Dict[str, Any]]:
    """命中回 payload；過期 / 沒寫過 → None。"""
    with _CACHE_LOCK:
        entry = _cache.get(key)
        if entry is None:
            return None
        expires_at, payload = entry
        if expires_at < time.time():
            _cache.pop(key, None)
            return None
        return payload


def set_cached(
    payload: Dict[str, Any],
    *,
    key: str = _GLOBAL_KEY,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    """寫入；同一 key 直接覆蓋。"""
    with _CACHE_LOCK:
        _cache[key] = (time.time() + ttl_seconds, payload)


def get_or_compute(
    compute_fn: Callable[[], Dict[str, Any]],
    *,
    key: str = _GLOBAL_KEY,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    should_cache_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> Dict[str, Any]:
    """命中回 cached；miss 跑 compute_fn 並寫 cache 後回。

    `should_cache_fn` 預設 lambda payload: True；可用來避開 cache 失敗結果
    （例如 fallback dict 不該被 cache 4 小時否則使用者一直看到 RANGE）。
    """
    cached = get_cached(key)
    if cached is not None:
        logger.info("M23 market_context cache hit (key=%s)", key)
        return cached

    fresh = compute_fn()
    if should_cache_fn is None or should_cache_fn(fresh):
        set_cached(fresh, key=key, ttl_seconds=ttl_seconds)
        logger.info("M23 market_context cache filled (key=%s ttl=%ds)", key, ttl_seconds)
    else:
        logger.info("M23 market_context not cached (compute_fn returned non-cacheable)")
    return fresh


def _reset_for_tests() -> None:
    """測試專用：清空 cache。"""
    with _CACHE_LOCK:
        _cache.clear()
