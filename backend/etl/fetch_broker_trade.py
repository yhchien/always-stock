"""
Fetch per-stock broker branch trading data from TWSE BSR.

Source: https://bsr.twse.com.tw/bshtm/
Flow:
  1. GET  bsMenu.aspx  → session cookie + ASP.NET viewstate
  2. POST bsMenu.aspx  → submit stock code (establishes server-side session)
  3. GET  bsContent.aspx?v=t&d={roc_date}&s={stock_id} → HTML with broker data

HTML structure (inside full page, NOT limited to sp_HtmlCode):
  - Header rows: 交易日期, 股票代號, 成交筆數, etc.
  - Data rows: 5 cells each → [sequence, broker_id/name, price, buy_shares, sell_shares]
  - First row per broker: "9800 元大" (code + name)
  - Subsequent rows:      "9800"      (code only, same broker at different price)
  - Need to SUM buy/sell across all price rows per broker.
"""
import logging
import re
import urllib.parse
import urllib.request
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models import BrokerTrade

logger = logging.getLogger(__name__)

BSR_MENU_URL = "https://bsr.twse.com.tw/bshtm/bsMenu.aspx"
BSR_CONTENT_URL = "https://bsr.twse.com.tw/bshtm/bsContent.aspx"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _to_roc_date(d: date) -> str:
    """Convert to ROC date string: YYY/MM/DD."""
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


def _parse_int(s: str) -> int:
    """Parse comma-separated integer from TWSE. Returns 0 for invalid."""
    s = s.strip().replace(",", "")
    if not s or s == "--":
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def _clean_text(html: str) -> str:
    """Strip HTML tags and entities, collapse whitespace."""
    text = html.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"<[^>]+>", " ", text).strip()
    # Remove full-width and non-breaking spaces, then collapse all whitespace
    text = text.replace("\u3000", "").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _establish_session(stock_id: str) -> Optional[str]:
    """
    Establish BSR session by posting stock code to bsMenu.aspx.
    Returns session cookie string, or None on failure.
    """
    headers = {"User-Agent": USER_AGENT}

    # Step 1: GET menu page for viewstate
    req1 = urllib.request.Request(BSR_MENU_URL, headers=headers)
    resp1 = urllib.request.urlopen(req1, timeout=15)
    menu_html = resp1.read().decode("utf-8")
    cookie = resp1.headers.get("Set-Cookie", "").split(";")[0]

    vs_m = re.search(r'__VIEWSTATE.*?value="(.*?)"', menu_html)
    evt_m = re.search(r'__EVENTVALIDATION.*?value="(.*?)"', menu_html)
    vsg_m = re.search(r'__VIEWSTATEGENERATOR.*?value="(.*?)"', menu_html)
    if not (vs_m and evt_m and vsg_m):
        logger.error("Failed to extract ASP.NET viewstate from BSR menu")
        return None

    # Step 2: POST with stock code
    post_data = urllib.parse.urlencode({
        "__EVENTTARGET": "", "__EVENTARGUMENT": "", "__LASTFOCUS": "",
        "__VIEWSTATE": vs_m.group(1),
        "__VIEWSTATEGENERATOR": vsg_m.group(1),
        "__EVENTVALIDATION": evt_m.group(1),
        "TextBox_Stkno": stock_id,
        "RadioButton_Normal": "RadioButton_Normal",
        "btnOK": "查詢",
    }).encode()
    req2 = urllib.request.Request(
        BSR_MENU_URL, data=post_data,
        headers={**headers, "Cookie": cookie, "Referer": BSR_MENU_URL},
    )
    urllib.request.urlopen(req2, timeout=15)

    return cookie


def _fetch_page(cookie: str, stock_id: str, trade_date: date) -> str:
    """Fetch bsContent page HTML using established session."""
    roc = _to_roc_date(trade_date)
    url = f"{BSR_CONTENT_URL}?v=t&d={roc}&s={stock_id}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Cookie": cookie},
    )
    resp = urllib.request.urlopen(req, timeout=30)
    return resp.read().decode("utf-8")


