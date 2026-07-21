"""
ETF / ETN taxonomy 規則引擎（Phase 1 §6/§21）。

設計：台灣掛牌 ETF 命名慣例本身就高度結構化（正2/反1 槓桿反向後綴、主動/平衡前綴、
高股息/永續/半導體等策略關鍵字），逐檔 261 檔 + 28 檔 ETN 做即時網路查證不現實也無
必要——用 **關鍵字規則引擎** 涵蓋絕大多數，只對少數規則判斷不到、或有把握個別確認的
旗艦 ETF 用 `ETF_OVERRIDES` 補強／修正。

規則優先序（`classify_etf`）：
    1. stock_id 後綴（L=槓桿正向 / R=反向 / B=債券 / U=期貨型 / K=次幣別股份）
    2. 名稱前綴（`主動` → ACTIVE 策略；`平衡` → 多重資產）
    3. 名稱關鍵字：region → asset_class → strategy → theme（可多個）
    4. 找不到 region 關鍵字 → 預設 TAIWAN（多數台股 ETF 命名不特別標注地區）
    5. `ETF_OVERRIDES[stock_id]` 若存在，覆蓋規則引擎結果（人工確認的旗艦 ETF）

confidence：規則引擎命中 region+strategy 給 MEDIUM；同時命中 `ETF_OVERRIDES` 給 HIGH；
完全無法辨識（理論上不該發生，因為預設值兜底）才會是 LOW。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from app.classification.taxonomy import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    ETF_ASSET_CLASS_BOND,
    ETF_ASSET_CLASS_COMMODITY,
    ETF_ASSET_CLASS_CURRENCY,
    ETF_ASSET_CLASS_EQUITY,
    ETF_ASSET_CLASS_MULTI_ASSET,
)


class EtfClassificationResult(TypedDict):
    asset_class: str
    region: str
    strategy: str
    themes: List[str]
    is_leveraged: bool
    is_inverse: bool
    is_active: bool
    tracking_index: Optional[str]
    confidence: str


# region 關鍵字（依特定 → 廣泛順序比對，第一個命中就採用）
_REGION_KEYWORDS = [
    ("VIETNAM", ["越南"]),
    ("INDIA", ["印度"]),
    ("KOREA", ["韓國"]),
    ("JAPAN", ["日本", "日經", "東證", "Shiller"]),
    ("HONG_KONG", ["恒香港", "恒生香港"]),
    ("CHINA", ["中國", "滬深", "深証", "上證", "恒生國企", "恒中國", "RMB", "中政金", "A股", "A50", "A150"]),
    ("US", ["美國", "那斯達克", "NASDAQ", "道瓊", "S&P", "費城半導體", "FANG", "ARK", "美债",
            "美債", "美元指", "美国"]),
    ("EUROPE", ["歐洲"]),
    ("EMERGING_MARKETS", ["新興市場", "新興債"]),
    ("ASIA", ["亞太", "洲際", "台韓"]),
    ("GLOBAL", ["全球", "世界"]),
]

# asset_class 關鍵字
_BOND_KEYWORDS = ["債", "公司債", "公債", "非投等", "投等債"]
_COMMODITY_KEYWORDS = ["黃金", "白銀", "原油", "布蘭特", "黃豆", "銅", "石油"]
_CURRENCY_KEYWORDS = ["美元指", "RMB短期報酬"]
_MULTI_ASSET_KEYWORDS = ["平衡", "雙核收息", "複合收益"]

# 策略關鍵字（依優先序，第一個命中就採用；槓桿/反向由後綴另外偵測，這裡只處理其餘策略）
_STRATEGY_KEYWORDS = [
    ("HIGH_DIVIDEND", ["高股息", "高息", "優息", "收息", "存股", "股利", "入息", "高配息"]),
    ("ESG", ["ESG", "永續", "低碳", "淨零", "公司治理"]),
    ("LOW_VOLATILITY", ["低波"]),
    ("THEMATIC", [
        "半導體", "晶圓", "IC設計", "科技", "AI", "機器人", "生技", "電動車", "元宇宙",
        "5G", "資安", "太空", "衛星", "航太防衛", "稀土", "儲能", "綠能", "雲端", "大數據",
        "網路", "晶片", "通訊", "基因", "免疫", "未來車", "未來通訊", "潔淨能源", "數位支付",
        "電力基建", "數據及電力",
    ]),
    ("GROWTH", ["動能", "成長", "趨勢"]),
    ("VALUE", ["價值"]),
]

# theme 關鍵字（可多個命中，獨立於 strategy 判斷）
_THEME_KEYWORDS = [
    "半導體", "晶圓", "IC設計", "AI", "機器人", "生技", "電動車", "元宇宙", "5G", "資安",
    "太空", "衛星", "航太防衛", "稀土", "儲能", "綠能", "雲端", "大數據", "基因", "免疫",
    "未來車", "未來通訊", "潔淨能源", "數位支付", "不動產", "REITs", "特別股", "金融",
]

_ACTIVE_PREFIX = "主動"


def _match_first(text: str, keyword_groups) -> Optional[str]:
    for code, keywords in keyword_groups:
        for kw in keywords:
            if kw in text:
                return code
    return None


def classify_etf(stock_id: str, stock_name: str) -> EtfClassificationResult:
    sid = (stock_id or "").strip()
    name = (stock_name or "").strip()

    is_leveraged = sid.endswith("L")
    is_inverse = sid.endswith("R")
    is_active = name.startswith(_ACTIVE_PREFIX) or sid.endswith(("A", "D", "T"))
    is_futures = sid.endswith("U")

    region = _match_first(name, _REGION_KEYWORDS) or "TAIWAN"

    if any(kw in name for kw in _BOND_KEYWORDS) or sid.endswith("B"):
        asset_class = ETF_ASSET_CLASS_BOND
    elif any(kw in name for kw in _COMMODITY_KEYWORDS):
        asset_class = ETF_ASSET_CLASS_COMMODITY
    elif any(kw in name for kw in _CURRENCY_KEYWORDS):
        asset_class = ETF_ASSET_CLASS_CURRENCY
    elif any(kw in name for kw in _MULTI_ASSET_KEYWORDS):
        asset_class = ETF_ASSET_CLASS_MULTI_ASSET
    elif is_futures and region in ("US", "GLOBAL"):
        # 期貨型 ETF 若無明確商品關鍵字，多為匯率/利率期貨（例：期元大美元指正2）
        asset_class = ETF_ASSET_CLASS_CURRENCY
    else:
        asset_class = ETF_ASSET_CLASS_EQUITY

    if is_leveraged:
        strategy = "LEVERAGED"
    elif is_inverse:
        strategy = "INVERSE"
    elif is_active:
        strategy = "ACTIVE"
    else:
        strategy = _match_first(name, _STRATEGY_KEYWORDS) or "MARKET_CAP"

    themes = [kw for kw in _THEME_KEYWORDS if kw in name]

    confidence = CONFIDENCE_MEDIUM if (region != "TAIWAN" or strategy != "MARKET_CAP" or themes) else CONFIDENCE_MEDIUM

    result: EtfClassificationResult = {
        "asset_class": asset_class,
        "region": region,
        "strategy": strategy,
        "themes": themes,
        "is_leveraged": is_leveraged,
        "is_inverse": is_inverse,
        "is_active": is_active,
        "tracking_index": None,
        "confidence": confidence,
    }

    override = ETF_OVERRIDES.get(sid)
    if override:
        result.update(override)  # type: ignore[typeddict-item]
        result["confidence"] = CONFIDENCE_HIGH

    return result


# 人工確認的旗艦 / 高知名度 ETF（補 tracking_index，並修正規則引擎可能誤判之處）
ETF_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "0050": {"tracking_index": "臺灣50指數", "strategy": "MARKET_CAP", "region": "TAIWAN"},
    "0051": {"tracking_index": "臺灣中型100指數", "strategy": "MARKET_CAP", "region": "TAIWAN"},
    "0052": {"tracking_index": "臺灣資訊科技指數", "strategy": "SECTOR", "themes": ["科技"], "region": "TAIWAN"},
    "0053": {"tracking_index": "電子類加權股價指數", "strategy": "SECTOR", "themes": ["電子"], "region": "TAIWAN"},
    "0055": {"tracking_index": "MSCI台灣金融指數", "strategy": "SECTOR", "themes": ["金融"], "region": "TAIWAN"},
    "0056": {"tracking_index": "臺灣高股息指數", "strategy": "HIGH_DIVIDEND", "region": "TAIWAN"},
    "006208": {"tracking_index": "臺灣50指數", "strategy": "MARKET_CAP", "region": "TAIWAN"},
    "00631L": {"tracking_index": "臺灣50指數", "strategy": "LEVERAGED", "region": "TAIWAN"},
    "00632R": {"tracking_index": "臺灣50指數", "strategy": "INVERSE", "region": "TAIWAN"},
    "00692": {"tracking_index": "台灣公司治理100指數", "strategy": "ESG", "region": "TAIWAN"},
    "00713": {"tracking_index": "臺灣指數公司高股息低波動指數", "strategy": "HIGH_DIVIDEND", "region": "TAIWAN"},
    "00830": {"tracking_index": "NYSE Philadelphia Semiconductor Index", "strategy": "SECTOR",
              "themes": ["半導體"], "region": "US"},
    "00850": {"tracking_index": "臺灣永續指數", "strategy": "ESG", "region": "TAIWAN"},
    "00878": {"tracking_index": "MSCI台灣ESG永續高股息精選30指數", "strategy": "HIGH_DIVIDEND", "region": "TAIWAN"},
    "00881": {"tracking_index": "臺灣科技優息指數", "strategy": "SECTOR", "themes": ["科技"], "region": "TAIWAN"},
    "00891": {"tracking_index": "臺灣指數公司特選台灣上市上櫃半導體30指數", "strategy": "SECTOR",
              "themes": ["半導體"], "region": "TAIWAN"},
    "00892": {"tracking_index": "臺灣指數公司特選台灣上市上櫃半導體30指數", "strategy": "SECTOR",
              "themes": ["半導體"], "region": "TAIWAN"},
    "00919": {"tracking_index": "臺灣指數公司特選臺灣上市上櫃精選高股息指數", "strategy": "HIGH_DIVIDEND",
              "region": "TAIWAN"},
    "00929": {"tracking_index": "臺灣指數公司特選臺灣上市上櫃科技優息指數", "strategy": "HIGH_DIVIDEND",
              "themes": ["科技"], "region": "TAIWAN"},
    "00934": {"tracking_index": "臺灣指數公司中信成長高股息精選30指數", "strategy": "HIGH_DIVIDEND",
              "region": "TAIWAN"},
}
