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

    # ------------------------------------------------------------------
    # Dataset-level fetch (no data_id) — 1 quota per call, regardless of
    # how many stocks the dataset contains. 用於每日 ETL：拿全市場單日資料
    # 後在 ETL 模組 layer 過濾 stocks_master 範圍。
    # ------------------------------------------------------------------
    def _fetch_dataset_for_range(
        self,
        dataset: str,
        start_date: str,
        end_date: str,
    ) -> Any:
        """
        對 FinMind v4 REST 打單一 dataset（不帶 data_id），回傳 pandas DataFrame。
        每個 dataset / 區間只計 1 配額。
        """
        import requests
        import pandas as pd

        if not self.can_proceed():
            raise RuntimeError("Insufficient quota or critical state")

        logger.info(
            f"Fetching dataset {dataset} from {start_date} to {end_date} "
            f"(no data_id, dataset-level batch)"
        )

        try:
            resp = requests.get(
                "https://api.finmindtrade.com/api/v4/data",
                params={
                    "dataset": dataset,
                    "start_date": start_date,
                    "end_date": end_date,
                    "token": self.token,
                },
                timeout=120,
            )
            if resp.status_code == 402:
                logger.error(f"402 quota exceeded for dataset {dataset}")
                self._refresh_quota()
                raise RuntimeError("Insufficient quota or critical state")
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data") or []
            df = pd.DataFrame(data) if data else pd.DataFrame()

            self._refresh_quota()
            logger.info(f"✓ Fetched {len(df)} {dataset} records (dataset-level)")
            return df

        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"Failed to fetch dataset {dataset}: {e}")
            raise

    def fetch_taiwan_stock_price_dataset(
        self,
        start_date: str,
        end_date: str,
    ) -> Any:
        """單日 / 區間全市場每日股價（1 quota）"""
        return self._fetch_dataset_for_range("TaiwanStockPrice", start_date, end_date)

    def fetch_inst_investors_buysell_dataset(
        self,
        start_date: str,
        end_date: str,
    ) -> Any:
        """單日 / 區間全市場個股法人買賣超（1 quota）

        注意：FinMind v4 已淘汰 TaiwanStockInstitutionalInvestors，
        改用 TaiwanStockInstitutionalInvestorsBuySell。
        """
        return self._fetch_dataset_for_range(
            "TaiwanStockInstitutionalInvestorsBuySell", start_date, end_date
        )

    def fetch_per_dataset(
        self,
        start_date: str,
        end_date: str,
    ) -> Any:
        """單日 / 區間全市場 PER/PBR/殖利率（1 quota）"""
        return self._fetch_dataset_for_range("TaiwanStockPER", start_date, end_date)

    def fetch_month_revenue_dataset(
        self,
        start_date: str,
        end_date: str,
    ) -> Any:
        """區間全市場月營收（1 quota）"""
        return self._fetch_dataset_for_range(
            "TaiwanStockMonthRevenue", start_date, end_date
        )

    def fetch_financial_statements_dataset(
        self,
        start_date: str,
        end_date: str,
    ) -> Any:
        """區間全市場財報（1 quota）"""
        return self._fetch_dataset_for_range(
            "TaiwanStockFinancialStatements", start_date, end_date
        )

    def fetch_margin_short_sale_dataset(
        self,
        start_date: str,
        end_date: str,
    ) -> Any:
        """區間全市場融資融券餘額（1 quota）"""
        return self._fetch_dataset_for_range(
            "TaiwanStockMarginPurchaseShortSale", start_date, end_date
        )

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
            # 優先嘗試 SDK batch 方法
            per_method = None
            for candidate in ('taiwan_stock_per_pbr', 'taiwan_stock_per'):
                if hasattr(self.api, candidate):
                    per_method = candidate
                    break

            if per_method:
                df = getattr(self.api, per_method)(
                    stock_id_list=stock_id_list,
                    start_date=start_date,
                    end_date=end_date,
                    use_async=use_async,
                )
            else:
                # SDK 不支援，fallback 至 REST API（逐批抓取，每批 50 支）
                import requests
                import pandas as pd

                logger.warning(
                    "SDK has no taiwan_stock_per_pbr / taiwan_stock_per. "
                    "Falling back to REST API (batches of 50)."
                )
                rows = []
                batch_size = 50
                for i in range(0, len(stock_id_list), batch_size):
                    batch = stock_id_list[i: i + batch_size]
                    for stock_id in batch:
                        resp = requests.get(
                            "https://api.finmindtrade.com/api/v4/data",
                            params={
                                "dataset": "TaiwanStockPER",
                                "data_id": stock_id,
                                "start_date": start_date,
                                "end_date": end_date,
                                "token": self.token,
                            },
                            timeout=30,
                        )
                        if resp.status_code == 402:
                            logger.error("402 quota exceeded during PER REST fallback")
                            break
                        data = resp.json().get("data", [])
                        rows.extend(data)
                df = pd.DataFrame(rows) if rows else pd.DataFrame()

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

    def fetch_broker_trade_by_date(
        self,
        stock_id_list: List[str],
        trade_date: str,
        use_async: bool = True,
    ) -> Any:
        """
        抓取單一交易日所有股票的分點買賣資料，並在本地 group by 分點做聚合。
        資料集：TaiwanStockTradingDailyReport（每支股票 1 request）
        回傳已聚合的 DataFrame：date, stock_id, broker_id, broker_name, buy_shares, sell_shares

        注意：TaiwanStockTradingDailyReportSecIdAgg 在 API/SDK 中不存在，
              改用 TaiwanStockTradingDailyReport 自行聚合。
        """
        import pandas as pd

        if not self.can_proceed():
            raise RuntimeError("Insufficient quota or critical state")

        logger.info(
            f"Fetching broker trade for {len(stock_id_list)} stocks on {trade_date} (async={use_async})"
        )

        try:
            df_raw = self.api.taiwan_stock_trading_daily_report(
                stock_id_list=stock_id_list,
                date=trade_date,
                use_async=use_async,
            )

            self._refresh_quota()

            if df_raw is None or df_raw.empty:
                logger.info(f"No broker trade data for {trade_date}")
                return pd.DataFrame()

            # group by (date, stock_id, broker) 加總 buy/sell（原始資料為逐價位明細）
            df_agg = (
                df_raw.groupby(
                    ["date", "stock_id", "securities_trader_id", "securities_trader"],
                    as_index=False,
                )
                .agg(buy_shares=("buy", "sum"), sell_shares=("sell", "sum"))
            )
            df_agg["net_shares"] = df_agg["buy_shares"] - df_agg["sell_shares"]
            df_agg = df_agg.rename(columns={
                "securities_trader_id": "broker_id",
                "securities_trader": "broker_name",
            })

            logger.info(
                f"✓ {trade_date}: {len(df_raw)} raw rows → {len(df_agg)} broker-level records"
            )
            return df_agg

        except Exception as e:
            logger.warning(f"Failed to fetch broker trade for {trade_date}: {e}")
            raise

    def fetch_margin_purchase_short_sale(
        self,
        stock_id_list: List[str],
        start_date: str,
        end_date: str,
        use_async: bool = True,
    ) -> Any:
        """
        批量抓取融資融券每日餘額（M23 訊號管線使用）
        資料集：TaiwanStockMarginPurchaseShortSale

        FinMind 回傳欄位：
            date, stock_id,
            MarginPurchaseTodayBalance, MarginPurchaseYesterdayBalance,
            ShortSaleTodayBalance, ShortSaleYesterdayBalance, ...

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
            f"Fetching {len(stock_id_list)} stocks margin/short sale data "
            f"from {start_date} to {end_date} (async={use_async})"
        )

        try:
            if not hasattr(self.api, 'taiwan_stock_margin_purchase_short_sale'):
                logger.warning(
                    "SDK has no taiwan_stock_margin_purchase_short_sale method; skipping."
                )
                return None

            df = self.api.taiwan_stock_margin_purchase_short_sale(
                stock_id_list=stock_id_list,
                start_date=start_date,
                end_date=end_date,
                use_async=use_async,
            )

            self._refresh_quota()
            logger.info(f"✓ Fetched {len(df) if df is not None else 0} margin/short sale records")

            return df

        except Exception as e:
            logger.error(f"Failed to fetch margin_purchase_short_sale: {e}")
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
