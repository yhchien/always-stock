"""M23 slice 4：exclusions / group_stocks helper unit tests."""
import pytest

from app.signals import exclusions
from app.signals.exclusions import (
    find_group_for_stock,
    get_group_leader,
    get_group_members,
    is_blacklisted,
    is_etf,
    is_financial,
    load_group_stocks,
    should_exclude,
)


@pytest.fixture(autouse=True)
def _reset_group_stocks_cache():
    """每個測試獨立讀檔；避免 monkeypatch 殘留影響後續測試。"""
    exclusions._GROUP_STOCKS_CACHE = None
    yield
    exclusions._GROUP_STOCKS_CACHE = None


# ---------- ETF / 金融 / 黑名單規則 ----------


def test_is_etf_matches_id_prefix():
    assert is_etf("0050") is True       # 元大台灣 50
    assert is_etf("00878") is True      # 國泰永續高股息
    assert is_etf("006208") is True     # 富邦台 50


def test_is_etf_rejects_normal_stocks():
    assert is_etf("2330") is False
    assert is_etf("2317") is False
    assert is_etf("1101") is False


def test_is_etf_matches_name_keyword():
    # ID 不是 00 開頭但名字含 ETF 關鍵字
    assert is_etf("9999", "群益某 ETF") is True
    assert is_etf("9999", "美國指數型基金") is True
    assert is_etf("9999", "指數股票型 - 高科技") is True


def test_is_etf_normal_name_not_misjudged():
    assert is_etf("2330", "台積電") is False


def test_is_etf_id_overrides_name():
    # 名字沒 ETF 字樣但 ID 是 00 開頭，仍視為 ETF
    assert is_etf("00878", "國泰永續高股息") is True


def test_is_financial_matches_industry():
    assert is_financial("金融保險業") is True
    assert is_financial("銀行業") is True
    assert is_financial("保險業") is True
    assert is_financial("證券業") is True


def test_is_financial_rejects_non_financial():
    assert is_financial("半導體業") is False
    assert is_financial("電子零組件業") is False


def test_is_financial_handles_none():
    assert is_financial(None) is False
    assert is_financial("") is False


def test_is_blacklisted_default_empty():
    assert is_blacklisted("2330") is False
    assert is_blacklisted("9999") is False


def test_is_blacklisted_with_monkeypatched_set(monkeypatch):
    monkeypatch.setattr(exclusions, "EXCLUSION_BLACKLIST", {"9999", "8888"})
    assert is_blacklisted("9999") is True
    assert is_blacklisted("8888") is True
    assert is_blacklisted("2330") is False


# ---------- should_exclude 整合 ----------


def test_should_exclude_etf():
    assert should_exclude("0050", "元大台灣 50", "ETF") is False


def test_should_exclude_financial():
    assert should_exclude("2880", "華南金", "金融保險業") is False


def test_should_exclude_normal_stock_returns_false():
    assert should_exclude("2330", "台積電", "半導體業") is False
    assert should_exclude("2317", "鴻海", "電子零組件業") is False


def test_should_exclude_blacklisted_with_monkeypatch(monkeypatch):
    monkeypatch.setattr(exclusions, "EXCLUSION_BLACKLIST", {"7777"})
    assert should_exclude("7777", "假設股", "半導體業") is True


# ---------- group_stocks.json 載入 ----------


def test_load_group_stocks_excludes_meta_key():
    groups = load_group_stocks()
    assert "_meta" not in groups
    # 第一版至少含這 5 個集團（slice 4 hardcoded）
    assert "鴻海集團" in groups
    assert "台塑集團" in groups
    assert "國巨集團" in groups
    assert "聯電集團" in groups
    assert "聯發科集團" in groups


def test_load_group_stocks_each_entry_has_leader_and_members():
    groups = load_group_stocks()
    for name, info in groups.items():
        assert "leader" in info, f"{name} missing leader"
        assert "members" in info, f"{name} missing members"
        assert info["leader"] in info["members"], (
            f"{name} leader {info['leader']} not in members"
        )


def test_find_group_for_stock_returns_correct_group():
    assert find_group_for_stock("2317") == "鴻海集團"
    assert find_group_for_stock("2354") == "鴻海集團"   # 鴻準
    assert find_group_for_stock("1301") == "台塑集團"
    assert find_group_for_stock("2454") == "聯發科集團"


def test_find_group_for_stock_returns_none_when_not_in_any_group():
    assert find_group_for_stock("9999") is None
    assert find_group_for_stock("2330") is None  # 台積電目前未列入手動白名單


def test_get_group_members_returns_list():
    members = get_group_members("鴻海集團")
    assert "2317" in members
    assert "2354" in members
    assert isinstance(members, list)


def test_get_group_members_returns_empty_for_unknown_group():
    assert get_group_members("不存在的集團") == []


def test_get_group_leader_returns_id():
    assert get_group_leader("鴻海集團") == "2317"
    assert get_group_leader("台塑集團") == "1301"
    assert get_group_leader("聯發科集團") == "2454"


def test_get_group_leader_returns_none_for_unknown_group():
    assert get_group_leader("不存在的集團") is None
