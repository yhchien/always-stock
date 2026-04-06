from sqlalchemy import Column, String, Integer, Float, Date, Boolean, UniqueConstraint
from .database import Base


class StockMaster(Base):
    __tablename__ = "stocks_master"

    stock_id = Column(String, primary_key=True)
    stock_name = Column(String, nullable=False)
    industry_name = Column(String, nullable=False)
    chain = Column(String, nullable=True)        # supply chain tier (upstream/midstream/downstream), Fugle only
    sub_industry = Column(String, nullable=True) # sub-industry category, Fugle only
    is_active = Column(Boolean, default=True)


class DailyPrice(Base):
    __tablename__ = "daily_price"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(Date, nullable=False)
    stock_id = Column(String, nullable=False)
    close_price = Column(Float)
    volume = Column(Float)
    turnover = Column(Float)
    avg_price = Column(Float)

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
