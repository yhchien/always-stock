---
name: tw-stock-dashboard
description: Taiwan stock industry-level institutional money flow dashboard — ETL, API, and frontend rules.
---

# 🧠 Product Skill: TW Stock Industry Flow Dashboard

## 📊 Core Concept

We analyze institutional money flow at INDUSTRY level.

Data source is TWSE (Taiwan Stock Exchange).

All calculations are derived from:

* stock-level institutional buy/sell shares
* stock price

---

## 🧮 Calculation Rules

For each stock per day:

buy_amount = buy_shares * price
sell_amount = sell_shares * price
net_amount = buy_amount - sell_amount

Then aggregate:

industry_net = sum(stock_net)

---

## 🏭 Industry Definition

* Use TWSE official industry classification
* Do NOT create custom categories

---

## 🧱 Architecture

### Data Flow

TWSE → ETL → SQLite → FastAPI → Frontend

---

## 🧩 Key Tables

* stocks_master
* daily_price
* inst_stock_flow
* industry_daily_flow (MOST IMPORTANT)

---

## ⚡ Performance Strategy

* Always query aggregated table
* Never aggregate on frontend
* Never aggregate on API layer

---

## 🎨 UI Philosophy

* Card-based overview (not treemap)
* Red = net buy
* Green = net sell
* Always show numbers (not only color)

---

## 📈 Visualization Rules

* Default range = 3 months
* Allow extend to 5 years
* Hover must show exact date + value

---

## 🧪 Data Assumptions

* Price can be approximated using:

  * close_price OR avg_price
* Precision is acceptable for trend analysis

---

## 🚫 Anti-patterns

* ❌ Do NOT scrape HTML if API exists
* ❌ Do NOT calculate industry on frontend
* ❌ Do NOT mix TWSE and TPEx in V1

---

## 🧪 Testing Rules

Every Python file produced MUST have a corresponding test file:

| 檔案位置 | 測試位置 |
|---------|---------|
| `backend/app/*.py` | `backend/tests/test_<name>.py` |
| `backend/etl/*.py` | `backend/tests/test_<name>.py` |
| `tools/*.py` | `tools/tests/test_<name>.py` |

- Use `pytest`
- Mock all external HTTP calls（FinMind、TWSE、Fugle）
- Use in-memory SQLite for DB tests（參考 `backend/tests/conftest.py`）
- 測試完成後執行 `pytest` 確認全過

---

## ✅ Definition of Done

* User opens page → sees all industries latest net flow
* Click industry → sees trend
* Select date → sees institution breakdown
* Everything works locally
