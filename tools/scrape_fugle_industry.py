#!/usr/bin/env python3
"""
從 Fugle 產業頁面爬取所有主產業 → 子產業 → 股票清單。
只保留上市（TWSE）股票，過濾上櫃。

輸出（tools/output/）：
  - fugle_industry_mapping.json   巢狀結構，供前端使用
  - fugle_industry_mapping.csv    扁平結構，供 ETL 使用

用法：
  python3 -u tools/scrape_fugle_industry.py

需要套件：requests
"""

import json
import time
import csv
import sys
from collections import defaultdict
from pathlib import Path

import requests

sys.stdout.reconfigure(line_buffering=True)

FUGLE_BASE = "https://www.fugle.tw/api/v2/data"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

JSON_OUTPUT = OUTPUT_DIR / "fugle_industry_mapping.json"
CSV_OUTPUT  = OUTPUT_DIR / "fugle_industry_mapping.csv"

SLEEP = 0.4   # 每次 API 請求間隔（秒）
RETRIES = 3
RETRY_WAIT = 3

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.fugle.tw/industry",
})


# ── HTTP ────────────────────────────────────────────────────────────────────

def get_json(url: str) -> dict:
    """帶 retry 的 GET。4xx 直接 raise；5xx / timeout / 連線錯誤才重試。"""
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = SESSION.get(url, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status < 500:
                raise RuntimeError(f"HTTP {status}，不重試：{url}") from e
            last_err = f"HTTP {status}"
        except requests.exceptions.Timeout:
            last_err = "timeout"
        except requests.exceptions.ConnectionError:
            last_err = "connection error"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        print(f"    [warn] {last_err}（第 {attempt}/{RETRIES} 次），等 {RETRY_WAIT}s...")
        time.sleep(RETRY_WAIT)

    raise RuntimeError(f"連續失敗 {RETRIES} 次，放棄：{url}（最後錯誤：{last_err}）")


# ── FinMind ──────────────────────────────────────────────────────────────────

def fetch_twse_stock_info() -> dict[str, str]:
    """
    回傳 {stock_id: stock_name}，只含上市（TWSE）股票。
    FinMind 掛掉時回傳空 dict，後續不過濾也不補名稱。
    """
    print("從 FinMind 取得 TWSE 股票清單與名稱...")
    try:
        data = get_json(f"{FINMIND_URL}?dataset=TaiwanStockInfo")
    except RuntimeError as e:
        print(f"  [ERROR] FinMind 無法連線：{e}")
        print("  → fallback：所有股票都保留，名稱欄位留空")
        return {}

    stocks = data.get("data", [])
    if not stocks:
        print("  [WARN] FinMind 回傳空資料，可能 API 異常")
        return {}

    result = {s["stock_id"]: s["stock_name"]
              for s in stocks if s.get("type") == "twse"}
    print(f"  → TWSE 股票數：{len(result)}")
    return result


# ── Fugle ────────────────────────────────────────────────────────────────────

def fetch_main_topics() -> list[dict]:
    print("取得主產業列表...")
    data = get_json(f"{FUGLE_BASE}/industry-main-topics")
    topics = data["data"]["topics"]
    print(f"  → 主產業數：{len(topics)}")
    return topics


def fetch_sub_topics(main_code: str) -> list[dict]:
    data = get_json(f"{FUGLE_BASE}/industry-sub-topics/{main_code}")
    return data["data"].get("topics", [])


def fetch_symbol_ids(sub_code: str) -> list[str]:
    data = get_json(f"{FUGLE_BASE}/industry-topic-detail/{sub_code}")
    symbol_list = data["data"]["topic"].get("symbolList", [])
    return [s["symbolId"] for s in symbol_list]


# ── 建構巢狀結構 ──────────────────────────────────────────────────────────────

def build_nested(main_topics: list[dict],
                 twse_info: dict[str, str],
                 failed: list[str]) -> list[dict]:
    """
    回傳格式：
    [
      {
        "industry": "印刷電路板",
        "chain": [
          {
            "name": "上游",
            "sub_industries": [
              {
                "name": "玻璃纖維/玻纖布",
                "stocks": [
                  { "code": "1234", "name": "某公司" }
                ]
              }
            ]
          }
        ]
      }
    ]
    """
    filter_twse = len(twse_info) > 0
    result = []

    for i, main in enumerate(main_topics, 1):
        main_code = main["code"]
        main_name = main["name"]
        print(f"\n[{i}/{len(main_topics)}] {main_code} {main_name}")

        try:
            sub_topics = fetch_sub_topics(main_code)
            time.sleep(SLEEP)
        except RuntimeError as e:
            print(f"  [ERROR] 取得子產業失敗，跳過：{e}")
            failed.append(main_code)
            continue

        # 依 chain 分組，保留順序
        chain_map: dict[str, list[dict]] = defaultdict(list)
        chain_order: list[str] = []

        for sub in sub_topics:
            sub_code = sub["code"]
            sub_name = sub["name"]
            chain    = sub.get("chain", "其他")
            print(f"  [{sub_code}] {sub_name} ({chain}) — {sub['numOfStocks']} 支", end="")

            try:
                symbol_ids = fetch_symbol_ids(sub_code)
                time.sleep(SLEEP)
            except RuntimeError as e:
                print(f" → [ERROR] {e}")
                failed.append(sub_code)
                continue

            # 過濾 + 補名稱
            stocks = []
            for sid in symbol_ids:
                if filter_twse and sid not in twse_info:
                    continue
                stocks.append({
                    "code": sid,
                    "name": twse_info.get(sid, ""),
                })

            print(f" → {len(stocks)} 支上市股票")

            if chain not in chain_order:
                chain_order.append(chain)
            chain_map[chain].append({
                "name": sub_name,
                "stocks": stocks,
            })

        result.append({
            "industry": main_name,
            "chain": [
                {"name": c, "sub_industries": chain_map[c]}
                for c in chain_order
            ],
        })

    return result


# ── 輸出 CSV（扁平，供 ETL 用）──────────────────────────────────────────────

def write_csv(nested: list[dict]) -> None:
    with open(CSV_OUTPUT, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["stock_id", "stock_name", "industry", "chain", "sub_industry"]
        )
        writer.writeheader()
        for ind in nested:
            for ch in ind["chain"]:
                for sub in ch["sub_industries"]:
                    for s in sub["stocks"]:
                        writer.writerow({
                            "stock_id":    s["code"],
                            "stock_name":  s["name"],
                            "industry":    ind["industry"],
                            "chain":       ch["name"],
                            "sub_industry": sub["name"],
                        })


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    twse_info = fetch_twse_stock_info()

    try:
        main_topics = fetch_main_topics()
    except RuntimeError as e:
        print(f"[ERROR] 無法取得主產業列表，中止：{e}")
        sys.exit(1)

    failed: list[str] = []
    nested = build_nested(main_topics, twse_info, failed)

    # 彙總
    total_stocks = sum(
        len(s["stocks"])
        for ind in nested
        for ch in ind["chain"]
        for s in ch["sub_industries"]
    )
    print(f"\n{'='*50}")
    print(f"完成！{len(nested)} 個產業，共 {total_stocks} 筆上市股票分類")
    if failed:
        print(f"[WARN] 以下 {len(failed)} 個代碼爬取失敗：{', '.join(failed)}")
    print(f"{'='*50}\n")

    # 寫出 JSON
    with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(nested, f, ensure_ascii=False, indent=2)
    print(f"JSON → {JSON_OUTPUT}")

    # 寫出 CSV
    write_csv(nested)
    print(f"CSV  → {CSV_OUTPUT}")


if __name__ == "__main__":
    main()
