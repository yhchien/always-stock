from sqlalchemy import Column, String, Integer, BigInteger, Float, Date, DateTime, Boolean, Text, JSON, UniqueConstraint, ForeignKey
from datetime import datetime
from .database import Base


class StockMaster(Base):
    __tablename__ = "stocks_master"

    stock_id = Column(String, primary_key=True)
    stock_name = Column(String, nullable=False)
    market = Column(String, default="twse")      # twse | tpex | emerging (新增)
    industry_name = Column(String, nullable=False)
    chain = Column(String, nullable=True)        # supply chain tier (upstream/midstream/downstream), Fugle only
    sub_industry = Column(String, nullable=True) # sub-industry category, Fugle only
    is_active = Column(Boolean, default=True)
    source = Column(String, default="fugle")     # fugle | finmind (新增)
    source_version = Column(String, nullable=True)  # 版本號或更新時間戳記 (新增)


class DailyPrice(Base):
    __tablename__ = "daily_price"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    stock_id = Column(String, nullable=False)
    open_price = Column(Float)
    high_price = Column(Float)
    low_price = Column(Float)
    close_price = Column(Float)
    volume = Column(Float)
    turnover = Column(Float)
    avg_price = Column(Float)
    spread = Column(Float, nullable=True)         # 漲跌幅 (新增)
    source = Column(String, default="twse")       # twse | finmind (新增)
    ingested_at = Column(DateTime, default=datetime.utcnow)  # 資料進入時間戳記 (新增)

    __table_args__ = (UniqueConstraint("trade_date", "stock_id", name="uq_price_date_stock"),)


class InstStockFlow(Base):
    __tablename__ = "inst_stock_flow"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    stock_id = Column(String, nullable=False)
    inst_type = Column(String, nullable=False)  # foreign | trust | dealer
    buy_shares = Column(Float, default=0)
    sell_shares = Column(Float, default=0)
    net_shares = Column(Float, default=0)
    buy_amount_est = Column(Float, default=0)
    sell_amount_est = Column(Float, default=0)
    net_amount_est = Column(Float, default=0)
    source = Column(String, default="twse")       # twse | finmind (新增)
    ingested_at = Column(DateTime, default=datetime.utcnow)  # 資料進入時間戳記 (新增)

    __table_args__ = (
        UniqueConstraint("trade_date", "stock_id", "inst_type", name="uq_flow_date_stock_inst"),
    )


class IndustryDailyFlow(Base):
    __tablename__ = "industry_daily_flow"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    industry_name = Column(String, nullable=False)
    total_buy_amount = Column(Float, default=0)
    total_sell_amount = Column(Float, default=0)
    total_net_amount = Column(Float, default=0)
    foreign_net_amount = Column(Float, default=0)
    trust_net_amount = Column(Float, default=0)
    dealer_net_amount = Column(Float, default=0)
    streak = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("trade_date", "industry_name", name="uq_industry_date"),
    )


class BrokerTrade(Base):
    __tablename__ = "broker_trade"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    stock_id = Column(String, nullable=False)
    broker_id = Column(String, nullable=False)   # TWSE BSR 4-digit code
    broker_name = Column(String, nullable=False)  # e.g. "元大", "凱基台北"
    buy_shares = Column(Float, default=0)
    sell_shares = Column(Float, default=0)
    net_shares = Column(Float, default=0)
    source = Column(String, default="twse")       # twse | finmind (新增)
    ingested_at = Column(DateTime, default=datetime.utcnow)  # 資料進入時間戳記 (新增)

    __table_args__ = (
        UniqueConstraint("trade_date", "stock_id", "broker_id", name="uq_broker_date_stock"),
    )


# ============================================================================
# 新增表格 — Phase 1 FinMind 遷移
# ============================================================================

class DailyValuation(Base):
    """
    日常估值表：P/E 比、P/B 比、股息殖利率等
    數據來源：FinMind TaiwanStockPER
    更新頻率：每日
    """
    __tablename__ = "daily_valuation"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    stock_id = Column(String, nullable=False)
    per = Column(Float, nullable=True)            # 本益比
    pbr = Column(Float, nullable=True)            # 股價淨值比
    dividend_yield = Column(Float, nullable=True) # 股息殖利率
    source = Column(String, default="finmind")
    ingested_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("trade_date", "stock_id", name="uq_valuation_date_stock"),
    )


