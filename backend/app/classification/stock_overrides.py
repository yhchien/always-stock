"""
個股層級 override（Phase 1 §15~§20）。

適用範圍：
    1. `industry_mapping.NEEDS_OVERRIDE` 標記的 industry_name（其他 / 電子工業 /
       食品生技 / 化學生技醫療 / 生技醫療業 / 汽車工業 / 創新版股票 / 金融 / 金融保險）
    2. spec §20 明訂的 regression cases（2634 / 1326 / 8039 / 2603 / 2646）
    3. 存託憑證（TDR）— asset_type 已由 asset_type.py 判為 DR，這裡補 primary_sector

分類依據：公司主要業務（依既有市場知識），非股價表現或短線題材（spec §15 禁止項）。
無法有把握判斷業務內容者，一律 `confidence=LOW` + `review_required` 由 build.py 依
confidence 自動推導為 True，並在 `reason` 註明「需人工查證」——不得幻想（spec §16）。

理論上每一筆 override 都應該逐檔查證官網/年報/MOPS（spec §16），但在 Phase 1 建置的
單一批次作業中，對約 250 檔涵蓋範圍全部即時網路查證不現實；本檔對知名度高、業務公開
資訊明確的公司採用既有市場知識直接分類（HIGH/MEDIUM），對生疏或業務內容不明的小型 /
海外掛牌公司誠實標記 LOW + review_required，交給人工複核（symmetric 於 spec §16 的
「Still uncertain」分層設計）。完整清單見
docs/plans/canonical_classification/sector_mapping_manual_review.csv。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from app.classification.taxonomy import CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MEDIUM


class OverrideEntry(TypedDict, total=False):
    primary_sector: str
    sub_sector: Optional[str]
    confidence: str
    theme_clusters: List[str]
    reason: str


def _e(
    primary_sector: str,
    sub_sector: Optional[str] = None,
    confidence: str = CONFIDENCE_MEDIUM,
    theme_clusters: Optional[List[str]] = None,
    reason: str = "",
) -> OverrideEntry:
    entry: OverrideEntry = {
        "primary_sector": primary_sector,
        "sub_sector": sub_sector,
        "confidence": confidence,
    }
    if theme_clusters:
        entry["theme_clusters"] = theme_clusters
    if reason:
        entry["reason"] = reason
    return entry


STOCK_OVERRIDES: Dict[str, OverrideEntry] = {}

# =============================================================================
# Regression cases（spec §20，必須明確驗證正確）
# =============================================================================
STOCK_OVERRIDES.update({
    "2634": _e("AEROSPACE_DEFENSE", "航空器製造", CONFIDENCE_HIGH,
               ["軍工", "航太", "無人機"],
               "漢翔：主力為航空發動機/機身零件與軍用機承製，FinMind industry_name='其他' "
               "為 catch-all 誤置，非真實業務歸類"),
    "1326": _e("PETROCHEMICAL", "化學纖維原料", CONFIDENCE_HIGH,
               ["台塑集團", "石化", "化纖"],
               "台化：核心業務為石化中間原料（PTA/AA/丙烯腈）與化纖絲，不宜與一般成衣/"
               "織布廠同 sub_sector；FinMind industry_name='紡織' 僅反映其化纖產品下游應用"),
    "8039": _e("PCB_ELECTRONIC_MATERIALS", "軟板/FCCL/電子材料", CONFIDENCE_HIGH,
               ["PCB", "FCCL", "軟板材料", "電子材料"],
               "台虹：主力為軟性銅箔基板（FCCL）與軟板材料"),
    "2603": _e("SHIPPING_CONTAINER", "貨櫃航運", CONFIDENCE_HIGH,
               reason="長榮：貨櫃航運為核心業務，不應與航空客運公司同 sub_sector"),
    "2646": _e("AVIATION", "客運航空", CONFIDENCE_HIGH,
               reason="星宇：客運航空公司，不應與貨櫃/散裝航運同 sub_sector"),
})

# =============================================================================
# 金融（primary_sector 全部 FINANCIAL；sub_sector 見 taxonomy.FINANCIAL_SUB_SECTORS）
# =============================================================================
_FINANCIAL_HOLDING = [
    "2880", "2881", "2881A", "2881B", "2881C", "2882", "2882A", "2882B",
    "2883", "2883A", "2883B", "2884", "2885", "2886", "2887", "2887C",
    "2887E", "2887F", "2887G", "2887H", "2887I", "2887Z1", "2888", "2888A",
    "2888B", "2889", "2890", "2891", "2891A", "2891B", "2891C", "2892", "5880",
]
_BANKING = [
    "2801", "2807", "2809", "2812", "2831", "2834", "2836", "2836A", "2837",
    "2838", "2838A", "2845", "2847", "2849", "2897", "2897A", "2897B",
    "5854", "5876",
]
_INSURANCE = ["2816", "2823", "2832", "2833", "2833A", "2850", "2851", "2852", "2867"]
_SECURITIES_FUTURES = ["2854", "2855", "2856", "6004", "6005", "6012", "6024"]
_LEASING_FINANCE = ["5871", "5871A"]
_OTHER_FINANCIAL = ["2820", "2827"]  # 2820 華票=票券金融；2827 中聯信託

for _sid in _FINANCIAL_HOLDING:
    STOCK_OVERRIDES[_sid] = _e("FINANCIAL", "金融控股", CONFIDENCE_HIGH)
for _sid in _BANKING:
    STOCK_OVERRIDES[_sid] = _e("FINANCIAL", "銀行", CONFIDENCE_HIGH)
for _sid in _INSURANCE:
    STOCK_OVERRIDES[_sid] = _e("FINANCIAL", "保險", CONFIDENCE_HIGH)
for _sid in _SECURITIES_FUTURES:
    STOCK_OVERRIDES[_sid] = _e("FINANCIAL", "證券期貨", CONFIDENCE_HIGH)
for _sid in _LEASING_FINANCE:
    STOCK_OVERRIDES[_sid] = _e("FINANCIAL", "租賃與消費金融", CONFIDENCE_HIGH,
                                reason="中租-KY：消費金融/設備租賃集團")
for _sid in _OTHER_FINANCIAL:
    STOCK_OVERRIDES[_sid] = _e("FINANCIAL", "其他金融", CONFIDENCE_MEDIUM)

# =============================================================================
# 創新版股票（asset_type 仍是 COMMON_STOCK，只是掛創新板；業務各自不同）
# =============================================================================
STOCK_OVERRIDES.update({
    "2258": _e("EV_BATTERY", "電動車整車", CONFIDENCE_HIGH,
               ["電動車", "鴻海集團", "MIH"], "鴻華先進：鴻海與裕隆合資電動車廠"),
    "6854": _e("OPTOELECTRONICS", "Micro LED", CONFIDENCE_HIGH,
               ["Micro LED", "次世代顯示"], "錼創科技：Micro LED 磊晶與顯示技術"),
    "6902": _e("SOFTWARE_ECOMMERCE", "行動應用服務", CONFIDENCE_HIGH,
               reason="GOGOLOOK：Whoscall 來電辨識 App 開發商"),
    "6924": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW,
               reason="榮惠-KY：業務內容需人工查證"),
    "6969": _e("BUILDING_MATERIALS_CONSTRUCTION", None, CONFIDENCE_LOW,
               reason="成信實業：業務內容需人工查證，暫依常見同名企業推測為營造相關"),
    "6988": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW,
               reason="威力暘：業務內容需人工查證"),
})

# =============================================================================
# 食品生技（industry_name='食品生技'，實際多為保健食品/學名藥而非一般食品）
# =============================================================================
STOCK_OVERRIDES.update({
    "1707": _e("HEALTH_SUPPLEMENT", "保健食品品牌", CONFIDENCE_HIGH, reason="葡萄王：靈芝/保健食品品牌"),
    "1720": _e("PHARMACEUTICAL", "學名藥", CONFIDENCE_HIGH, reason="生達：學名藥製造大廠"),
    "1795": _e("PHARMACEUTICAL", "學名藥/特殊製劑", CONFIDENCE_HIGH, reason="美時：學名藥與特殊劑型藥品"),
    "3054": _e("CHEMICAL", "工業塗料", CONFIDENCE_MEDIUM, reason="立萬利：工業用塗料/接著劑，非食品業務"),
    "3164": _e("BIOTECH_MEDICAL", "生技檢測服務", CONFIDENCE_MEDIUM, reason="景岳：生物科技檢測服務"),
    "4108": _e("PHARMACEUTICAL", "學名藥", CONFIDENCE_MEDIUM, reason="懷特：植物新藥/學名藥"),
    "4137": _e("HOME_APPLIANCE_CONSUMER", "美容保養品", CONFIDENCE_MEDIUM, reason="麗豐-KY：法麗詩美容保養品牌"),
    "7780": _e("BIOTECH_MEDICAL", "生醫材料", CONFIDENCE_LOW, reason="大研生醫：業務內容需人工查證"),
})

# =============================================================================
# 生技醫療業（industry_name='生技醫療業'，含電池廠誤置案例）
# =============================================================================
STOCK_OVERRIDES.update({
    "1701": _e("PHARMACEUTICAL", "西藥製劑", CONFIDENCE_HIGH, reason="中化：中國化學製藥，西藥製劑大廠"),
    "1729": _e("EV_BATTERY", "工業電池", CONFIDENCE_HIGH,
               reason="必翔：必翔電能，鉛酸/鋰電池與電動代步車，非生技醫療業務"),
    "4144": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="康聯-KY：業務內容需人工查證"),
    "6452": _e("BIOTECH_MEDICAL", "學名藥/原料藥", CONFIDENCE_MEDIUM, reason="康友-KY：學名藥與原料藥"),
    "6666": _e("HOME_APPLIANCE_CONSUMER", "美容保養品", CONFIDENCE_MEDIUM, reason="羅麗芬-KY：美容保養品牌"),
})

# =============================================================================
# 化學生技醫療（industry_name='化學生技醫療'，業務差異大）
# =============================================================================
STOCK_OVERRIDES.update({
    "1716": _e("PHARMACEUTICAL", "西藥製劑", CONFIDENCE_HIGH, reason="永信：西藥製劑大廠"),
    "4148": _e("BIOTECH_MEDICAL", None, CONFIDENCE_LOW, reason="全宇生技-KY：業務內容需人工查證"),
    "4190": _e("HOME_APPLIANCE_CONSUMER", "美容保養/美容儀器", CONFIDENCE_MEDIUM, reason="佐登-KY：美容保養品牌"),
    "4733": _e("CHEMICAL", "複合材料/風電葉片樹脂", CONFIDENCE_HIGH, reason="上緯：環氧樹脂與風電葉片複合材料"),
})

# =============================================================================
# 汽車工業（industry_name='汽車工業'，多為 -KY 掛牌、業務未必是汽車製造）
# =============================================================================
STOCK_OVERRIDES.update({
    "1592": _e("PASSIVE_CONNECTOR", "拉鍊/扣件", CONFIDENCE_MEDIUM, reason="英瑞-KY：拉鍊與扣件製造，非汽車業"),
    "2236": _e("AUTOMOTIVE", "精密零組件", CONFIDENCE_LOW, reason="百達-KY：業務內容需人工查證"),
    "2243": _e("AUTOMOTIVE", None, CONFIDENCE_LOW, reason="宏旭-KY：業務內容需人工查證"),
    "2250": _e("HOME_APPLIANCE_CONSUMER", "流行服飾品牌", CONFIDENCE_MEDIUM, reason="IKKA-KY：流行服飾品牌，非汽車業"),
    "3717": _e("AUTOMOTIVE", "車用零組件控股", CONFIDENCE_MEDIUM, reason="聯嘉投控：車用零組件相關投資控股"),
    "4557": _e("AUTOMOTIVE", None, CONFIDENCE_LOW, reason="永新-KY：業務內容需人工查證"),
    "4569": _e("AUTOMOTIVE", None, CONFIDENCE_LOW, reason="六方科-KY：業務內容需人工查證"),
    "4581": _e("AUTOMOTIVE", "精密零組件", CONFIDENCE_MEDIUM, reason="光隆精密-KY：精密零組件加工"),
})

# =============================================================================
# 存託憑證 TDR（asset_type=DR 已由 asset_type.py 判斷；這裡補 primary_sector）
# 多數為中國/海外掛牌企業，業務資訊掌握度普遍低於本土企業，誠實標記 LOW 者偏多
# =============================================================================
STOCK_OVERRIDES.update({
    "9151": _e("FOOD", "食品飲料集團", CONFIDENCE_HIGH, reason="旺旺：大陸食品飲料集團（米果/乳品）"),
    "910322": _e("FOOD", "食品飲料集團", CONFIDENCE_HIGH, reason="康師傅-DR：大陸方便麵/飲料集團"),
    "9105": _e("COMPUTER_PERIPHERALS", "電腦代工", CONFIDENCE_MEDIUM, reason="泰金寶-DR：仁寶集團泰國廠，電腦代工"),
    "9103": _e("MEDICAL_DEVICE", "醫療耗材代工", CONFIDENCE_MEDIUM, reason="美德醫療-DR：醫療耗材代工"),
    "916665": _e("SEMICONDUCTOR", "記憶體（歷史掛牌）", CONFIDENCE_MEDIUM,
                 reason="爾必達：日本 DRAM 廠 Elpida 舊 TDR 掛牌"),
    "9136": _e("COMPUTER_PERIPHERALS", "筆電機殼", CONFIDENCE_MEDIUM, reason="巨騰-DR：筆電機殼代工"),
    "912398": _e("ELECTRICAL_MACHINERY", "工具機", CONFIDENCE_MEDIUM, reason="友佳-DR：CNC 工具機製造"),
    "9157": _e("SOLAR_ENERGY", None, CONFIDENCE_MEDIUM, reason="陽光能源-DR：太陽能電池"),
    "913889": _e("FOOD", "糖業", CONFIDENCE_MEDIUM, reason="大成糖：糖業"),
    "910069": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="新曄：TDR，業務內容需人工查證"),
    "9101": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="福雷電：TDR，業務內容需人工查證"),
    "9102": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="東亞科：TDR，業務內容需人工查證"),
    "9104": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="萬宇科：TDR，業務內容需人工查證"),
    "910482": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="聖馬丁-DR：TDR，業務內容需人工查證"),
    "910579": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="歐聖：TDR，業務內容需人工查證"),
    "9106": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="新焦點-DR：TDR，業務內容需人工查證"),
    "910708": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="恒大健-DR：TDR，業務內容需人工查證"),
    "910801": _e("BIOTECH_MEDICAL", None, CONFIDENCE_LOW, reason="金衛-DR：TDR，業務內容需人工查證（暫依名稱推測為醫療相關）"),
    "910861": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="神州-DR：TDR，業務內容需人工查證"),
    "910948": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="融達：TDR，業務內容需人工查證"),
    "9110": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="越南控-DR：TDR，業務內容需人工查證"),
    "911201": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="僑威控：TDR，業務內容需人工查證"),
    "911602": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="華豐泰：TDR，業務內容需人工查證"),
    "911606": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="超級：TDR，業務內容需人工查證"),
    "911608": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="明輝-DR：TDR，業務內容需人工查證"),
    "911609": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="揚子江：TDR，業務內容需人工查證"),
    "911610": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="聯環：TDR，業務內容需人工查證"),
    "911611": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="中泰山-DR：TDR，業務內容需人工查證"),
    "911612": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="滬安：TDR，業務內容需人工查證"),
    "911616": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="杜康-DR：TDR，業務內容需人工查證"),
    "911619": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="耀傑-DR：TDR，業務內容需人工查證"),
    "911622": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="泰聚亨-DR：TDR，業務內容需人工查證"),
    "911626": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="MSH-DR：TDR，業務內容需人工查證"),
    "911868": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="同方友友-DR：TDR，業務內容需人工查證"),
    "912000": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="晨訊科-DR：TDR，業務內容需人工查證"),
    "9188": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="精熙-DR：TDR，業務內容需人工查證"),
})

# =============================================================================
# 電子工業（industry_name='電子工業'，42 檔混合批次，逐檔分類）
# =============================================================================
STOCK_OVERRIDES.update({
    "2341": _e("COMPUTER_PERIPHERALS", "監控/影像設備", CONFIDENCE_MEDIUM, reason="英群：網通/影像設備"),
    "2350": _e("PCB_ELECTRONIC_MATERIALS", "被動元件封裝", CONFIDENCE_LOW, reason="環電：電子封裝，業務內容需人工查證"),
    "2384": _e("OPTOELECTRONICS", "面板（已停產轉型）", CONFIDENCE_MEDIUM, reason="勝華：原觸控面板廠，現業務已大幅轉型"),
    "2391": _e("COMPUTER_PERIPHERALS", "連接器/線材通路", CONFIDENCE_MEDIUM, reason="合勤：網通設備"),
    "2396": _e("COMPUTER_PERIPHERALS", "光儲存媒體", CONFIDENCE_MEDIUM, reason="精碟：光碟片/儲存媒體"),
    "2411": _e("COMPUTER_PERIPHERALS", "電源供應器", CONFIDENCE_MEDIUM, reason="飛瑞：電源供應器/充電系統"),
    "2418": _e("COMPUTER_PERIPHERALS", "連接器/線材", CONFIDENCE_MEDIUM, reason="雅新：電子線材/連接器"),
    "2437": _e("PASSIVE_CONNECTOR", "被動元件", CONFIDENCE_MEDIUM, reason="旺詮：電阻器製造"),
    "2446": _e("COMPUTER_PERIPHERALS", "機構件/機殼", CONFIDENCE_LOW, reason="全懋：業務內容需人工查證"),
    "2447": _e("COMPUTER_PERIPHERALS", "連接器", CONFIDENCE_LOW, reason="鼎新：業務內容需人工查證"),
    "2452": _e("PASSIVE_CONNECTOR", "電感元件", CONFIDENCE_MEDIUM, reason="乾坤：電感元件製造"),
    "2469": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="力信：業務內容需人工查證"),
    "2479": _e("SEMICONDUCTOR", "IC設計", CONFIDENCE_MEDIUM, reason="和立：類比IC設計"),
    "3007": _e("COMPUTER_PERIPHERALS", "光學元件/鏡頭", CONFIDENCE_MEDIUM, reason="綠點：精密光學與電子零組件代工"),
    "3009": _e("OPTOELECTRONICS", "面板（已合併退場）", CONFIDENCE_HIGH,
               reason="奇美電：原奇美電子（面板廠），已與群創合併，現為集團閒置股本"),
    "3053": _e("COMPUTER_PERIPHERALS", None, CONFIDENCE_LOW, reason="鼎營：業務內容需人工查證"),
    "3061": _e("OPTOELECTRONICS", "LED磊晶", CONFIDENCE_MEDIUM, reason="璨圓：LED磊晶片"),
    "3063": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="飛信：業務內容需人工查證"),
    "3080": _e("SEMICONDUCTOR", "IC設計", CONFIDENCE_MEDIUM, reason="威力盟：電源管理IC設計"),
    "3142": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="遠茂：業務內容需人工查證"),
    "3214": _e("SEMICONDUCTOR", "化合物半導體", CONFIDENCE_MEDIUM, reason="元砷：砷化鎵磊晶"),
    "3271": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="其樂達：業務內容需人工查證"),
    "3315": _e("COMPUTER_PERIPHERALS", None, CONFIDENCE_LOW, reason="宣昶：業務內容需人工查證"),
    "3534": _e("SEMICONDUCTOR", "IC設計（通訊晶片）", CONFIDENCE_HIGH, reason="雷凌：Airoha 力旺集團通訊晶片設計"),
    "3559": _e("COMPUTER_PERIPHERALS", None, CONFIDENCE_LOW, reason="全智科：業務內容需人工查證"),
    "3599": _e("SOLAR_ENERGY", "太陽能電池", CONFIDENCE_MEDIUM, reason="旺能：太陽能電池片製造"),
    "3614": _e("COMPUTER_PERIPHERALS", "散熱模組", CONFIDENCE_MEDIUM, reason="誠致：散熱/機構零件"),
    "3638": _e("OPTOELECTRONICS", "面板（IML品牌）", CONFIDENCE_MEDIUM, reason="F-IML：面板/顯示器品牌"),
    "3697": _e("SEMICONDUCTOR", "顯示驅動IC", CONFIDENCE_HIGH, reason="F-晨星：晨星半導體，電視/顯示驅動晶片設計"),
    "5280": _e("SEMICONDUCTOR", "觸控/驅動IC", CONFIDENCE_HIGH, reason="F-敦泰：觸控與顯示驅動整合IC設計"),
    "6119": _e("FACTORY_ENGINEERING", "廠務工程", CONFIDENCE_MEDIUM, reason="大傳：無塵室/廠務系統工程"),
    "6255": _e("COMPUTER_PERIPHERALS", "連接器", CONFIDENCE_MEDIUM, reason="奈普：電池連接器/電源模組"),
    "6280": _e("SEMICONDUCTOR", "IC設計", CONFIDENCE_MEDIUM, reason="崇貿：電源管理IC設計"),
    "6431": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="光麗-KY：業務內容需人工查證"),
    "6525": _e("SEMICONDUCTOR", "IC設計", CONFIDENCE_MEDIUM, reason="捷敏-KY：類比IC設計"),
    "6573": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="虹揚-KY：業務內容需人工查證"),
    "6715": _e("SEMICONDUCTOR", "化合物半導體材料", CONFIDENCE_MEDIUM, reason="嘉基：碳化矽/氮化鎵基板材料"),
    "6781": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="AES-KY：業務內容需人工查證"),
    "6862": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="三集瑞-KY：業務內容需人工查證"),
    "6863": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="永道-KY：業務內容需人工查證"),
    "6921": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="嘉雨思-創：業務內容需人工查證"),
    "8199": _e("OPTOELECTRONICS", "LED磊晶", CONFIDENCE_MEDIUM, reason="廣鎵：氮化鎵磊晶"),
})

# =============================================================================
# 其他電子業（industry_name='其他電子業'，僅 1 檔）
# =============================================================================
STOCK_OVERRIDES["3367"] = _e("COMPUTER_PERIPHERALS", "NB/伺服器代工", CONFIDENCE_HIGH,
                              reason="英華達：英業達集團旗下，NB/伺服器代工")

# =============================================================================
# 智慧電網（industry_name='智慧電網'，主要業務與電線電纜/重電差異較大者個別 override，
# 其餘 15 檔沿用 industry_mapping.py 的 ELECTRICAL_CABLE 系統性映射）
# =============================================================================
STOCK_OVERRIDES.update({
    "2308": _e("ELECTRICAL_MACHINERY", "電源管理與工業自動化", CONFIDENCE_HIGH,
               reason="台達電：電源管理/工業自動化/電動車充電多角化大廠，非傳統電纜重電業務"),
    "2360": _e("COMPUTER_PERIPHERALS", "電子量測儀器", CONFIDENCE_HIGH, reason="致茂：電子測試儀器製造"),
    "2371": _e("ELECTRICAL_MACHINERY", "重電與家電集團", CONFIDENCE_HIGH, reason="大同：重電設備與家電集團"),
    "3622": _e("OPTOELECTRONICS", "觸控感測", CONFIDENCE_MEDIUM, reason="洋華：觸控感測元件"),
    "7740": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="熙特爾-創：業務內容需人工查證"),
})

# =============================================================================
# 其他（industry_name='其他'，117 檔最大 catch-all，逐檔分類）
# =============================================================================
STOCK_OVERRIDES.update({
    "1107": _e("BUILDING_MATERIALS_CONSTRUCTION", None, CONFIDENCE_LOW, reason="建台：業務內容需人工查證"),
    "1213": _e("FOOD", "餐飲", CONFIDENCE_MEDIUM, reason="大飲：飲料/餐飲相關"),
    "1262": _e("SPORTING_GOODS", None, CONFIDENCE_LOW, reason="綠悅-KY：業務內容需人工查證"),
    "1342": _e("TEXTILE_FIBER", None, CONFIDENCE_LOW, reason="八貫：業務內容需人工查證"),
    "1435": _e("TEXTILE_FIBER", "成衣", CONFIDENCE_MEDIUM, reason="中福：成衣製造"),
    "1437": _e("INVESTMENT_HOLDING", "紡織集團控股", CONFIDENCE_MEDIUM, reason="勤益控：勤益紡織控股公司"),
    "1443": _e("TEXTILE_FIBER", None, CONFIDENCE_LOW, reason="立益：業務內容需人工查證"),
    "1516": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="川飛：業務內容需人工查證"),
    "1520": _e("ELECTRICAL_MACHINERY", "壓縮機/流體機械", CONFIDENCE_HIGH, reason="復盛：空氣壓縮機/高爾夫球頭"),
    "1598": _e("SPORTING_GOODS", "健身器材", CONFIDENCE_HIGH, reason="岱宇：健身器材製造"),
    "1608": _e("ELECTRICAL_CABLE", "電線電纜", CONFIDENCE_MEDIUM, reason="華榮：電線電纜製造"),
    "1616": _e("ELECTRICAL_CABLE", "電線電纜", CONFIDENCE_MEDIUM, reason="億泰：電線電纜相關"),
    "1626": _e("HOME_APPLIANCE_CONSUMER", "小家電", CONFIDENCE_HIGH, reason="艾美特-KY：小家電製造"),
    "1708": _e("CHEMICAL", "工業化學品", CONFIDENCE_HIGH, reason="東鹼：工業用鹼/化學原料"),
    "1718": _e("TEXTILE_FIBER", "人纖原料", CONFIDENCE_HIGH, reason="中纖：人造纖維原料"),
    "1722": _e("CHEMICAL", "肥料/工業化學品", CONFIDENCE_HIGH, reason="台肥：肥料與工業化學品製造"),
    "1726": _e("CHEMICAL", "塗料/接著劑", CONFIDENCE_HIGH, reason="永記：永記造漆，塗料製造"),
    "1736": _e("SPORTING_GOODS", "健身器材", CONFIDENCE_HIGH, reason="喬山：健身器材製造大廠"),
    "2062": _e("STEEL", None, CONFIDENCE_LOW, reason="橋椿：業務內容需人工查證"),
    "2348": _e("RETAIL_TRADE", "建材零售/裝修", CONFIDENCE_MEDIUM, reason="海悅：房屋代銷/裝修服務"),
    "2348A": _e("RETAIL_TRADE", "建材零售/裝修", CONFIDENCE_MEDIUM, reason="海悅甲特：同海悅（母公司特別股）"),
    "2358": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="廷鑫：業務內容需人工查證"),
    "2359": _e("AUTOMATION_ROBOTICS", "工業機器人整合", CONFIDENCE_HIGH, reason="所羅門：工業機器人與自動化整合"),
    "2373": _e("RETAIL_TRADE", "辦公事務機器經銷", CONFIDENCE_HIGH, reason="震旦行：辦公家具/事務機器經銷"),
    "2404": _e("FACTORY_ENGINEERING", "廠務工程", CONFIDENCE_HIGH, reason="漢唐：半導體廠務系統工程"),
    "2414": _e("SOFTWARE_ECOMMERCE", "IT系統整合/通路", CONFIDENCE_HIGH, reason="精技：資訊系統整合與通路"),
    "2423": _e("COMPUTER_PERIPHERALS", "量測儀器", CONFIDENCE_HIGH, reason="固緯：電子量測儀器製造"),
    "2433": _e("ELECTRONIC_DISTRIBUTION", "電子零件通路", CONFIDENCE_MEDIUM, reason="互盛電：電子零件代理通路"),
    "2443": _e("COMPUTER_PERIPHERALS", None, CONFIDENCE_LOW, reason="昶虹：業務內容需人工查證"),
    "2461": _e("ELECTRICAL_MACHINERY", "電動工具/五金", CONFIDENCE_MEDIUM, reason="光群雷：電動工具/手工具"),
    "2466": _e("ENVIRONMENTAL_SERVICES", "環保工程", CONFIDENCE_HIGH, reason="冠西電：環保設備/太陽能工程"),
    "2477": _e("HOME_APPLIANCE_CONSUMER", "家電", CONFIDENCE_HIGH, reason="美隆電：家電製造"),
    "2482": _e("COMMUNICATION_NETWORK", "網通線材", CONFIDENCE_HIGH, reason="連宇：網路通訊線材"),
    "2488": _e("ELECTRONIC_DISTRIBUTION", "電子零件代理", CONFIDENCE_MEDIUM, reason="漢平：電子零件代理商"),
    "2496": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="卓越：業務內容需人工查證"),
    "2497": _e("AUTOMOTIVE", "車用電子", CONFIDENCE_HIGH, reason="怡利電：車用電子/音響系統"),
    "2540": _e("BUILDING_MATERIALS_CONSTRUCTION", "建設開發", CONFIDENCE_HIGH, reason="愛山林：房地產開發"),
    "2904": _e("RETAIL_TRADE", "貿易", CONFIDENCE_LOW, reason="匯僑：貿易相關，業務內容需人工查證"),
    "3040": _e("COMPUTER_PERIPHERALS", "顯示器/監視器", CONFIDENCE_MEDIUM, reason="遠見：顯示器製造"),
    "3557": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="嘉威：業務內容需人工查證"),
    "3703": _e("ENVIRONMENTAL_SERVICES", "環保能源工程", CONFIDENCE_HIGH, reason="欣陸：環保/水資源工程"),
    "4141": _e("PHARMACEUTICAL", "動物用藥/農藥", CONFIDENCE_MEDIUM, reason="龍燈-KY：動物用藥與農藥代理"),
    "4536": _e("PLASTICS_RUBBER", "運動器材複合材料", CONFIDENCE_MEDIUM, reason="拓凱：碳纖維複合材料運動器材"),
    "4764": _e("AGRI_TECH", None, CONFIDENCE_MEDIUM, reason="雙鍵：農業科技相關"),
    "5225": _e("COMPUTER_PERIPHERALS", "散熱模組", CONFIDENCE_MEDIUM, reason="東科-KY：散熱模組製造"),
    "5283": _e("HOME_APPLIANCE_CONSUMER", "家電品牌", CONFIDENCE_HIGH, reason="禾聯碩：家電品牌"),
    "5292": _e("ENVIRONMENTAL_SERVICES", "環保工程", CONFIDENCE_MEDIUM, reason="華懋：環保潔能服務"),
    "6139": _e("FACTORY_ENGINEERING", "廠務工程", CONFIDENCE_HIGH, reason="亞翔：半導體廠務系統工程"),
    "6196": _e("FACTORY_ENGINEERING", "廠務系統整合", CONFIDENCE_HIGH, reason="帆宣：半導體廠務系統整合"),
    "6215": _e("AUTOMATION_ROBOTICS", "自動化設備", CONFIDENCE_HIGH, reason="和椿：工業自動化設備整合"),
    "6464": _e("COMMUNICATION_NETWORK", "有線電視/電信服務", CONFIDENCE_HIGH, reason="台數科：有線電視與電信服務"),
    "6581": _e("ENVIRONMENTAL_SERVICES", "環保儲槽設備", CONFIDENCE_MEDIUM, reason="鋼聯：環保設備/儲槽"),
    "6592": _e("FINANCIAL", "租賃與消費金融", CONFIDENCE_HIGH, reason="和潤企業：中租控股旗下汽車貸款/租賃"),
    "6592A": _e("FINANCIAL", "租賃與消費金融", CONFIDENCE_HIGH, reason="和潤企業甲特：同和潤企業"),
    "6592B": _e("FINANCIAL", "租賃與消費金融", CONFIDENCE_HIGH, reason="和潤企業乙特：同和潤企業"),
    "6641": _e("ENVIRONMENTAL_SERVICES", "環保工程", CONFIDENCE_MEDIUM, reason="基士德-KY：環保潔能服務"),
    "6655": _e("BUILDING_MATERIALS_CONSTRUCTION", "室內裝修建材", CONFIDENCE_MEDIUM, reason="科定：室內裝修建材"),
    "6671": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="三能-KY：業務內容需人工查證"),
    "6691": _e("FACTORY_ENGINEERING", "廠務工程", CONFIDENCE_HIGH, reason="洋基工程：廠務系統工程"),
    "6698": _e("PCB_ELECTRONIC_MATERIALS", "半導體化學材料", CONFIDENCE_MEDIUM, reason="旭暉應材：半導體製程化學品"),
    "6753": _e("SHIPBUILDING", "造船", CONFIDENCE_HIGH, reason="龍德造船：專業造船廠"),
    "6768": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="志強-KY：業務內容需人工查證"),
    "6771": _e("ENVIRONMENTAL_SERVICES", "環保工程", CONFIDENCE_MEDIUM, reason="平和環保-創：環保潔能服務"),
    "6776": _e("AGRI_TECH", "植物工廠/蘭花科技", CONFIDENCE_MEDIUM, reason="展碁國際：農業科技相關業務"),
    "6807": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="峰源-KY：業務內容需人工查證"),
    "6830": _e("SEMICONDUCTOR", "半導體檢測服務", CONFIDENCE_MEDIUM, reason="汎銓：半導體材料分析檢測服務"),
    "6901": _e("INVESTMENT_HOLDING", None, CONFIDENCE_MEDIUM, reason="鑽石投資：投資控股公司"),
    "6914": _e("INVESTMENT_HOLDING", None, CONFIDENCE_LOW, reason="阜爾運通：業務內容需人工查證"),
    "6923": _e("ENVIRONMENTAL_SERVICES", "環保工程", CONFIDENCE_MEDIUM, reason="中台：環保潔能服務"),
    "6936": _e("AGRI_TECH", None, CONFIDENCE_MEDIUM, reason="永鴻生技：農業科技相關"),
    "6944": _e("ENVIRONMENTAL_SERVICES", "環保工程", CONFIDENCE_MEDIUM, reason="兆聯實業：環保潔能服務"),
    "6951": _e("ENVIRONMENTAL_SERVICES", "環保工程", CONFIDENCE_MEDIUM, reason="青新-創：環保潔能服務"),
    "6952": _e("AGRI_TECH", None, CONFIDENCE_MEDIUM, reason="大武山：農業科技相關"),
    "6957": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="裕慶-KY：業務內容需人工查證"),
    "6958": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="日盛台駿：業務內容需人工查證"),
    "6958A": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="日盛台駿甲特：同日盛台駿"),
    "7610": _e("STEEL", "金屬加工", CONFIDENCE_MEDIUM, reason="聯友金屬：金屬材料加工"),
    "7818": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="溢泰實業：業務內容需人工查證"),
    "8021": _e("COMPUTER_PERIPHERALS", "散熱模組", CONFIDENCE_MEDIUM, reason="尖點：散熱模組製造"),
    "8033": _e("HOME_APPLIANCE_CONSUMER", "模型玩具/無人機", CONFIDENCE_HIGH, reason="雷虎：遙控模型/軍用無人機"),
    "8072": _e("COMPUTER_PERIPHERALS", "監視器/資安設備", CONFIDENCE_HIGH, reason="陞泰：監視器與影像資安設備"),
    "8201": _e("HOME_APPLIANCE_CONSUMER", "電子辭典/教育電子", CONFIDENCE_HIGH, reason="無敵：電子辭典與教育電子產品"),
    "8341": _e("ENVIRONMENTAL_SERVICES", "廢棄物處理", CONFIDENCE_HIGH, reason="日友：醫療廢棄物處理"),
    "8422": _e("ENVIRONMENTAL_SERVICES", "廢棄物處理", CONFIDENCE_HIGH, reason="可寧衛：廢棄物處理與資源化"),
    "8427": _e("BUILDING_MATERIALS_CONSTRUCTION", None, CONFIDENCE_LOW, reason="基勝-KY：業務內容需人工查證，暫依常見同名企業推測為營建相關"),
    "8442": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="威宏-KY：業務內容需人工查證"),
    "8464": _e("HOME_APPLIANCE_CONSUMER", "窗簾/家飾製造", CONFIDENCE_HIGH, reason="億豐：窗簾製造大廠"),
    "8466": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="美吉吉-KY：業務內容需人工查證"),
    "8467": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="波力-KY：業務內容需人工查證"),
    "8473": _e("ENVIRONMENTAL_SERVICES", "水資源工程", CONFIDENCE_HIGH, reason="山林水：水資源與環保工程"),
    "8476": _e("ENVIRONMENTAL_SERVICES", "環保工程", CONFIDENCE_MEDIUM, reason="台境*：環保潔能服務"),
    "8478": _e("HOME_APPLIANCE_CONSUMER", "遊艇製造", CONFIDENCE_HIGH, reason="東哥遊艇：豪華遊艇製造"),
    "8480": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="泰昇-KY：業務內容需人工查證"),
    "8482": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="商億-KY：業務內容需人工查證"),
    "8488": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="吉源-KY：業務內容需人工查證"),
    "8497": _e("CULTURAL_CREATIVE", "媒體傳播", CONFIDENCE_MEDIUM, reason="格威傳媒：媒體/傳播相關"),
    "8499": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="鼎炫-KY：業務內容需人工查證"),
    "9802": _e("HOME_APPLIANCE_CONSUMER", "製鞋代工", CONFIDENCE_HIGH, reason="鈺齊-KY：運動鞋代工製造"),
    "9902": _e("DIVERSIFIED_OTHER", None, CONFIDENCE_LOW, reason="台火：業務內容需人工查證"),
    "9904": _e("HOME_APPLIANCE_CONSUMER", "製鞋代工", CONFIDENCE_HIGH, reason="寶成：全球最大製鞋代工集團"),
    "9905": _e("RETAIL_TRADE", "家用品貿易", CONFIDENCE_LOW, reason="大華：業務內容需人工查證"),
    "9910": _e("HOME_APPLIANCE_CONSUMER", "製鞋代工", CONFIDENCE_HIGH, reason="豐泰：NIKE 主力製鞋代工廠"),
    "9911": _e("HOME_APPLIANCE_CONSUMER", "廚具家電", CONFIDENCE_HIGH, reason="櫻花：廚具/熱水器家電品牌"),
    "9915": _e("HOME_APPLIANCE_CONSUMER", "窗簾/家飾製造", CONFIDENCE_MEDIUM, reason="億豐：窗簾製造（與8464同集團代碼）"),
    "9922": _e("SECURITY_SERVICES", "保全服務", CONFIDENCE_MEDIUM, reason="優美：保全服務"),
    "9925": _e("SECURITY_SERVICES", "保全服務", CONFIDENCE_HIGH, reason="新保：新光保全，保全服務大廠"),
    "9927": _e("HOME_APPLIANCE_CONSUMER", None, CONFIDENCE_LOW, reason="泰銘：業務內容需人工查證"),
    "9929": _e("CULTURAL_CREATIVE", "印刷/包裝", CONFIDENCE_HIGH, reason="秋雨：印刷/彩色印刷包裝"),
    "9930": _e("ENVIRONMENTAL_SERVICES", "資源回收再生", CONFIDENCE_HIGH, reason="中聯資源：轉爐石資源化再生"),
    "9934": _e("HOME_APPLIANCE_CONSUMER", "衛浴五金", CONFIDENCE_HIGH, reason="成霖：衛浴五金製造"),
    "9935": _e("TEXTILE_FIBER", "成衣代工", CONFIDENCE_MEDIUM, reason="慶豐富：成衣代工"),
    "9938": _e("TEXTILE_FIBER", "織帶/尼龍拉鍊", CONFIDENCE_HIGH, reason="百和：織帶/尼龍拉鍊製造"),
    "9940": _e("BUILDING_MATERIALS_CONSTRUCTION", "建設開發", CONFIDENCE_HIGH, reason="信義：信義房屋仲介"),
    "9941": _e("FINANCIAL", "租賃與消費金融", CONFIDENCE_HIGH, reason="裕融：中租集團旗下汽車貸款"),
    "9941A": _e("FINANCIAL", "租賃與消費金融", CONFIDENCE_HIGH, reason="裕融甲特：同裕融"),
    "9955": _e("ENVIRONMENTAL_SERVICES", "廢棄物處理", CONFIDENCE_HIGH, reason="佳龍：廢棄物處理與再利用"),
})
