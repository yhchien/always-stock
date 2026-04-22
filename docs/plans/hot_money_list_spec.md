# M22 熱錢湧入個股排行 Spec

> **Canonical spec**：若記憶或程式碼與本文件衝突，以本文件為準。
> 2026-04-22 定案，對應 milestone M22。

## 1. 需求

L0 首頁底部 + L1 產業頁頂部，各新增一個「近 N 日三大法人累計淨買超」個股排行列表。

- **L0**：全市場 Top 20，放在 `<IndustryDashboard />` 之後（首頁最下面）
- **L1**：單一產業 Top 10，放在 `StockList` 最上面；若帶 `?sub=xxx` 子產業 filter，只篩該子產業個股
- 點股票列 → `router.push(/stocks/{id})` 跳 L2 研究頁

## 2. 設計決策

### 2.1 第一版不含大戶欄位（重要）

| 方案 | 問題 |
|------|------|
| A `TaiwanStockShareholding` | 週頻，跟「近 3 日」時間窗對不齊；股數推金額要乘均價，有噪音 |
| B `broker_trade_agg` 頭部分點 | 日頻可用但「頭部分點 ≠ 散戶大戶」，命名尷尬 |
| C 過濾外資/投信分點 | 黑名單自行維護、成本高 |

**結論**：交付乾淨的三大法人 Top K，先驗證需求。未來要加大戶另做獨立「月變化」功能。

### 2.2 時間窗定義

- **窗口長度 = 交易日**：用 `inst_stock_flow.trade_date DESC LIMIT N`，不用曆日推算（避免落在假日）
- `end_date` 先用 `get_latest_industry_trade_date(db, requested)` resolve 到最近有資料的交易日
- `trade_dates` 回傳 list（由遠到近），方便前端 debug
- `start_date` / `end_date` 以 ISO 字串回傳

### 2.3 排序 key

- 三大法人合計（外資 + 投信 + 自營）累計 `net_amount_est`
- 降冪
- 單位保留「元」，前端顯示時除以 `1e8` 轉億

### 2.4 呈現

- `<table>` 列表（**不要**卡片），欄位：排名 / 代號 / 名稱 / 子產業 / 期間漲跌 % / 外資 / 投信 / 自營 / 合計
- 億元 1 位小數
- 正綠負紅：**漲跌 %** 用 `>0 紅 / <0 綠`（台股習慣），**法人金額**也用 `>0 紅 / <0 綠`
- `null` 欄位（價格缺、股票停牌）顯示 `—`

### 2.5 漲跌 % 定義

- 「窗口起點的前一交易日收盤」→「窗口終點收盤」
- 例：days=3，窗口為 `[D-2, D-1, D]`，漲跌 % = `(close[D] - close[D-3]) / close[D-3] * 100`
- 完整涵蓋 3 日累計漲跌（含窗口內第一天的跳空）

## 3. API

### 3.1 L0 `/api/market/hot-money`

- 路徑：`GET /api/market/hot-money`
- 權限：**公開**（跟 `/api/market/daily-brief` / `trade-quality` 同層級）
- Query：
  - `date`：optional；預設 `latest trade date`
  - `days`：optional；預設 `3`；最小 `1`、最大 `20`
  - `limit`：optional；預設 `20`；最大 `50`

### 3.2 L1 `/api/industries/{industry_name}/hot-money`

- 路徑：`GET /api/industries/{industry_name}/hot-money`
- 權限：繼承 L1 頁面 `<RequireAuth>`（API 本身不強制登入，前端 gating 已擋）
- Query：
  - `date` / `days` / `limit` 同上；`limit` 預設 `10`
  - `sub_industry`：optional；帶值時只算該子產業股票

### 3.3 Response schema

```jsonc
{
  "start_date": "2026-04-17",
  "end_date": "2026-04-22",
  "trade_dates": ["2026-04-17", "2026-04-21", "2026-04-22"],
  "items": [
    {
      "rank": 1,
      "stock_id": "2330",
      "stock_name": "台積電",
      "industry_name": "半導體業",
      "sub_industry": "IC 製造",
      "start_close_price": 1000.0,  // 窗口前一交易日收盤
      "end_close_price": 1050.0,    // 窗口終點收盤
      "price_change_pct": 5.0,      // null 表無法計算
      "foreign_net_amount": 1.2e9,
      "trust_net_amount": 3e8,
      "dealer_net_amount": -1e8,
      "total_net_amount": 1.4e9
    }
  ]
}
```