class MonthlyRevenue(Base):
    """
    月營收表：每月營收、年增率、月增率等
    數據來源：FinMind TaiwanStockMonthRevenue
    更新頻率：每月（通常月中公布上月資料）
    """
    __tablename__ = "monthly_revenue"

    id = Column(Integer, primary_key=True, autoincrement=True)
    revenue_month = Column(Date, nullable=False)  # 本月最後一天
    stock_id = Column(String, nullable=False)
    revenue = Column(Float, nullable=True)        # 月營收金額（百萬元）
    yoy_pct = Column(Float, nullable=True)        # 年增率 (%)
    mom_pct = Column(Float, nullable=True)        # 月增率 (%)
    source = Column(String, default="finmind")
    ingested_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("revenue_month", "stock_id", name="uq_revenue_month_stock"),
    )


class FinancialStatement(Base):
    """
    財報項目表：營業收入、淨利、EPS、ROE 等
    數據來源：FinMind TaiwanStockFinancialStatements
    更新頻率：每季（通常隔月公布）
    """
    __tablename__ = "financial_statement"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_date = Column(Date, nullable=False)    # 財報公布日期或報告期間結束日
    stock_id = Column(String, nullable=False)
    item_name = Column(String, nullable=False)    # 營業收入、淨利、EPS、ROE 等
    item_code = Column(String, nullable=True)     # FinMind 原始代碼
    value = Column(Float, nullable=True)          # 數值
    period_type = Column(String, default="quarterly")  # quarterly | annual
    unit = Column(String, nullable=True)          # 單位（百萬、千元等）
    source = Column(String, default="finmind")
    ingested_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("report_date", "stock_id", "item_name", name="uq_finstatement_date_stock_item"),
    )


class BrokerTradeRaw(Base):
    """
    券商交易原始數據（逐價分點）
    數據來源：FinMind TaiwanStockTradingDailyReport
    更新頻率：每日
    用途：支援未來的逐價分點分析
    """
    __tablename__ = "broker_trade_raw"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    stock_id = Column(String, nullable=False)
    price = Column(Float, nullable=True)          # 成交價
    securities_trader_id = Column(String, nullable=False)  # 券商代碼
    securities_trader_name = Column(String, nullable=True)  # 券商名稱
    buy = Column(Float, default=0)                # 買進股數
    sell = Column(Float, default=0)               # 賣出股數
    source = Column(String, default="finmind")
    ingested_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # 允許同一券商同一價格多筆交易
        # 使用索引加速查詢，但無單一主鍵
    )


class BrokerTradeAgg(Base):
    """
    券商交易聚合數據（每日彙總）
    數據來源：FinMind TaiwanStockTradingDailyReportSecIdAgg（推薦）
              或由 broker_trade_raw 聚合計算
    更新頻率：每日
    用途：支援現有 BrokerPanel 邏輯
    """
    __tablename__ = "broker_trade_agg"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    stock_id = Column(String, nullable=False)
    broker_id = Column(String, nullable=False)   # 券商代碼（如 "0961"）
    broker_name = Column(String, nullable=True)   # 券商名稱
    buy_shares = Column(Float, default=0)
    sell_shares = Column(Float, default=0)
    net_shares = Column(Float, default=0)         # buy - sell
    source = Column(String, default="finmind")
    ingested_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("trade_date", "stock_id", "broker_id", name="uq_broker_agg_date_stock_broker"),
    )


