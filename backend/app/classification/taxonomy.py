"""
Canonical taxonomy 定義（Phase 1 §10/§11/§21）。

`PRIMARY_SECTORS`：普通股（含金融股）的 Level 1 分類，目標 30~60 類（本版 45 類）。
每類定義為「受相似需求/供給/終端市場/景氣循環驅動的公司群」，非直接照抄 TWSE 33 類，
也刻意合併 FinMind 因不同批次 ETL 產生的重複命名（例如 半導體 / 半導體業）。

sub_sector 本版**不**建立獨立 canonical 列表——沿用 `stocks_master.sub_industry`
本身已有的顆粒度（多數已達 peer-group 水準，見 current_industry_data_flow.md 第 2 節），
只在缺值或需要拆分大類時才由 `stock_overrides.py` 個別指定。這是刻意的工程決策：
FinMind 的 sub_industry 覆蓋率 67%、且有值時品質已經不錯，重新發明一套 sub_sector
taxonomy 並全部 remap 的邊際效益低於直接沿用 + 補洞。

ETF taxonomy 見本檔 `ETF_ASSET_CLASSES` / `ETF_REGIONS` / `ETF_STRATEGIES`。
"""
from __future__ import annotations

MAPPING_VERSION = "v1"

# ---------------------------------------------------------------------------
# asset_type enum
# ---------------------------------------------------------------------------
ASSET_TYPE_COMMON_STOCK = "COMMON_STOCK"
ASSET_TYPE_ETF = "ETF"
ASSET_TYPE_ETN = "ETN"
ASSET_TYPE_PREFERRED_STOCK = "PREFERRED_STOCK"
ASSET_TYPE_DR = "DR"                    # 存託憑證（TDR）
ASSET_TYPE_REIT = "REIT"                # 不動產投資信託受益證券
ASSET_TYPE_INDEX_BENCHMARK = "INDEX_BENCHMARK"  # 非真實證券的指數佔位列（Index/大盤）
ASSET_TYPE_OTHER = "OTHER"

ALL_ASSET_TYPES = (
    ASSET_TYPE_COMMON_STOCK,
    ASSET_TYPE_ETF,
    ASSET_TYPE_ETN,
    ASSET_TYPE_PREFERRED_STOCK,
    ASSET_TYPE_DR,
    ASSET_TYPE_REIT,
    ASSET_TYPE_INDEX_BENCHMARK,
    ASSET_TYPE_OTHER,
)

# ---------------------------------------------------------------------------
# confidence enum
# ---------------------------------------------------------------------------
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"

