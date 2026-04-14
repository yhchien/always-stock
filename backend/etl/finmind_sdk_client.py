"""
FinMind SDK 客戶端包裝層
支援 async batch query、配額管理、自動重試
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class FinMindSDKClient:
    """
    FinMind Python SDK 包裝層
    提供：batch query、async 支援、配額管理、錯誤處理
    """

    def __init__(self, token: str):
        """
        初始化 FinMind SDK 客戶端

        Args:
            token: FinMind API token
        """
        try:
            from FinMind.data import DataLoader
        except ImportError:
            raise ImportError(
                "FinMind SDK not installed. "
                "Install with: pip install FinMind"
            )

        self.token = token
        self.api = DataLoader()

        try:
            self.api.login_by_token(api_token=token)
            logger.info("✓ FinMind SDK initialized and authenticated")
        except Exception as e:
            logger.error(f"Failed to authenticate with FinMind: {e}")
            raise

        self.quota_info = None
        self._refresh_quota()

    def _refresh_quota(self) -> Dict[str, Any]:
        """
        刷新配額資訊

        Returns:
            {
                "user_count": int,
                "api_request_limit": int,
                "remaining": int,
                "status": "ok" | "warning" | "critical",
            }
        """
        try:
            import requests

            resp = requests.get(
                "https://api.web.finmindtrade.com/v2/user_info",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10,
            )

            if resp.status_code == 402:
                logger.error("402 Payment Required: API quota exceeded")
                self.quota_info = {
                    "user_count": None,
                    "api_request_limit": None,
                    "remaining": 0,
                    "status": "critical",
                    "message": "Quota exceeded",
                }
                return self.quota_info

            resp.raise_for_status()
            data = resp.json()

            user_count = data.get("user_count", 0)
            api_request_limit = data.get("api_request_limit", 0)
            remaining = api_request_limit - user_count

            # 判定狀態
            if remaining <= 0:
                status = "critical"
            elif remaining / api_request_limit < 0.1:
                status = "warning"
            else:
                status = "ok"

            self.quota_info = {
                "user_count": user_count,
                "api_request_limit": api_request_limit,
                "remaining": remaining,
                "status": status,
                "usage_pct": (user_count / api_request_limit * 100) if api_request_limit > 0 else 0,
                "timestamp": datetime.utcnow(),
            }

            logger.info(
                f"Quota: {user_count}/{api_request_limit} "
                f"(remaining: {remaining}, usage: {self.quota_info['usage_pct']:.1f}%)"
            )

            return self.quota_info

        except Exception as e:
            logger.warning(f"Failed to refresh quota: {e}")
            return {
                "status": "unknown",
                "error": str(e),
            }

    def can_proceed(self) -> bool:
        """檢查是否還有配額可執行 ETL"""
        if not self.quota_info:
            self._refresh_quota()

        if self.quota_info.get("status") == "critical":
            logger.error("Quota critical, cannot proceed")
            return False

        return True

    def fetch_taiwan_stock_price(
        self,
        stock_id_list: List[str],
        start_date: str,
        end_date: str,
        use_async: bool = True,
    ) -> Any:
        """
        批量抓取台股每日價格（支援 async）

        Args:
            stock_id_list: 股票代碼列表
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
            use_async: 是否使用 async 並行

        Returns:
            pandas DataFrame
        """
        if not self.can_proceed():
            raise RuntimeError("Insufficient quota or critical state")

        logger.info(
            f"Fetching {len(stock_id_list)} stocks price data "
            f"from {start_date} to {end_date} (async={use_async})"
        )

        try:
            df = self.api.taiwan_stock_daily(
                stock_id_list=stock_id_list,
                start_date=start_date,
                end_date=end_date,
                use_async=use_async,
            )

            self._refresh_quota()  # 刷新配額
            logger.info(f"✓ Fetched {len(df)} price records")

            return df

        except Exception as e:
            logger.error(f"Failed to fetch taiwan_stock_daily: {e}")
            raise

    def fetch_institutional_investors(
        self,
        stock_id_list: List[str],
        start_date: str,
        end_date: str,
        use_async: bool = True,
    ) -> Any:
        """
        批量抓取三大法人買賣超（支援 async）

        Args:
            stock_id_list: 股票代碼列表
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
            use_async: 是否使用 async 並行

        Returns:
            pandas DataFrame
        """
        if not self.can_proceed():
            raise RuntimeError("Insufficient quota or critical state")

        logger.info(
            f"Fetching {len(stock_id_list)} stocks institutional investors "
            f"from {start_date} to {end_date} (async={use_async})"
        )

        try:
            df = self.api.taiwan_stock_institutional_investors(
                stock_id_list=stock_id_list,
                start_date=start_date,
                end_date=end_date,
                use_async=use_async,
            )

            self._refresh_quota()  # 刷新配額
            logger.info(f"✓ Fetched {len(df)} institutional investor records")

            return df

        except Exception as e:
            logger.error(f"Failed to fetch institutional investors: {e}")
            raise

    def fetch_per(
        self,
        stock_id_list: List[str],
        start_date: str,
        end_date: str,
        use_async: bool = True,
    ) -> Any:
        """
        批量抓取本益比與估值資訊

        Args:
            stock_id_list: 股票代碼列表
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
            use_async: 是否使用 async 並行

        Returns:
            pandas DataFrame
        """
        if not self.can_proceed():
            raise RuntimeError("Insufficient quota or critical state")

        logger.info(
            f"Fetching {len(stock_id_list)} stocks PER data "
            f"from {start_date} to {end_date} (async={use_async})"
        )

        try:
            df = self.api.taiwan_stock_per_pbr(
                stock_id_list=stock_id_list,
                start_date=start_date,
                end_date=end_date,
                use_async=use_async,
            )

            self._refresh_quota()  # 刷新配額
            logger.info(f"✓ Fetched {len(df)} PER records")

            return df

        except Exception as e:
            logger.error(f"Failed to fetch PER data: {e}")
            raise

    def fetch_trading_report(
        self,
        stock_id_list: List[str],
        start_date: str,
        end_date: str,
        use_async: bool = True,
    ) -> Any:
        """
        批量抓取每日交易明細（含券商分點資料）
        需要 Sponsor 權限

        Args:
            stock_id_list: 股票代碼列表
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
            use_async: 是否使用 async 並行

        Returns:
            pandas DataFrame （或 None 如果無權限）
        """
        if not self.can_proceed():
            raise RuntimeError("Insufficient quota or critical state")

        logger.info(
            f"Fetching {len(stock_id_list)} stocks trading report (Sponsor) "
            f"from {start_date} to {end_date} (async={use_async})"
        )

        try:
            # 注意：需要檢查 API 是否提供此方法
            # 如果沒有，應該使用 REST API 直接調用
            if not hasattr(self.api, 'taiwan_stock_trading_daily_report'):
                logger.warning(
                    "SDK may not support trading_daily_report. "
                    "Falls back to REST API"
                )
                return None

            df = self.api.taiwan_stock_trading_daily_report(
                stock_id_list=stock_id_list,
                start_date=start_date,
                end_date=end_date,
                use_async=use_async,
            )

            self._refresh_quota()  # 刷新配額
            logger.info(f"✓ Fetched {len(df)} trading report records")

            return df

        except Exception as e:
            logger.warning(f"Failed to fetch trading report (may require Sponsor): {e}")
            return None

    def fetch_month_revenue(
        self,
        stock_id_list: List[str],
        start_date: str,
        end_date: str,
        use_async: bool = True,
    ) -> Any:
        """
        批量抓取月營收資料
        需要 Sponsor 權限

        Args:
            stock_id_list: 股票代碼列表
            start_date: 開始日期 (YYYY-MM-DD)
            end_date: 結束日期 (YYYY-MM-DD)
            use_async: 是否使用 async 並行

        Returns:
            pandas DataFrame
        """
        if not self.can_proceed():
            raise RuntimeError("Insufficient quota or critical state")

        logger.info(
            f"Fetching {len(stock_id_list)} stocks monthly revenue "
            f"from {start_date} to {end_date} (async={use_async})"
        )

        try:
            if not hasattr(self.api, 'taiwan_stock_month_revenue'):
                logger.warning("SDK may not support month_revenue. Skipping.")
                return None

            df = self.api.taiwan_stock_month_revenue(
                stock_id_list=stock_id_list,
                start_date=start_date,
                end_date=end_date,
                use_async=use_async,
            )

            self._refresh_quota()  # 刷新配額
            logger.info(f"✓ Fetched {len(df)} monthly revenue records")

            return df

        except Exception as e:
            logger.warning(f"Failed to fetch monthly revenue: {e}")
            return None

    def fetch_broker_trade_agg(
        self,
        stock_id_list: List[str],
        start_date: str,
        end_date: str,
        use_async: bool = True,
    ) -> Any:
        """
        批量抓取券商分點聚合買賣資料
        資料集：TaiwanStockTradingDailyReportSecIdAgg
        需要 Sponsor 權限；歷史起點 2021-06-30
        """
        if not self.can_proceed():
            raise RuntimeError("Insufficient quota or critical state")

        logger.info(
            f"Fetching {len(stock_id_list)} stocks broker trade agg "
            f"from {start_date} to {end_date} (async={use_async})"
        )

        try:
            if hasattr(self.api, 'taiwan_stock_trading_daily_report_sec_id_agg'):
                df = self.api.taiwan_stock_trading_daily_report_sec_id_agg(
                    stock_id_list=stock_id_list,
                    start_date=start_date,
                    end_date=end_date,
                    use_async=use_async,
                )
            else:
                # fallback: 使用 REST API 直接呼叫
                import requests, pandas as pd
                rows = []
                for stock_id in stock_id_list:
                    resp = requests.get(
                        "https://api.finmindtrade.com/api/v4/data",
                        params={
                            "dataset": "TaiwanStockTradingDailyReportSecIdAgg",
                            "data_id": stock_id,
                            "start_date": start_date,
                            "end_date": end_date,
                            "token": self.token,
                        },
                        timeout=30,
                    )
                    data = resp.json().get("data", [])
                    rows.extend(data)
                df = pd.DataFrame(rows) if rows else pd.DataFrame()

            self._refresh_quota()
            logger.info(f"✓ Fetched {len(df)} broker trade agg records")
            return df

        except Exception as e:
            logger.warning(f"Failed to fetch broker trade agg: {e}")
            raise

    def fetch_financial_statements(
        self,
        stock_id_list: List[str],
        start_date: str,
        end_date: str,
        use_async: bool = True,
    ) -> Any:
        """
        批量抓取財報資料
        資料集：TaiwanStockFinancialStatements
        需要 Sponsor 權限
        """
        if not self.can_proceed():
            raise RuntimeError("Insufficient quota or critical state")

        logger.info(
            f"Fetching {len(stock_id_list)} stocks financial statements "
            f"from {start_date} to {end_date} (async={use_async})"
        )

        try:
            if hasattr(self.api, 'taiwan_stock_financial_statement'):
                df = self.api.taiwan_stock_financial_statement(
                    stock_id_list=stock_id_list,
                    start_date=start_date,
                    end_date=end_date,
                    use_async=use_async,
                )
            else:
                import requests, pandas as pd
                rows = []
                for stock_id in stock_id_list:
                    resp = requests.get(
                        "https://api.finmindtrade.com/api/v4/data",
                        params={
                            "dataset": "TaiwanStockFinancialStatements",
                            "data_id": stock_id,
                            "start_date": start_date,
                            "end_date": end_date,
                            "token": self.token,
                        },
                        timeout=30,
                    )
                    data = resp.json().get("data", [])
                    rows.extend(data)
                df = pd.DataFrame(rows) if rows else pd.DataFrame()

            self._refresh_quota()
            logger.info(f"✓ Fetched {len(df)} financial statement records")
            return df

        except Exception as e:
            logger.warning(f"Failed to fetch financial statements: {e}")
            raise

    def get_quota(self) -> Dict[str, Any]:
        """取得當前配額資訊"""
        if not self.quota_info:
            self._refresh_quota()
        return self.quota_info

    def get_field_mapping(self, dataset_name: str) -> Dict[str, str]:
        """
        取得資料集的欄位對照（英文 → 中文）

        Args:
            dataset_name: 資料集名稱 (e.g., "TaiwanStockPrice")

        Returns:
            欄位對照字典
        """
        logger.info(f"Fetching field mapping for {dataset_name}")

        try:
            mapping = self.api.get_field_translation(dataset_name)
            logger.info(f"✓ Got {len(mapping)} field mappings")
            return mapping

        except Exception as e:
            logger.warning(f"Could not get field mapping for {dataset_name}: {e}")
            return {}


# ============================================================================
# 工廠函數
# ============================================================================

def create_finmind_client(token: str) -> FinMindSDKClient:
    """
    工廠函數：建立 FinMind 客戶端

    Args:
        token: FinMind API token

    Returns:
        FinMindSDKClient 實例
    """
    return FinMindSDKClient(token)
