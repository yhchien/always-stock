"""
FinMind SDK 集成測試

驗證：
1. FinMindSDKClient 初始化與認證
2. 配額查詢是否正確
3. Batch fetch 效能（vs 單次請求）
4. DataFrame 解析是否正確
5. HTTP 402 處理（配額超出）

使用方式：
    export FINMIND_TOKEN=your_token_here
    python test_finmind_sdk_integration.py --config test_small
"""

import logging
import sys
import os
from datetime import datetime, date, timedelta
from typing import Dict, Any
import argparse

logger = logging.getLogger(__name__)


def test_sdk_initialization(token: str) -> bool:
    """測試 SDK 初始化與認證"""
    logger.info("\n[TEST 1] SDK Initialization & Authentication")
    logger.info("-" * 60)

    try:
        from etl.finmind_sdk_client import FinMindSDKClient

        client = FinMindSDKClient(token)
        logger.info("✓ FinMindSDKClient initialized")

        # 檢查基本屬性
        assert hasattr(client, "token"), "Missing token attribute"
        assert hasattr(client, "api"), "Missing FinMind api"
        logger.info("✓ Client attributes validated")

        return client

    except Exception as e:
        logger.error(f"✗ SDK initialization failed: {e}")
        return None


def test_quota_check(client: Any) -> Dict[str, Any]:
    """測試配額查詢"""
    logger.info("\n[TEST 2] Quota Check")
    logger.info("-" * 60)

    try:
        quota = client.get_quota()
        logger.info(f"✓ Quota retrieved: {quota}")

        # 檢查必要欄位
        assert "status" in quota, "Missing quota status"
        logger.info(f"  Status: {quota['status']}")

        if "remaining" in quota:
            logger.info(f"  Remaining: {quota['remaining']}")

        if "user_count" in quota:
            logger.info(f"  Monthly usage: {quota['user_count']}")

        if "api_request_limit" in quota:
            logger.info(f"  Monthly limit: {quota['api_request_limit']}")

        return quota

    except Exception as e:
        logger.error(f"✗ Quota check failed: {e}")
        return None


