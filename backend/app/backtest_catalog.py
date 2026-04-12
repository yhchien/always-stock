DEFAULT_INITIAL_CAPITAL = 1_000_000
DEFAULT_TRADE_TIMING = "next_open"
DEFAULT_POSITION_SIZE_PCT = 100.0
DEFAULT_STRATEGY_TEXT = "收盤價站上20日均線且外資連買3天就買進；收盤價跌破20日均線或外資轉賣就賣出。"

BACKTEST_CAPABILITY_CATALOG = {
    "indicators": [
        {
            "id": "close_above_ma",
            "category": "price",
            "label": "收盤價站上 N 日均線",
            "examples": ["收盤價站上20日均線", "股價站上月線"],
        },
        {
            "id": "close_below_ma",
            "category": "price",
            "label": "收盤價跌破 N 日均線",
            "examples": ["收盤價跌破20日均線", "股價跌破季線"],
        },
        {
            "id": "ma_golden_cross",
            "category": "price",
            "label": "短均線黃金交叉長均線",
            "examples": ["5日均線黃金交叉20日均線", "10日均線上穿60日均線"],
        },
        {
            "id": "ma_dead_cross",
            "category": "price",
            "label": "短均線死亡交叉長均線",
            "examples": ["5日均線死亡交叉20日均線", "10日均線跌破60日均線"],
        },
        {
            "id": "close_breakout_high",
            "category": "price",
            "label": "收盤價突破 N 日高點",
            "examples": ["突破20日高點", "收盤價突破60日高點"],
        },
        {
            "id": "close_breakdown_low",
            "category": "price",
            "label": "收盤價跌破 N 日低點",
            "examples": ["跌破20日低點", "收盤價失守60日低點"],
        },
        {
            "id": "volume_above_ma",
            "category": "volume",
            "label": "成交量高於 N 日均量",
            "examples": ["成交量高於20日均量"],
        },
        {
            "id": "volume_ratio_above_ma",
            "category": "volume",
            "label": "成交量暴增至 N 日均量的 X 倍以上",
            "examples": ["成交量暴增至20日均量的1.5倍以上", "成交量大於20日均量2倍"],
        },
        {
            "id": "foreign_consecutive_buy",
            "category": "flow",
            "label": "外資連買 N 天",
            "examples": ["外資連買3天"],
        },
        {
            "id": "trust_consecutive_buy",
            "category": "flow",
            "label": "投信連買 N 天",
            "examples": ["投信連買3天"],
        },
        {
            "id": "dealer_consecutive_buy",
            "category": "flow",
            "label": "自營商連買 N 天",
            "examples": ["自營商連買2天"],
        },
        {
            "id": "foreign_consecutive_sell",
            "category": "flow",
            "label": "外資連賣 N 天",
            "examples": ["外資連賣3天"],
        },
        {
            "id": "trust_consecutive_sell",
            "category": "flow",
            "label": "投信連賣 N 天",
            "examples": ["投信連賣3天"],
        },
        {
            "id": "dealer_consecutive_sell",
            "category": "flow",
            "label": "自營商連賣 N 天",
            "examples": ["自營商連賣2天"],
        },
        {
            "id": "foreign_net_positive",
            "category": "flow",
            "label": "外資買超",
            "examples": ["外資買超"],
        },
        {
            "id": "trust_net_positive",
            "category": "flow",
            "label": "投信買超",
            "examples": ["投信買超"],
        },
        {
            "id": "dealer_net_positive",
            "category": "flow",
            "label": "自營商買超",
            "examples": ["自營商買超"],
        },
        {
            "id": "foreign_net_negative",
            "category": "flow",
            "label": "外資轉賣 / 賣超",
            "examples": ["外資轉賣", "外資賣超"],
        },
        {
            "id": "trust_net_negative",
            "category": "flow",
            "label": "投信轉賣 / 賣超",
            "examples": ["投信轉賣", "投信賣超"],
        },
        {
            "id": "dealer_net_negative",
            "category": "flow",
            "label": "自營商轉賣 / 賣超",
            "examples": ["自營商轉賣", "自營商賣超"],
        },
        {
            "id": "all_inst_net_positive",
            "category": "flow",
            "label": "三大法人合計買超",
            "examples": ["三大法人買超", "三大法人合計買超"],
        },
        {
            "id": "all_inst_net_negative",
            "category": "flow",
            "label": "三大法人合計轉賣 / 賣超",
            "examples": ["三大法人轉賣", "三大法人合計轉賣", "三大法人賣超"],
        },
    ],
    "risk_controls": [
        {
            "id": "stop_loss_pct",
            "label": "固定停損",
            "examples": ["停損8%", "固定停損 10%"],
        },
        {
            "id": "take_profit_pct",
            "label": "固定停利",
            "examples": ["停利20%", "固定停利 15%"],
        },
    ],
    "notes": [
        "目前只支援日線、單檔、long-only、next_open 成交。",
        "自然語言最終仍必須落到受控 indicator catalog，未對應成功的條件只會留在 preview。",
        "下一階段可在 catalog 基礎上加入 AI mapping，但 AI 不能直接生成任意交易程式。",
    ],
}

BACKTEST_TEMPLATES = [
    {
        "id": "foreign_breakout",
        "name": "外資連買突破型",
        "description": "以月線與外資連買確認趨勢續強",
        "strategy_text": DEFAULT_STRATEGY_TEXT,
    },
    {
        "id": "trust_trend",
        "name": "投信趨勢跟隨型",
        "description": "5日均線黃金交叉20日均線且投信買超時進場，死亡交叉或投信轉賣時退場",
        "strategy_text": "5日均線黃金交叉20日均線且投信買超就買進；5日均線死亡交叉20日均線或投信轉賣就賣出。",
    },
    {
        "id": "volume_breakout",
        "name": "量價突破型",
        "description": "突破60日高點且量能爆增至1.5倍均量時進場，跌破月線時退場",
        "strategy_text": "突破60日高點且成交量暴增至20日均量的1.5倍以上就買進；收盤價跌破20日均線就賣出。",
    },
    {
        "id": "chip_ma_resonance",
        "name": "均線 + 籌碼共振型",
        "description": "站上月線且外資投信同步買超時進場，跌破月線���三大法人合計轉賣時退場",
        "strategy_text": "收盤價站上20日均線且外資買超且投信買超就買進；收盤價跌破20日均線或三大法人合計轉賣就賣出。",
    },
    {
        "id": "ma_golden_cross",
        "name": "均線黃金 / 死亡交叉型",
        "description": "5日均線黃金交叉20日均線時進場，死亡交叉時退場，搭配8%固定停損",
        "strategy_text": "5日均線黃金交叉20日均線就買進；5日均線死亡交叉20日均線或停損8%就賣出。",
    },
    {
        "id": "price_breakout_high",
        "name": "高點突破 + 停利型",
        "description": "收盤價突破20日高點進場，跌破10日低點或停利15%退場",
        "strategy_text": "突破20日高點就買進；跌破10日低點或停利15%就賣出。",
    },
    {
        "id": "triple_ma_trend",
        "name": "多重均線趨勢型",
        "description": "短中長均線同向排列時進場，月線跌破時退場",
        "strategy_text": "收盤價站上20日均線且收盤價站上60日均線就買進；收盤價跌破20日均線就賣出。",
    },
]