## 4. 共用 service

`backend/app/hot_money_service.py`：

```python
@dataclass
class HotMoneyStockItem:
    rank: int
    stock_id: str
    stock_name: str
    industry_name: str
    sub_industry: Optional[str]
    start_close_price: Optional[float]
    end_close_price: Optional[float]
    price_change_pct: Optional[float]
    foreign_net_amount: float
    trust_net_amount: float
    dealer_net_amount: float
    total_net_amount: float


@dataclass
class HotMoneyResult:
    start_date: date
    end_date: date
    trade_dates: List[date]
    items: List[HotMoneyStockItem]


def get_recent_trade_dates(
    db: Session,
    end_date: date,
    days: int,
    stock_ids: Optional[Sequence[str]] = None,
) -> List[date]: ...


def compute_hot_money(
    db: Session,
    end_date: date,
    days: int,
    limit: int,
    stock_ids: Optional[Sequence[str]] = None,
) -> HotMoneyResult: ...
```

### 4.1 演算法

1. `get_recent_trade_dates`：`inst_stock_flow.trade_date <= end_date DISTINCT ORDER BY DESC LIMIT days`；若 `stock_ids` 有值則加 `IN` filter（避免某檔停牌時少算窗口天數）
2. Aggregate：`SUM(net_amount_est) GROUP BY stock_id, inst_type`；Python 側再把 foreign/trust/dealer 合成 total
3. JOIN `stocks_master` 取 `stock_name / industry_name / sub_industry`
4. 篩 `stock_ids`（L1 場景傳入產業股票清單）
5. 取 `total_net_amount DESC LIMIT limit`
6. 撈 `end_close_price`（`daily_price` on `end_date_resolved`）與 `start_close_price`
   - `start_close_price` = 窗口起點的「前一交易日」收盤；用 `DailyPrice.trade_date < trade_dates[0] ORDER BY DESC LIMIT 1 per stock`
   - 任一端缺資料 → `price_change_pct = None`
7. 組裝 `HotMoneyStockItem[]`，依 total 降冪

### 4.2 邊界

- 如果 `get_recent_trade_dates` 回空 list：回 `HotMoneyResult(items=[])`，API 回 `200`，前端顯示「此期間無資料」
- 如果查到 0 檔股票：`items=[]`、有 `trade_dates`、前端顯示「此期間無資料」
- `stock_ids` 傳空 list：視為「無股票」，items=[]（L1 場景若產業下無股票）
- `days <= 0` 或 `limit <= 0`：422

## 5. 前端

### 5.1 `HotMoneyList.tsx`

```tsx
interface HotMoneyListProps {
  industryName?: string   // 有值 = L1 mode
  subIndustry?: string    // L1 with sub filter
  date: string            // ISO yyyy-mm-dd
  days?: number           // default 3
  limit?: number          // default: L0=20, L1=10
  title?: string          // overrideable
}
```

- 依 `industryName` 決定呼叫哪支 API
- loading：Skeleton
- error：`<p className="text-sm text-red-400">`
- 空：`<p className="text-sm text-slate-500">此期間無資料</p>`
- 列表：`<Table>` 欄位 = 排名 / 代號+名稱 / 子產業 / 期間漲跌 % / 外資 / 投信 / 自營 / 合計

### 5.2 嵌入位置

- **L0** `frontend/src/app/page.tsx`：`<IndustryDashboard />` 之後（最底部）
- **L1** `frontend/src/components/StockList.tsx`：在 Header 下、Summary Table 前（最上面）；依 `subFilter` state 動態帶 `subIndustry` prop

## 6. 實作順序（commit 切片）

1. 本 spec 檔
2. `hot_money_service.py` + `tests/test_hot_money_service.py`
3. router + `tests/test_hot_money_router.py`
4. 前端 `lib/api.ts` + `HotMoneyList.tsx` + L0/L1 嵌入
5. 同步 README / CLAUDE.md / memory / skill
