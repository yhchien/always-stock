---
name: always-stock
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

## 🐍 Python 版本限制

本機 Python 3.9，以下語法**不可使用**，改用 `typing` 模組替代：

| ❌ 不可用（3.10+） | ✅ 改用 |
|-------------------|---------|
| `str \| None` | `Optional[str]`（from typing import Optional） |
| `list[str]` | `List[str]`（from typing import List） |
| `dict[str, int]` | `Dict[str, int]`（from typing import Dict） |
| `X \| Y` union | `Union[X, Y]`（from typing import Union） |

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

## 🧠 Shared Memory

This project uses Claude Code's auto-memory system to persist context across conversations.

Memory location: `~/.claude/projects/-Users-brian-yh-chien--gstack-projects-always-stock/memory/`

- `MEMORY.md` — index of all memory files (auto-loaded each conversation)
- `project_overview.md` — project architecture, milestones, and progress

**When to check memory:**
- Before starting a new feature — read `MEMORY.md` to understand current project state
- After completing a milestone — update `project_overview.md` progress
- When the user references past decisions — check for relevant memory files

**When to update memory:**
- Milestone status changes (started / completed)
- Non-obvious architectural decisions or trade-offs
- User preferences learned during the conversation

---

## ✅ Definition of Done

* User opens page → sees all industries latest net flow
* Click industry → sees trend
* Select date → sees institution breakdown
* Everything works locally
