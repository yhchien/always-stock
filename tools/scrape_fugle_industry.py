#!/usr/bin/env python3
"""
Scrape all main industries → sub-industries → stock listings from Fugle.
Only TWSE-listed stocks are kept; OTC (上櫃) stocks are filtered out.

Outputs (tools/output/):
  - fugle_industry_mapping.json   nested structure for frontend use
  - fugle_industry_mapping.csv    flat structure for ETL use

Usage:
  python3 -u tools/scrape_fugle_industry.py

Requires: requests
"""

import csv
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

# ── Logging setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

FUGLE_BASE = "https://www.fugle.tw/api/v2/data"
FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

JSON_OUTPUT = OUTPUT_DIR / "fugle_industry_mapping.json"
CSV_OUTPUT  = OUTPUT_DIR / "fugle_industry_mapping.csv"

SLEEP = 0.4   # seconds between API requests
RETRIES = 3
RETRY_WAIT = 3

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.fugle.tw/industry",
})


# ── HTTP ─────────────────────────────────────────────────────────────────────

def get_json(url: str) -> dict:
    """GET with retry. 4xx errors raise immediately; 5xx / timeout / connection errors are retried."""
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = SESSION.get(url, timeout=20)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            if status < 500:
                raise RuntimeError(f"HTTP {status}, not retrying: {url}") from e
            last_err = f"HTTP {status}"
        except requests.exceptions.Timeout:
            last_err = "timeout"
        except requests.exceptions.ConnectionError:
            last_err = "connection error"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        logger.warning("Attempt %d/%d failed (%s), retrying in %ds...", attempt, RETRIES, last_err, RETRY_WAIT)
        time.sleep(RETRY_WAIT)

    raise RuntimeError(f"Failed after {RETRIES} attempts: {url} (last error: {last_err})")


# ── FinMind ──────────────────────────────────────────────────────────────────

def fetch_twse_stock_info() -> dict:
    """
    Return {stock_id: stock_name} for TWSE-listed stocks only.
    Returns an empty dict on failure; downstream code will skip TWSE filtering.
    """
    logger.info("Fetching TWSE stock list from FinMind...")
    try:
        data = get_json(f"{FINMIND_URL}?dataset=TaiwanStockInfo")
    except RuntimeError as e:
        logger.error("FinMind unreachable: %s", e)
        logger.warning("Fallback: keeping all stocks, stock names will be empty")
        return {}

    stocks = data.get("data", [])
    if not stocks:
        logger.warning("FinMind returned empty data, API may be down")
        return {}

    result = {s["stock_id"]: s["stock_name"]
              for s in stocks if s.get("type") == "twse"}
    logger.info("TWSE stock count: %d", len(result))
    return result


# ── Fugle ─────────────────────────────────────────────────────────────────────

def fetch_main_topics() -> list:
    logger.info("Fetching main industry list...")
    data = get_json(f"{FUGLE_BASE}/industry-main-topics")
    topics = data["data"]["topics"]
    logger.info("Main industries: %d", len(topics))
    return topics


def fetch_sub_topics(main_code: str) -> list:
    data = get_json(f"{FUGLE_BASE}/industry-sub-topics/{main_code}")
    return data["data"].get("topics", [])


def fetch_symbol_ids(sub_code: str) -> list:
    data = get_json(f"{FUGLE_BASE}/industry-topic-detail/{sub_code}")
    symbol_list = data["data"]["topic"].get("symbolList", [])
    return [s["symbolId"] for s in symbol_list]


# ── Build nested structure ────────────────────────────────────────────────────

def build_nested(main_topics: list, twse_info: dict, failed: list) -> list:
    """
    Build and return nested industry structure:
    [
      {
        "industry": "Printed Circuit Board",
        "chain": [
          {
            "name": "Upstream",
            "sub_industries": [
              {
                "name": "Glass fiber / woven fabric",
                "stocks": [
                  { "code": "1234", "name": "Company Name" }
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
        logger.info("[%d/%d] %s %s", i, len(main_topics), main_code, main_name)

        try:
            sub_topics = fetch_sub_topics(main_code)
            time.sleep(SLEEP)
        except RuntimeError as e:
            logger.error("Failed to fetch sub-industries for %s, skipping: %s", main_code, e)
            failed.append(main_code)
            continue

        # Group by chain, preserving order
        chain_map: dict = defaultdict(list)
        chain_order: list = []

        for sub in sub_topics:
            sub_code = sub["code"]
            sub_name = sub["name"]
            chain    = sub.get("chain", "其他")

            try:
                symbol_ids = fetch_symbol_ids(sub_code)
                time.sleep(SLEEP)
            except RuntimeError as e:
                logger.error("Failed to fetch stocks for sub-industry %s: %s", sub_code, e)
                failed.append(sub_code)
                continue

            # Filter to TWSE only and fill in stock names
            stocks = []
            for sid in symbol_ids:
                if filter_twse and sid not in twse_info:
                    continue
                stocks.append({
                    "code": sid,
                    "name": twse_info.get(sid, ""),
                })

            logger.debug("  [%s] %s (%s): %d TWSE stocks (of %d total)",
                         sub_code, sub_name, chain, len(stocks), sub["numOfStocks"])

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


# ── Write CSV (flat, for ETL) ─────────────────────────────────────────────────

def write_csv(nested: list) -> None:
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


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    twse_info = fetch_twse_stock_info()

    try:
        main_topics = fetch_main_topics()
    except RuntimeError as e:
        logger.error("Cannot fetch main industry list, aborting: %s", e)
        sys.exit(1)

    failed: list = []
    nested = build_nested(main_topics, twse_info, failed)

    total_stocks = sum(
        len(s["stocks"])
        for ind in nested
        for ch in ind["chain"]
        for s in ch["sub_industries"]
    )

    logger.info("=" * 50)
    logger.info("Done! %d industries, %d TWSE stock classifications", len(nested), total_stocks)
    if failed:
        logger.warning("Failed codes (%d): %s", len(failed), ", ".join(failed))
    logger.info("=" * 50)

    # Write JSON
    with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(nested, f, ensure_ascii=False, indent=2)
    logger.info("JSON → %s", JSON_OUTPUT)

    # Write CSV
    write_csv(nested)
    logger.info("CSV  → %s", CSV_OUTPUT)


if __name__ == "__main__":
    main()
