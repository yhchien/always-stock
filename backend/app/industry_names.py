"""Shared helpers for canonical industry names."""

from __future__ import annotations


CANONICAL_INDUSTRY_NAME_MAP = {
    "半導體業": "半導體",
    "汽車工業": "汽車",
    "造紙工業": "造紙",
    "紡織纖維": "紡織",
    "電腦及週邊設備業": "電腦及週邊設備",
    "通信網路業": "通信網路",
    "創新版股票": "創新板股票",
    "金融保險": "金融",
    "塑膠工業": "石化及塑橡膠",
    "航運業": "交通運輸及航運",
    "其他電子業": "其他",
    "電子通路業": "其他",
    "電子零組件業": "其他",
    "電器電纜": "電機機械",
    "運動休閒": "休閒娛樂",
    "運動科技": "休閒娛樂",
    "生技醫療業": "醫療器材",
}


def normalize_industry_name(industry_name: str | None) -> str | None:
    if industry_name is None:
        return None
    return CANONICAL_INDUSTRY_NAME_MAP.get(industry_name, industry_name)
