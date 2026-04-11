DEFAULT_INITIAL_CAPITAL = 1_000_000
DEFAULT_TRADE_TIMING = "next_open"
DEFAULT_POSITION_SIZE_PCT = 100.0
DEFAULT_STRATEGY_TEXT = "收盤價站上20日均線且外資連買3天就買進；收盤價跌破20日均線或外資轉賣就賣出。"

BACKTEST_TEMPLATES = [
    {
        "id": "chip_ma_resonance",
        "name": "均線 + 籌碼共振型",
        "description": "股價站上月線且外資連買時進場，跌破月線或外資轉賣時退場",
        "strategy_text": DEFAULT_STRATEGY_TEXT,
    },
    {
        "id": "foreign_breakout",
        "name": "外資連買突破型",
        "description": "以月線與外資連買確認趨勢續強",
        "strategy_text": "收盤價站上20日均線且外資連買3天就買進；收盤價跌破20日均線或外資轉賣就賣出。",
    },
    {
        "id": "trust_trend",
        "name": "投信趨勢跟隨型",
        "description": "股價站上20日均線且投信連買3天時進場",
        "strategy_text": "收盤價站上20日均線且投信連買3天就買進；收盤價跌破20日均線或投信轉賣就賣出。",
    },
    {
        "id": "volume_breakout",
        "name": "量價突破型",
        "description": "以量能放大搭配價格站穩均線做進出",
        "strategy_text": "收盤價站上20日均線且成交量高於20日均量就買進；收盤價跌破20日均線就賣出。",
    },
]
