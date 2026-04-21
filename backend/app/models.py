from sqlalchemy import Column, String, Integer, Float, Date, DateTime, Boolean, UniqueConstraint
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
