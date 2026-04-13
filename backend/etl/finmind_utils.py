"""
FinMind ETL 輔助工具模組
包含配額查詢、市場代碼映射、資料驗證等共用函式
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import date
from etl.http_client import FinMindHTTPClient

logger = logging.getLogger(__name__)


# ============================================================================
# FinMind 常數與配置
# ============================================================================

FINMIND_INST_TYPES_MAPPING = {
    # 中文名稱（舊 TWSE 格式）
    "外資": "foreign",
    "投信": "trust",
    "自營商": "dealer",
    # FinMind SDK 英文名稱（實際回傳）
    "Foreign_Investor": "foreign",
    "Foreign_Dealer_Self": "foreign",   # 外資自行買賣，合併入外資
    "Investment_Trust": "trust",
    "Dealer_self": "dealer",
    "Dealer_Hedging": "dealer",         # 自營商避險，合併入自營商
    # 內部正規名稱
    "foreign": "foreign",
    "trust": "trust",
    "dealer": "dealer",
}

# 上市公司股票代碼範圍（TWSE）
TWSE_STOCK_CODE_PATTERNS = {
    "range": (1000, 9992),  # 標準上市代碼
    "excludes": [],  # 排除的特殊代碼
}


# ============================================================================
# 輔助函式
# ============================================================================

def check_finmind_quota(client: FinMindHTTPClient) -> Dict[str, Any]:
    """
    檢查 FinMind API 配額使用狀況

    Args:
        client: FinMind HTTP 客戶端

    Returns:
        {
            "quota_total": int,
            "quota_used": int,
            "quota_remaining": int,
            "quota_usage_pct": float,
            "status": "ok" | "warning" | "critical",
            "recommendation": str,
        }
    """
    try:
        quota_info = client.check_quota()

        # FinMind API 回應格式可能因版本而異，這裡使用通用解析
        data = quota_info.get("data", {})

        quota_total = data.get("quota", 0)
        quota_used = data.get("usage", 0)
        quota_remaining = quota_total - quota_used

        if quota_total > 0:
            usage_pct = (quota_used / quota_total) * 100
        else:
            usage_pct = 0

        # 判定狀態
        if usage_pct >= 90:
            status = "critical"
            recommendation = "⚠️  配額即將用完，建議停止新增請求"
        elif usage_pct >= 70:
            status = "warning"
            recommendation = "⚠️  配額使用率較高，建議監控"
        else:
            status = "ok"
            recommendation = "正常"

        return {
            "quota_total": quota_total,
            "quota_used": quota_used,
            "quota_remaining": quota_remaining,
            "quota_usage_pct": usage_pct,
            "status": status,
            "recommendation": recommendation,
        }

    except Exception as e:
        logger.warning(f"Failed to check quota: {e}")
        return {
            "quota_total": None,
            "quota_used": None,
            "quota_remaining": None,
            "quota_usage_pct": None,
            "status": "unknown",
            "recommendation": "無法查詢配額",
        }


def normalize_inst_type(finmind_name: str) -> Optional[str]:
    """
    將 FinMind 的法人名稱映射到內部標準化名稱

    Args:
        finmind_name: FinMind API 回傳的名稱 (e.g., "外資", "投信", "自營商")

    Returns:
        標準化名稱 ("foreign", "trust", "dealer") 或 None
    """
    normalized = FINMIND_INST_TYPES_MAPPING.get(finmind_name.strip())
    if normalized:
        return normalized

    # 備用邏輯：模糊匹配
    name_lower = finmind_name.lower()
    if "foreign" in name_lower or "外資" in finmind_name:
        return "foreign"
    elif "trust" in name_lower or "投信" in finmind_name:
        return "trust"
    elif "dealer" in name_lower or "自營" in finmind_name:
        return "dealer"

    logger.warning(f"Unknown inst_type: {finmind_name}")
    return None


def is_valid_twse_stock(stock_id: str) -> bool:
    """
    檢查股票代碼是否為合法的上市公司代碼

    Args:
        stock_id: 股票代碼

    Returns:
        True 如果是有效的 TWSE 上市公司代碼
    """
    try:
        code = int(stock_id)
        min_code, max_code = TWSE_STOCK_CODE_PATTERNS["range"]

        if code < min_code or code > max_code:
            return False

        # 檢查排除清單
        if code in TWSE_STOCK_CODE_PATTERNS["excludes"]:
            return False

        return True

    except ValueError:
        return False


def calculate_est_amount(shares: float, price: float) -> float:
    """
    由股數和價格估算金額

    Args:
        shares: 股數
        price: 每股價格（新台幣）

    Returns:
        估算金額（新台幣）或 0 如果計算失敗
    """
    try:
        if shares is None or price is None or price <= 0:
            return 0.0

        return float(shares) * float(price)
    except (TypeError, ValueError):
        return 0.0


def validate_price_data(data: Dict[str, Any]) -> bool:
    """
    驗證每日價格資料的完整性

    Args:
        data: FinMind API 回傳的資料字典

    Returns:
        True 如果資料有效
    """
    required_fields = ["open", "high", "low", "close", "volume"]

    for field in required_fields:
        if field not in data or data[field] is None:
            logger.warning(f"Missing or None field: {field}")
            return False

    # 邏輯驗證
    try:
        high = float(data.get("high", 0))
        low = float(data.get("low", 0))
        close = float(data.get("close", 0))
        open_price = float(data.get("open", 0))

        # high >= low >= close （通常成立）
        if high < low:
            logger.warning(f"Invalid price data: high ({high}) < low ({low})")
            return False

        # volume >= 0
        volume = float(data.get("volume", 0))
        if volume < 0:
            logger.warning(f"Negative volume: {volume}")
            return False

        return True

    except (TypeError, ValueError) as e:
        logger.warning(f"Price validation error: {e}")
        return False


def batch_fetch_with_throttle(
    client: FinMindHTTPClient,
    dataset: str,
    stock_ids: List[str],
    date_str: str,
    concurrent: int = 1,
    delay_per_request: float = 0.5,
) -> Dict[str, Any]:
    """
    批量抓取，支援節流和並行控制

    Args:
        client: FinMind HTTP 客戶端
        dataset: 資料集名稱
        stock_ids: 股票代碼列表
        date_str: 日期字串 (YYYY-MM-DD)
        concurrent: 並行度 (暫時使用 1)
        delay_per_request: 請求間隔（秒）

    Returns:
        {
            "success": [stock_id, ...],
            "failed": [stock_id, ...],
            "data": {stock_id: api_response, ...},
        }
    """
    results = {
        "success": [],
        "failed": [],
        "data": {},
    }

    for i, stock_id in enumerate(stock_ids):
        try:
            data = client.fetch(
                dataset,
                stock_id=stock_id,
                date=date_str,
            )

            if data.get("status") == 200:
                results["success"].append(stock_id)
                results["data"][stock_id] = data
            else:
                results["failed"].append(stock_id)
                logger.warning(f"API returned status {data.get('status')} for {stock_id}")

        except Exception as e:
            results["failed"].append(stock_id)
            logger.warning(f"Failed to fetch {stock_id}: {e}")

        # 節流
        if i < len(stock_ids) - 1:
            import time
            time.sleep(delay_per_request)

    return results


# ============================================================================
# 日期工具
# ============================================================================

def is_trading_day(check_date: date) -> bool:
    """
    簡易判定是否為交易日（排除週末）
    注意：不排除台灣股市假日

    Args:
        check_date: 檢查日期

    Returns:
        True 如果不在星期六或日
    """
    return check_date.weekday() < 5


def get_trading_days_in_month(year: int, month: int) -> List[date]:
    """
    取得指定月份的所有交易日（簡易版，只排除週末）

    Args:
        year: 年份
        month: 月份

    Returns:
        交易日期列表
    """
    from datetime import timedelta
    import calendar

    trading_days = []
    last_day = calendar.monthrange(year, month)[1]

    for day in range(1, last_day + 1):
        check_date = date(year, month, day)
        if is_trading_day(check_date):
            trading_days.append(check_date)

    return trading_days
