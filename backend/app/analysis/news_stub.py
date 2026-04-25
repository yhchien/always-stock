"""
M21 PART 6：news_input_stub

純字串組裝：把 stock / industry / buy_date 組成關鍵字，給未來 M14 輿情 ETL 查 News API 用。
本層不查 DB 新聞、不打外部 API、不做 NLP。
"""
from __future__ import annotations

from datetime import date
from typing import List, Tuple


def build_news_stub(
    stock_id: str,
    stock_name: str,
    industry_name: str,
    buy_date: date,
) -> Tuple[dict, List[str]]:
    query_stock = f"{stock_id} OR {stock_name}" if stock_name else stock_id
    query_industry = industry_name or ""

    return (
        {
            "query_stock": query_stock,
            "query_industry": query_industry,
            "date_end": str(buy_date),
        },
        [],
    )
