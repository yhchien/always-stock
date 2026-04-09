"""
Key broker branch category mapping.

Categories:
  day_trade:  當沖
  next_day:   隔日沖
  short_term: 短線
  swing:      波段

Names use the display format (with dash for branches).
BSR data uses no-dash format — matching is done via normalize().
"""

BROKER_CATEGORIES: dict[str, list[str]] = {
    "day_trade": [
        "新加坡瑞銀", "摩根大通", "美林", "元大", "元富", "台新", "亞東", "新光", "群益",
        "元大-中壢", "永全-八德", "兆豐-南門", "凱基-大里", "凱基-台北", "凱基-屏東",
        "凱基-員林", "凱基-站前", "富邦-嘉義", "華南-中正", "聯邦-富強",
    ],
    "next_day": [
        "美商高盛", "港商野村", "新加坡瑞銀", "摩根大通", "元大", "元富",
        "元大-土城永寧", "元大-太平", "元大-成功", "元大-竹科", "元大-虎尾",
        "元大-鹿港", "元大-新竹", "元大-彰化", "日盛-忠孝", "永豐金-虎尾",
        "永豐金-桃園", "兆豐-中港", "兆豐-北高雄", "兆豐-虎尾",
        "兆豐-南京", "兆豐-復興", "凱基-士林", "凱基-台北", "凱基-市政",
        "凱基-板橋", "凱基-屏東", "富邦-台中", "富邦-台南", "富邦-虎尾",
        "富邦-建國", "統一-仁愛", "統一-松江", "統一-南京", "華南-竹北",
        "華南-長虹", "華南-嘉義", "群益-內湖", "群益-海山", "群益-館前",
    ],
    "short_term": [
        "新加坡瑞銀", "摩根大通", "玉山", "康和", "台企銀-嘉義", "台新-台中",
        "台新-建北", "台新-高雄", "玉山-台南", "兆豐-大安", "兆豐-忠孝",
        "合庫-台中", "國泰-博愛", "國票-長城", "凱基-市政", "凱基-桃園",
        "富邦-建國", "富邦-員林", "統一-敦南",
    ],
    "swing": [
        "台灣匯立", "台灣摩根", "港商野村", "瑞士信貸", "摩根大通", "元富",
        "台新", "宏遠", "國泰綜合", "富邦", "華南永昌", "新光", "群益", "福邦",
        "中國信託",
        "元大-敦化", "日盛-龍潭", "台企銀-桃園", "台新-高雄", "兆豐-忠孝",
        "兆豐-復興", "國票-和平", "國票-長城", "凱基-大安", "凱基-中港",
        "富邦-南屯", "統一-敦南",
    ],
}

CATEGORY_LABELS: dict[str, str] = {
    "day_trade": "當沖",
    "next_day": "隔日沖",
    "short_term": "短線",
    "swing": "波段",
}

# Known aliases: display_name -> BSR name(s) that don't follow dash-removal rule.
_ALIASES: dict[str, list[str]] = {
    "新加坡瑞銀": ["瑞銀"],
    "台企銀-嘉義": ["企銀嘉義"],
    "台企銀-桃園": ["企銀桃園"],
}


def normalize(name: str) -> str:
    """Remove dash and whitespace for matching."""
    return name.replace("-", "").replace(" ", "").replace("\u3000", "")


def _build_lookup() -> dict[str, set[str]]:
    """Build {normalized_bsr_name -> set of categories}."""
    lookup: dict[str, set[str]] = {}
    for cat, brokers in BROKER_CATEGORIES.items():
        for name in brokers:
            # primary key: dash-removed
            lookup.setdefault(normalize(name), set()).add(cat)
            # aliases
            for alias in _ALIASES.get(name, []):
                lookup.setdefault(normalize(alias), set()).add(cat)
    return lookup


BROKER_LOOKUP = _build_lookup()


def get_categories(bsr_broker_name: str) -> set[str]:
    """Return categories for a broker name from BSR data."""
    return BROKER_LOOKUP.get(normalize(bsr_broker_name), set())