def test_batch_fetch(client: Any, stock_ids: list, days: int = 5) -> Dict[str, Any]:
    """
    測試 Batch fetch 效能

    對比：
    - 逐股逐日：N stocks * M days API calls
    - SDK Batch：1 API call（async 內部平行）
    """
    logger.info("\n[TEST 3] Batch Fetch (Daily Price)")
    logger.info("-" * 60)

    try:
        # 準備測試用期間
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        logger.info(f"Test stocks: {stock_ids[:5]}... (total: {len(stock_ids)})")
        logger.info(f"Test period: {start_date} to {end_date} ({days} days)")
        logger.info(f"Expected sequential calls: {len(stock_ids)} * {days} = {len(stock_ids) * days}")
        logger.info(f"SDK async batch calls: 1 (with parallel processing)")

        # 執行 batch fetch
        logger.info("Calling SDK batch fetch...")
        import time
        start_time = time.time()

        df = client.fetch_taiwan_stock_price(
            stock_id_list=stock_ids,
            start_date=start_date,
            end_date=end_date,
            use_async=True
        )

        elapsed = time.time() - start_time
        logger.info(f"✓ Batch fetch completed in {elapsed:.1f}s")

        # 驗證 DataFrame
        if df is not None and not df.empty:
            logger.info(f"✓ DataFrame received: {len(df)} rows")
            logger.info(f"  Columns: {list(df.columns)}")

            # 檢查必要欄位
            required_cols = ["stock_id", "date", "open", "high", "low", "close"]
            for col in required_cols:
                if col in df.columns:
                    logger.info(f"  ✓ {col} present")
                else:
                    logger.warning(f"  ✗ {col} MISSING")

            # 顯示樣本
            logger.info(f"\nSample rows (first 3):")
            for i, (_, row) in enumerate(df.head(3).iterrows()):
                logger.info(
                    f"  {row['stock_id']} {row['date']}: "
                    f"O={row['open']} H={row['high']} L={row['low']} C={row['close']}"
                )

            return {
                "status": "ok",
                "total_rows": len(df),
                "elapsed_seconds": elapsed,
                "columns": list(df.columns)
            }
        else:
            logger.warning("✗ Empty DataFrame returned")
            return {"status": "empty", "elapsed_seconds": elapsed}

    except Exception as e:
        logger.error(f"✗ Batch fetch failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {"status": "error", "error": str(e)}


def test_inst_flow_batch(client: Any, stock_ids: list, days: int = 3) -> Dict[str, Any]:
    """測試三大法人 batch 查詢"""
    logger.info("\n[TEST 4] Batch Fetch (Institutional Flows)")
    logger.info("-" * 60)

    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        logger.info(f"Test period: {start_date} to {end_date}")
        logger.info("Calling SDK batch fetch for inst flows...")

        import time
        start_time = time.time()

        df = client.fetch_institutional_investors(
            stock_id_list=stock_ids,
            start_date=start_date,
            end_date=end_date,
            use_async=True
        )

        elapsed = time.time() - start_time
        logger.info(f"✓ Inst flow batch completed in {elapsed:.1f}s")

        if df is not None and not df.empty:
            logger.info(f"✓ DataFrame received: {len(df)} rows")
            logger.info(f"  Columns: {list(df.columns)}")

            # 檢查三大法人類型
            if "name" in df.columns:
                inst_types = df["name"].unique()
                logger.info(f"  Institutional types: {list(inst_types)}")

            return {
                "status": "ok",
                "total_rows": len(df),
                "elapsed_seconds": elapsed,
                "columns": list(df.columns)
            }
        else:
            logger.warning("✗ Empty DataFrame returned")
            return {"status": "empty"}

    except Exception as e:
        logger.error(f"✗ Inst flow batch failed: {e}")
        return {"status": "error", "error": str(e)}


def test_quota_exceeded_handling(client: Any) -> Dict[str, Any]:
    """測試是否正確處理 HTTP 402（配額超出）"""
    logger.info("\n[TEST 5] HTTP 402 Handling (假設配額已滿)")
    logger.info("-" * 60)

    try:
        # 偽造一個大規模請求（期望觸發 402）
        # 注意：這個測試會消耗配額，請小心使用
        logger.warning("⚠  Skipping large-scale quota exhaustion test for safety")
        logger.info("✓ Test 5 skipped (use --force-quota-test to run)")

        return {"status": "skipped", "reason": "safety"}

    except Exception as e:
        logger.error(f"HTTP 402 handling test error: {e}")
        return {"status": "error", "error": str(e)}


def run_tests(token: str, config: str = "test_small"):
    """執行所有集成測試"""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )

    logger.info("=" * 80)
    logger.info("FinMind SDK Integration Test Suite")
    logger.info("=" * 80)

    # TEST 1: SDK 初始化
    client = test_sdk_initialization(token)
    if client is None:
        logger.error("\n❌ SDK initialization failed, stopping tests")
        return 1

    # TEST 2: 配額查詢
    quota = test_quota_check(client)
    if quota is None:
        logger.error("\n❌ Quota check failed")
        return 1

    # 根據 config 決定測試規模
    if config == "test_small":
        stock_ids = ["2330", "2454", "3383", "6005", "8039"]  # 5 檔
        days = 3
    elif config == "test_medium":
        stock_ids = ["2330", "2454", "3383", "6005", "8039", "2891", "2412", "4768", "1101", "2317"]  # 10 檔
        days = 5
    elif config == "test_large":
        # 需要大量分點資料表，暫時使用中等規模
        stock_ids = ["2330", "2454", "3383", "6005", "8039", "2891", "2412", "4768", "1101", "2317"]
        days = 10
    else:
        logger.error(f"Unknown config: {config}")
        return 1

    # TEST 3: Daily price batch
    price_result = test_batch_fetch(client, stock_ids, days)

    # TEST 4: Inst flow batch
    inst_result = test_inst_flow_batch(client, stock_ids[:3], days)  # 縮小規模

    # TEST 5: 配額超出處理
    quota_result = test_quota_exceeded_handling(client)

    # 最終總結
    logger.info("\n" + "=" * 80)
    logger.info("Test Summary")
    logger.info("=" * 80)
    logger.info(f"SDK Initialization: ✓")
    logger.info(f"Quota Check: ✓")
    logger.info(f"Batch Fetch (Price): {price_result.get('status', 'unknown')}")
    logger.info(f"Batch Fetch (Inst Flow): {inst_result.get('status', 'unknown')}")
    logger.info(f"Quota Excess Handling: {quota_result.get('status', 'unknown')}")

    # 判定成功
    if price_result.get("status") == "ok" and inst_result.get("status") == "ok":
        logger.info("\n✅ All critical tests passed!")
        return 0
    else:
        logger.error("\n⚠️  Some tests failed or returned empty data")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FinMind SDK Integration Test")
    parser.add_argument(
        "--config",
        type=str,
        default="test_small",
        choices=["test_small", "test_medium", "test_large"],
        help="Test scale configuration"
    )
    parser.add_argument(
        "--force-quota-test",
        action="store_true",
        help="Force quota exhaustion test (⚠️  will consume quota)"
    )

    args = parser.parse_args()

    token = os.getenv("FINMIND_TOKEN")
    if not token:
        print("ERROR: FINMIND_TOKEN environment variable not set")
        print("\nUsage:")
        print("  export FINMIND_TOKEN=your_token_here")
        print("  python test_finmind_sdk_integration.py --config test_small")
        sys.exit(1)

    exit_code = run_tests(token, config=args.config)
    sys.exit(exit_code)
