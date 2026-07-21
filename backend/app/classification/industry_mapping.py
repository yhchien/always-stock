"""
FinMind `industry_name`（原始，含歷史重複命名批次）→ canonical `primary_sector` 系統性映射
（Phase 1 §9/§10/§18）。

`source_industry` 永遠保留 `stocks_master.industry_name` 原值（不覆寫，§9）；本檔只負責
「這個原始值該歸進哪個 canonical primary_sector」的規則層。

設計：
    - FinMind 因不同批次 ETL 對同一概念產生重複命名（例：`半導體` vs `半導體業`，
      見 current_industry_data_flow.md 第 2 節），這裡直接 consolidate 成同一
      primary_sector，因為抽樣檢查兩批股票（例：半導體業=日月光/矽品/華亞科等）業務性質
      與主要批次一致，屬於同概念的命名差異，非真正的分類分歧
    - `NEEDS_OVERRIDE` 標記代表這個 industry_name 底下的股票業務組成太混雜
      （例：`電子工業`、`其他`、`食品生技`），必須查 `stock_overrides.py` 逐檔決定，
      不可用單一 industry_name → primary_sector 規則硬套
    - Asset type 非 COMMON_STOCK 的 industry_name（ETF/ETN/Index/大盤/存託憑證/受益證券）
      不在這裡處理 primary_sector，由 `asset_type.py` 與 `etf_mapping.py` 個別處理
"""
from __future__ import annotations

from typing import Optional

NEEDS_OVERRIDE = "__NEEDS_OVERRIDE__"

# 原始 industry_name → primary_sector code（見 taxonomy.PRIMARY_SECTORS）
# 覆蓋 2026-07-21 快照全部 87 個 distinct 值 + 特殊 asset_type 類別的 pass-through 標記
INDUSTRY_NAME_TO_PRIMARY_SECTOR = {
    # 半導體
    "半導體": "SEMICONDUCTOR",
    "半導體業": "SEMICONDUCTOR",
    # PCB / 電子材料
    "印刷電路板": "PCB_ELECTRONIC_MATERIALS",
    # 被動元件 / 連接器
    "被動元件": "PASSIVE_CONNECTOR",
    "連接器": "PASSIVE_CONNECTOR",
    "電子零組件業": "PASSIVE_CONNECTOR",
    # 電腦及週邊設備
    "電腦及週邊設備": "COMPUTER_PERIPHERALS",
    "電腦及週邊設備業": "COMPUTER_PERIPHERALS",
    # 光電（面板/背光/觸控/光學）
    "平面顯示器": "OPTOELECTRONICS",
    "光電業": "OPTOELECTRONICS",
    "觸控面板": "OPTOELECTRONICS",
    # LED 照明（獨立供應鏈，不併入光電）
    "LED照明產業": "LED_LIGHTING",
    # 太陽能
    "太陽能產業": "SOLAR_ENERGY",
    # 風力發電
    "風力發電": "WIND_ENERGY",
    # 通信網路
    "通信網路": "COMMUNICATION_NETWORK",
    "通信網路業": "COMMUNICATION_NETWORK",
    "資通訊安全": "COMMUNICATION_NETWORK",
    # 電子零組件通路
    "電子通路業": "ELECTRONIC_DISTRIBUTION",
    "電子商務": "SOFTWARE_ECOMMERCE",
    # AI / 大數據 / 雲端 / 軟體
    "人工智慧": "AI",
    "大數據": "BIG_DATA_CLOUD",
    "雲端運算": "BIG_DATA_CLOUD",
    "數位雲端": "BIG_DATA_CLOUD",
    "軟體服務": "SOFTWARE_ECOMMERCE",
    "區塊鏈": "SOFTWARE_ECOMMERCE",
    # 電機機械 / 自動化 / 重電
    "電機機械": "ELECTRICAL_MACHINERY",
    "自動化": "AUTOMATION_ROBOTICS",
    "電器電纜": "ELECTRICAL_CABLE",
    # 智慧電網：多為電線電纜/重電/變壓器製造商，個別業務差異大者見 stock_overrides.py
    "智慧電網": "ELECTRICAL_CABLE",
    # 汽車 / 電動車
    "汽車": "AUTOMOTIVE",
    "電動車輛產業": "EV_BATTERY",
    "能源元件": "EV_BATTERY",
    # 鋼鐵 / 水泥 / 建材營造
    "鋼鐵": "STEEL",
    "水泥": "CEMENT",
    "建材營造": "BUILDING_MATERIALS_CONSTRUCTION",
    # 石化 / 塑膠橡膠 / 化學
    "石化及塑橡膠": "PETROCHEMICAL",
    "塑膠工業": "PLASTICS_RUBBER",
    "化學工業": "CHEMICAL",
    # 紡織
    "紡織": "TEXTILE_FIBER",
    "紡織纖維": "TEXTILE_FIBER",
    # 造紙
    "造紙": "PAPER",
    "造紙工業": "PAPER",
    # 食品
    "食品": "FOOD",
    # 醫療 / 製藥
    "醫療器材": "MEDICAL_DEVICE",
    "製藥": "PHARMACEUTICAL",
    "再生醫療": "BIOTECH_MEDICAL",
    # 貿易百貨
    "貿易百貨": "RETAIL_TRADE",
    # 金融：primary_sector 統一為 FINANCIAL，但 sub_sector（金控/銀行/保險/證券/租賃）
    # 需要逐檔 override（FinMind sub_industry 把金控/銀行/保險三種混在同一個值裡）
    "金融": NEEDS_OVERRIDE,
    "金融保險": NEEDS_OVERRIDE,
    # 休閒娛樂 / 運動休閒 / 文化創意
    "休閒娛樂": "LEISURE_TOURISM",
    "運動休閒": "SPORTING_GOODS",
    "運動科技": "SPORTING_GOODS",
    "體驗科技": "LEISURE_TOURISM",
    "文化創意業": "CULTURAL_CREATIVE",
    # 交通運輸及航運（產業層混合海運/空運/物流，個股層再細分 sub_sector）
    "交通運輸及航運": "SHIPPING_CONTAINER",
    "航運業": "SHIPPING_CONTAINER",
    # 能源與公用事業
    "油電燃氣": "ENERGY_UTILITY",
    "汽電共生": "ENERGY_UTILITY",
    "綠能環保": "ENVIRONMENTAL_SERVICES",
    # 需要逐檔 override 的混合 / catch-all 類別
    "電子工業": NEEDS_OVERRIDE,
    "其他": NEEDS_OVERRIDE,
    "其他電子業": NEEDS_OVERRIDE,
    "化學生技醫療": NEEDS_OVERRIDE,
    "食品生技": NEEDS_OVERRIDE,
    "生技醫療業": NEEDS_OVERRIDE,
    "汽車工業": NEEDS_OVERRIDE,
    "創新版股票": NEEDS_OVERRIDE,
}


def map_industry_name_to_primary_sector(industry_name: Optional[str]) -> Optional[str]:
    """回傳 primary_sector code；若查無對照，回傳 None（呼叫端應視為需要 override）。"""
    if not industry_name:
        return None
    return INDUSTRY_NAME_TO_PRIMARY_SECTOR.get(industry_name.strip())