def _parse_brokers(html: str) -> list[dict]:
    """
    Parse broker trading data from full BSR page HTML.
    Returns list of {broker_id, broker_name, buy_shares, sell_shares, net_shares}.
    """
    all_tds = re.findall(r"<td[^>]*>(.*?)</td>", html, re.DOTALL)
    if len(all_tds) < 30:
        return []

    # Find the header row "序" to locate data start
    start_idx = None
    for i, td in enumerate(all_tds):
        if _clean_text(td) == "序":
            start_idx = i + 4  # skip header: 序, 證券商, 成交單價, 買進股數, 賣出股數
            break
    if start_idx is None:
        return []

    # Parse 5-cell rows: [seq, broker, price, buy, sell]
    brokers: dict[str, dict] = {}  # broker_id -> {name, buy, sell}
    i = start_idx + 1  # +1 for first sequence number
    while i + 4 <= len(all_tds):
        broker_cell = _clean_text(all_tds[i])
        buy_cell = _clean_text(all_tds[i + 2])
        sell_cell = _clean_text(all_tds[i + 3])

        # Detect broker cell: starts with 4-digit code
        m = re.match(r"^(\d{4})\s*(.*)", broker_cell)
        if not m:
            i += 5  # skip malformed row
            continue

        broker_id = m.group(1)
        name_part = re.sub(r"\s+", "", m.group(2).strip())

        buy = _parse_int(buy_cell)
        sell = _parse_int(sell_cell)

        if broker_id not in brokers:
            brokers[broker_id] = {"name": name_part or broker_id, "buy": 0, "sell": 0}
        elif name_part:
            # Update name if this row has it (first row for this broker)
            brokers[broker_id]["name"] = name_part

        brokers[broker_id]["buy"] += buy
        brokers[broker_id]["sell"] += sell

        i += 5

    return [
        {
            "broker_id": bid,
            "broker_name": data["name"],
            "buy_shares": data["buy"],
            "sell_shares": data["sell"],
            "net_shares": data["buy"] - data["sell"],
        }
        for bid, data in brokers.items()
    ]


def fetch_broker_trade_rows(stock_id: str, trade_date: date) -> list[dict]:
    """
    Fetch broker trading rows from TWSE BSR without writing to the database.

    Returns parsed broker rows, or an empty list if the remote page is empty,
    malformed, or unavailable for the requested stock/date.
    """
    if trade_date.weekday() >= 5:
        logger.info("Skipping weekend preview fetch: %s", trade_date)
        return []

    logger.info("Fetching broker trade preview: %s on %s", stock_id, trade_date)

    cookie = _establish_session(stock_id)
    if not cookie:
        logger.error("Failed to establish BSR session for preview %s", stock_id)
        return []

    html = _fetch_page(cookie, stock_id, trade_date)
    brokers = _parse_brokers(html)
    if not brokers:
        logger.warning("No broker data parsed for %s on %s", stock_id, trade_date)
    return brokers


def fetch_and_upsert_broker_trade(
    db: Session, stock_id: str, trade_date: date
) -> int:
    """
    Fetch broker trading data for a single stock+date from TWSE BSR.
    Upserts into broker_trade table.

    Returns number of broker records upserted, or 0 on failure/skip.
    """
    brokers = fetch_broker_trade_rows(stock_id, trade_date)
    if not brokers:
        return 0

    count = 0
    for b in brokers:
        # Skip header artifacts: stock_id itself or date remnants ("/MM/DD")
        if b["broker_id"] == stock_id or b["broker_name"].startswith("/"):
            continue
        existing = (
            db.query(BrokerTrade)
            .filter_by(trade_date=trade_date, stock_id=stock_id, broker_id=b["broker_id"])
            .first()
        )
        if existing:
            existing.broker_name = b["broker_name"]
            existing.buy_shares = b["buy_shares"]
            existing.sell_shares = b["sell_shares"]
            existing.net_shares = b["net_shares"]
        else:
            db.add(BrokerTrade(
                trade_date=trade_date,
                stock_id=stock_id,
                broker_id=b["broker_id"],
                broker_name=b["broker_name"],
                buy_shares=b["buy_shares"],
                sell_shares=b["sell_shares"],
                net_shares=b["net_shares"],
            ))
        count += 1

    db.commit()
    logger.info("Broker trade upserted: %d brokers for %s on %s", count, stock_id, trade_date)
    return count
