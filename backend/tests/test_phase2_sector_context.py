"""Phase 2 §D/§E：hierarchical canonical sector context 測試。"""
from app.signals.phase2 import sector_context as sc


def _frame(rs_values: dict) -> dict:
    return {sid: {"rs_market_percentile_20d": rs} for sid, rs in rs_values.items()}


def test_is_mapping_usable():
    assert sc.is_mapping_usable("HIGH") is True
    assert sc.is_mapping_usable("MEDIUM") is True
    assert sc.is_mapping_usable("LOW") is False
    assert sc.is_mapping_usable(None) is False


def test_single_stock_sector_is_unusable_not_fail():
    """漢翔案例：primary_sector 樣本數=1，不可產生 100 percentile 假訊號，
    也不可判 FAIL——peer_scope 落到 MARKET_ONLY，quality=UNUSABLE。"""
    frame = _frame({"2634": 99.0, "2330": 50.0, "2317": 40.0, "1101": 30.0, "1301": 20.0, "2454": 10.0})
    classifications = {
        "2634": {"primary_sector": "AEROSPACE_DEFENSE", "sub_sector": "航空器製造", "confidence": "HIGH"},
        "2330": {"primary_sector": "SEMICONDUCTOR", "sub_sector": "晶圓代工", "confidence": "HIGH"},
        "2317": {"primary_sector": "SEMICONDUCTOR", "sub_sector": "晶圓代工", "confidence": "HIGH"},
        "1101": {"primary_sector": "SEMICONDUCTOR", "sub_sector": "晶圓代工", "confidence": "HIGH"},
        "1301": {"primary_sector": "SEMICONDUCTOR", "sub_sector": "晶圓代工", "confidence": "HIGH"},
        "2454": {"primary_sector": "SEMICONDUCTOR", "sub_sector": "晶圓代工", "confidence": "HIGH"},
    }
    out = sc.compute_sector_context(frame, classifications, min_peer_sample=5)
    hanxiang = out["2634"]
    assert hanxiang["primary_sector_stock_count"] == 1
    assert hanxiang["peer_scope_used"] == sc.PEER_SCOPE_MARKET_ONLY
    assert hanxiang["sector_context_quality"] == sc.QUALITY_UNUSABLE
    assert hanxiang["peer_rs_percentile_20d"] is None
    assert hanxiang["sector_strength_percentile_20d"] is None


def test_sub_sector_used_when_sample_sufficient():
    ids = [f"S{i}" for i in range(6)]
    frame = _frame({sid: float(i * 10) for i, sid in enumerate(ids)})
    classifications = {
        sid: {"primary_sector": "PCB_ELECTRONIC_MATERIALS", "sub_sector": "FCCL", "confidence": "HIGH"}
        for sid in ids
    }
    out = sc.compute_sector_context(frame, classifications, min_peer_sample=5)
    for sid in ids:
        assert out[sid]["peer_scope_used"] == sc.PEER_SCOPE_SUB_SECTOR
        assert out[sid]["sub_sector_stock_count"] == 6
        assert out[sid]["sector_context_quality"] == sc.QUALITY_HIGH
    # 最強的那檔 peer_rs 應該是 100
    assert out["S5"]["peer_rs_percentile_20d"] == 100.0
    assert out["S0"]["peer_rs_percentile_20d"] == 0.0


def test_falls_back_to_primary_sector_when_sub_sector_too_small():
    """台虹案例雛型：sub_sector 樣本不足 5，但 primary_sector 夠 → fallback PRIMARY_SECTOR。"""
    ids = [f"S{i}" for i in range(6)]
    frame = _frame({sid: float(i * 10) for i, sid in enumerate(ids)})
    classifications = {}
    for i, sid in enumerate(ids):
        # 前 2 檔 sub_sector=A（樣本不足 5），其餘 sub_sector=B
        classifications[sid] = {
            "primary_sector": "PCB_ELECTRONIC_MATERIALS",
            "sub_sector": "A" if i < 2 else "B",
            "confidence": "HIGH",
        }
    out = sc.compute_sector_context(frame, classifications, min_peer_sample=5)
    assert out["S0"]["peer_scope_used"] == sc.PEER_SCOPE_PRIMARY_SECTOR
    assert out["S0"]["sub_sector_stock_count"] == 2
    assert out["S0"]["primary_sector_stock_count"] == 6
    assert out["S0"]["sector_context_quality"] == sc.QUALITY_MEDIUM


def test_low_confidence_mapping_is_not_usable_for_grouping():
    """confidence=LOW 的股票不進分組，也不能借用別人的分組結果。"""
    ids = [f"S{i}" for i in range(6)]
    frame = _frame({sid: float(i * 10) for i, sid in enumerate(ids)})
    classifications = {
        sid: {"primary_sector": "PCB_ELECTRONIC_MATERIALS", "sub_sector": "FCCL", "confidence": "HIGH"}
        for sid in ids
    }
    classifications["S0"] = {"primary_sector": "PCB_ELECTRONIC_MATERIALS", "sub_sector": "FCCL", "confidence": "LOW"}
    out = sc.compute_sector_context(frame, classifications, min_peer_sample=5)
    assert out["S0"]["canonical_mapping_usable"] is False
    assert out["S0"]["peer_scope_used"] == sc.PEER_SCOPE_MARKET_ONLY
    # 其餘 5 檔（S1~S5）樣本數應該是 5（不含 S0）
    assert out["S1"]["sub_sector_stock_count"] == 5


def test_sector_strength_and_peer_rs_are_independent():
    """弱產業 + 強個股（台虹雛型）：sector_strength 低，但 peer_rs 仍可以是 100。
    需要 >= min_peer_sample 個 sector 才能對「sector 強弱」做 percentile 排名，
    這裡用 5 個 sector（WEAK 最弱、STRONG 最強、另外 3 個居中）模擬真實 universe。"""
    weak_sector_ids = [f"W{i}" for i in range(6)]
    strong_sector_ids = [f"T{i}" for i in range(6)]
    frame_values = {
        **{sid: 20.0 for sid in weak_sector_ids[:-1]}, weak_sector_ids[-1]: 90.0,
        **{sid: 80.0 for sid in strong_sector_ids},
    }
    classifications = {}
    for sid in weak_sector_ids:
        classifications[sid] = {"primary_sector": "WEAK", "sub_sector": None, "confidence": "HIGH"}
    for sid in strong_sector_ids:
        classifications[sid] = {"primary_sector": "STRONG", "sub_sector": None, "confidence": "HIGH"}
    # 另外 3 個居中強度的 sector，讓 universe 有 5 個 sector 可排名
    for label, val in (("MID1", 40.0), ("MID2", 50.0), ("MID3", 60.0)):
        for i in range(6):
            sid = f"{label}_{i}"
            frame_values[sid] = val
            classifications[sid] = {"primary_sector": label, "sub_sector": None, "confidence": "HIGH"}

    frame = _frame(frame_values)
    out = sc.compute_sector_context(frame, classifications, min_peer_sample=5)
    strong_stock_in_weak_sector = out[weak_sector_ids[-1]]
    assert strong_stock_in_weak_sector["peer_rs_percentile_20d"] == 100.0  # peer 裡最強
    assert strong_stock_in_weak_sector["sector_strength_percentile_20d"] == 0.0  # 但整個 sector 是最弱的