# ---------------------------------------------------------------------------
# primary_sector taxonomy（45 類，普通股 + 金融股）
# ---------------------------------------------------------------------------
PRIMARY_SECTORS = {
    "SEMICONDUCTOR": "半導體",
    "PCB_ELECTRONIC_MATERIALS": "印刷電路板與電子材料",
    "PASSIVE_CONNECTOR": "被動元件與連接器",
    "COMPUTER_PERIPHERALS": "電腦及週邊設備",
    "OPTOELECTRONICS": "光電（面板/觸控/光學）",
    "LED_LIGHTING": "LED照明",
    "SOLAR_ENERGY": "太陽能",
    "WIND_ENERGY": "風力發電",
    "COMMUNICATION_NETWORK": "通信網路",
    "ELECTRONIC_DISTRIBUTION": "電子零組件通路與服務",
    "FACTORY_ENGINEERING": "廠務工程與系統整合",
    "AI": "人工智慧",
    "BIG_DATA_CLOUD": "大數據與雲端運算",
    "SOFTWARE_ECOMMERCE": "軟體服務與電子商務",
    "ELECTRICAL_MACHINERY": "電機機械",
    "ELECTRICAL_CABLE": "電器電纜與重電",
    "AUTOMATION_ROBOTICS": "自動化與機器人",
    "AUTOMOTIVE": "汽車與零組件",
    "EV_BATTERY": "電動車輛與電池",
    "STEEL": "鋼鐵",
    "CEMENT": "水泥",
    "BUILDING_MATERIALS_CONSTRUCTION": "建材營造",
    "PETROCHEMICAL": "石化",
    "PLASTICS_RUBBER": "塑膠與橡膠製品",
    "TEXTILE_FIBER": "紡織與人纖",
    "CHEMICAL": "化學工業",
    "PAPER": "造紙",
    "FOOD": "食品",
    "HEALTH_SUPPLEMENT": "保健食品",
    "PHARMACEUTICAL": "製藥",
    "BIOTECH_MEDICAL": "生技醫療",
    "MEDICAL_DEVICE": "醫療器材",
    "RETAIL_TRADE": "貿易百貨與零售",
    # 金融：spec 明訂 primary_sector 統一為「金融」，細分由 sub_sector 承擔
    # （金融控股 / 銀行 / 保險 / 證券期貨 / 租賃與消費金融 / 其他金融）
    "FINANCIAL": "金融",
    "REAL_ESTATE_TRUST": "不動產投資信託",
    "SHIPPING_CONTAINER": "海運（貨櫃／散裝）",
    "AVIATION": "航空",
    "LOGISTICS": "物流與貨運承攬",
    "SHIPBUILDING": "造船",
    "AEROSPACE_DEFENSE": "航太國防",
    "LEISURE_TOURISM": "休閒觀光與娛樂",
    "SPORTING_GOODS": "運動休閒用品",
    "CULTURAL_CREATIVE": "文化創意",
    "ENERGY_UTILITY": "能源與公用事業",
    "ENVIRONMENTAL_SERVICES": "環保工程與資源循環",
    "AGRI_TECH": "農業科技",
    "SECURITY_SERVICES": "保全服務",
    "HOME_APPLIANCE_CONSUMER": "家電與民生消費品",
    "INVESTMENT_HOLDING": "投資控股",
    "DIVERSIFIED_OTHER": "其他（待歸類）",
}

# ---------------------------------------------------------------------------
# ETF taxonomy（Phase 1 §6/§21）
# ---------------------------------------------------------------------------
ETF_ASSET_CLASS_EQUITY = "EQUITY"
ETF_ASSET_CLASS_BOND = "BOND"
ETF_ASSET_CLASS_COMMODITY = "COMMODITY"
ETF_ASSET_CLASS_MULTI_ASSET = "MULTI_ASSET"
ETF_ASSET_CLASS_CURRENCY = "CURRENCY"
ETF_ASSET_CLASS_OTHER = "OTHER"

ETF_ASSET_CLASSES = (
    ETF_ASSET_CLASS_EQUITY,
    ETF_ASSET_CLASS_BOND,
    ETF_ASSET_CLASS_COMMODITY,
    ETF_ASSET_CLASS_MULTI_ASSET,
    ETF_ASSET_CLASS_CURRENCY,
    ETF_ASSET_CLASS_OTHER,
)

ETF_REGIONS = (
    "TAIWAN",
    "US",
    "JAPAN",
    "CHINA",
    "HONG_KONG",
    "KOREA",
    "INDIA",
    "VIETNAM",
    "EUROPE",
    "GLOBAL",
    "ASIA",
    "EMERGING_MARKETS",
    "OTHER",
)

# ---------------------------------------------------------------------------
# 金融 sub_sector（唯一固定 canonical sub_sector 列表；spec §5 明訂）
# 其餘 primary_sector 的 sub_sector 直接沿用 stocks_master.sub_industry，見本檔頂部說明
# ---------------------------------------------------------------------------
FINANCIAL_SUB_SECTORS = (
    "金融控股",
    "銀行",
    "保險",
    "證券期貨",
    "租賃與消費金融",
    "其他金融",
)

ETF_STRATEGIES = (
    "MARKET_CAP",
    "HIGH_DIVIDEND",
    "ESG",
    "GROWTH",
    "VALUE",
    "LOW_VOLATILITY",
    "SECTOR",
    "THEMATIC",
    "LEVERAGED",
    "INVERSE",
    "ACTIVE",
    "BOND_DURATION",
    "MULTI_ASSET_BALANCED",
    "OTHER",
)