class User(Base):
    """
    使用者帳號（M18）
    認證方式：email + password（bcrypt hash）
    admin 帳號由啟動時 seeder 建立，可透過 ADMIN_EMAIL / ADMIN_PASSWORD 覆寫
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)


class UserSession(Base):
    """
    Server-side session（M18）
    登入成功後建立一筆，session_id 以 httpOnly cookie 發給前端
    登出 / revoke 時把 revoked_at 設為 now()，不刪 row 保留稽核軌跡
    """
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=False, unique=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    user_agent = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


class UserWatchlist(Base):
    """
    使用者關注買進清單（M19）
    每個使用者一個清單，上限 30 檔（由 API 層強制）
    """
    __tablename__ = "user_watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_id = Column(String, nullable=False)
    buy_date = Column(Date, nullable=False)
    # avg_price 沿用既有 daily_price.close_price 的 Float；M20 加碼建議納入運算前再一併換 Numeric(12, 4)
    avg_price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "stock_id", name="uq_watchlist_user_stock"),
    )


class MarginTrade(Base):
    """
    融資融券每日餘額（M23 訊號管線使用）
    資料來源：FinMind TaiwanStockMarginPurchaseShortSale
    更新頻率：每日
    M23 用途：判斷散戶融資追高 vs 法人吸貨對沖
    """
    __tablename__ = "margin_trade"

    trade_date = Column(Date, primary_key=True)
    stock_id = Column(String(16), primary_key=True)
    margin_balance = Column(BigInteger, nullable=True)   # 融資餘額（張，當日收盤）
    margin_change = Column(BigInteger, nullable=True)    # 當日融資餘額變化（today - yesterday）
    short_balance = Column(BigInteger, nullable=True)    # 融券餘額（張，當日收盤）
    short_change = Column(BigInteger, nullable=True)     # 當日融券餘額變化（today - yesterday）
    source = Column(String(16), default="finmind")
    ingested_at = Column(DateTime, default=datetime.utcnow)


class SignalGenerationJob(Base):
    """
    M23 訊號管線 job 追蹤
    每次觸發（cron / 使用者 / admin）建一筆，前端 polling 進度條讀此表
    """
    __tablename__ = "signal_generation_jobs"

    job_id = Column(String(36), primary_key=True)              # uuid4
    snapshot_date = Column(Date, nullable=False, index=True)
    triggered_by = Column(String(64), nullable=False)          # "cron" | "user:{id}" | "admin:{id}"
    status = Column(String(16), nullable=False, index=True)    # pending | running | done | failed
    current_stage = Column(String(64), nullable=True)          # ingest | rank | candidate | filter | llm_research | llm_explain | persist
    progress_pct = Column(Integer, default=0, nullable=False)  # 0~100
    progress_label = Column(String(255), nullable=True)        # "正在分析第 12 / 45 檔"
    error_message = Column(Text, nullable=True)                # 失敗時填 traceback 摘要
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)


class SignalSnapshot(Base):
    """
    M23 每日訊號快照
    一天一筆（snapshot_date unique），重新產生則 UPSERT 覆蓋
    歷史保留所有日期，給未來評估 filter 與 LLM 註解品質
    """
    __tablename__ = "signal_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(Date, nullable=False, unique=True, index=True)
    market_context = Column(JSON, nullable=False)              # market_state / VIX / 加權 etc.
    watchlist = Column(JSON, nullable=False)                   # List[StockSignal]
    removed = Column(JSON, nullable=False)                     # List[RemovedItem]
    summary = Column(JSON, nullable=False)                     # leader_count / follower_count / etc.
    candidate_pool_size = Column(Integer, nullable=True)       # filter 前候選數
    final_watchlist_size = Column(Integer, nullable=True)      # filter 後 WATCH 數
    llm_model = Column(String(64), nullable=True)              # e.g. gpt-4o-search-preview
    llm_total_tokens = Column(Integer, nullable=True)          # cost tracking
    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    job_id = Column(
        String(36),
        ForeignKey("signal_generation_jobs.job_id", ondelete="SET NULL"),
        nullable=True,
    )


class IndustryMapping(Base):
    """
    產業分類對照表（雙軌驗證用）
    用途：在 Fugle 和 FinMind 之間進行產業分類對照
    特點：記錄雙方的產業分類，並標記是否一致
    """
    __tablename__ = "industry_mapping"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(String, nullable=False, unique=True)
    industry_fugle = Column(String, nullable=True)        # Fugle 大產業
    sub_industry_fugle = Column(String, nullable=True)    # Fugle 子產業
    industry_finmind = Column(String, nullable=True)      # FinMind 大產業
    sub_industry_finmind = Column(String, nullable=True)  # FinMind 子產業
    consensus = Column(Boolean, default=False)           # 兩邊是否一致
    last_check_date = Column(Date)
    source = Column(String, default="manual_review")
    notes = Column(String, nullable=True)                 # 備註
    ingested_at = Column(DateTime, default=datetime.utcnow)
