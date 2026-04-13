"""
FinMind HTTP 防斷線客戶端
提供自動重試、指數退避、速率限制處理、配額查詢等功能
"""

import time
import logging
import json
from typing import Any, Dict, Optional, List
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class FinMindHTTPClient:
    """
    FinMind 專用 HTTP 客戶端，支援：
    - 自動重試（指數退避）
    - Rate limit 檢測 & 回退
    - 斷線恢復
    - 請求計數與配額跟蹤
    - 詳細日誌
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.finmindtrade.com/api/v4",
        web_base_url: str = "https://api.web.finmindtrade.com/v2",
        timeout: int = 30,
        max_retries: int = 5,
        backoff_factor: float = 2.0,
    ):
        """
        初始化 FinMind 客戶端

        Args:
            token: FinMind API token
            base_url: FinMind API base URL
            web_base_url: FinMind Web API base URL (for quota check)
            timeout: 請求逾時秒數
            max_retries: 最大重試次數
            backoff_factor: 指數退避係數 (2 = 2, 4, 8, 16, 32 秒)
        """
        self.token = token
        self.base_url = base_url
        self.web_base_url = web_base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

        self.session = self._create_session()
        self.request_count = 0
        self.last_rate_limit_reset = None

    def _create_session(self) -> requests.Session:
        """建立 session，設定指數退避重試策略"""
        session = requests.Session()

        # 指數退避重試策略
        retry_strategy = Retry(
            total=self.max_retries,
            backoff_factor=self.backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],  # 速率限制 & 伺服器錯誤
            allowed_methods=["GET", "POST"],
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # 預設 headers
        session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "always-stock/1.0",
            "Content-Type": "application/json",
        })

        return session

    def fetch(
        self,
        dataset: str,
        **params
    ) -> Dict[str, Any]:
        """
        從 FinMind 抓取資料，自動處理重試與速率限制

        Args:
            dataset: 資料集名稱 (e.g., "TaiwanStockPrice", "TaiwanStockInstitutionalInvestorsBuySell")
            **params: 查詢參數 (stock_id, date, etc.)

        Returns:
            API 回應的 JSON 字典

        Raises:
            requests.exceptions.RequestException: 在超過重試次數後仍失敗
        """
        url = f"{self.base_url}/data"

        query_params = {
            "dataset": dataset,
            **params
        }

        logger.info(f"Fetching {dataset} with params: {params}")

        try:
            resp = self.session.get(
                url,
                params=query_params,
                timeout=self.timeout
            )
            self.request_count += 1

            # 檢查速率限制 headers
            self._check_rate_limit_headers(resp.headers)

            resp.raise_for_status()

            data = resp.json()

            # 檢查 API 回應狀態
            if data.get("status") != 200:
                logger.warning(f"FinMind API non-200 status: {data.get('status')}")
                logger.warning(f"Message: {data.get('message')}")

                # 429 = Too Many Requests
                if data.get("status") == 429:
                    wait_seconds = self._get_rate_limit_wait()
                    logger.error(f"Rate limit hit, please wait {wait_seconds}s")
                    raise requests.exceptions.HTTPError(
                        f"Rate limited (429), wait {wait_seconds}s"
                    )

            logger.debug(f"Response status: {data.get('status')}")
            return data

        except requests.exceptions.Timeout as e:
            logger.error(f"Request timeout for {dataset}: {e}")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error for {dataset}: {e}")
            raise
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error for {dataset}: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON for {dataset}: {e}")
            raise

    def _check_rate_limit_headers(self, headers: Dict[str, str]) -> None:
        """檢查並記錄速率限制 headers"""
        limit = headers.get("X-RateLimit-Limit")
        remaining = headers.get("X-RateLimit-Remaining")
        reset = headers.get("X-RateLimit-Reset")

        if remaining:
            logger.info(f"Rate limit: {remaining}/{limit}, reset at {reset}")

            # 如果剩餘次數少於 10，發出警告
            try:
                remaining_int = int(remaining)
                if remaining_int < 10:
                    logger.warning(f"Rate limit nearly exhausted: {remaining_int} requests remaining")
            except ValueError:
                pass

    def _get_rate_limit_wait(self) -> int:
        """計算需要等待的秒數（使用指數退避邏輯）"""
        # 簡單實作：如果被限流，等待 60 秒
        return 60

    def batch_fetch(
        self,
        dataset: str,
        params_list: List[Dict[str, Any]],
        delay_between_requests: float = 1.0,
    ) -> List[Dict[str, Any]]:
        """
        批量抓取，含延遲以避免頻繁觸發速率限制

        Args:
            dataset: 資料集名稱
            params_list: 參數字典列表
            delay_between_requests: 請求間隔秒數

        Returns:
            API 回應列表
        """
        results = []

        for i, params in enumerate(params_list):
            try:
                data = self.fetch(dataset, **params)
                results.append(data)

                # 不是最後一個請求時，加入延遲
                if i < len(params_list) - 1:
                    logger.debug(f"Waiting {delay_between_requests}s before next request")
                    time.sleep(delay_between_requests)

            except Exception as e:
                logger.error(f"Batch fetch failed for params {params}: {e}")
                results.append({"status": 500, "error": str(e)})

        return results

    def check_quota(self) -> Dict[str, Any]:
        """
        查詢 API 配額使用狀況

        Returns:
            配額資訊字典，包含 quota, usage, remaining 等

        Raises:
            requests.exceptions.RequestException: 查詢失敗
        """
        url = f"{self.web_base_url}/user_info"

        logger.info("Checking quota...")

        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()

            data = resp.json()
            logger.info(f"Quota info: {data}")

            return data

        except Exception as e:
            logger.error(f"Failed to check quota: {e}")
            raise

    def get_request_count(self) -> int:
        """取得本次 session 累計請求次數"""
        return self.request_count

    def reset_request_count(self) -> None:
        """重設請求計數"""
        self.request_count = 0
        logger.info("Request count reset")


# ============================================================================
# 工具函式
# ============================================================================

def create_finmind_client(token: str, **kwargs) -> FinMindHTTPClient:
    """
    工廠函式：建立 FinMind 客戶端

    Args:
        token: FinMind API token
        **kwargs: 傳遞給 FinMindHTTPClient 的其他參數

    Returns:
        FinMindHTTPClient 實例
    """
    return FinMindHTTPClient(token, **kwargs)


# ============================================================================
# 測試用
# ============================================================================

if __name__ == "__main__":
    # 基本測試
    logging.basicConfig(level=logging.INFO)

    # 需要實際 token 才能測試
    # client = FinMindHTTPClient(token="your_token_here")
    # data = client.fetch("TaiwanStockInfo", market_type="TSE")
    # print(data)
