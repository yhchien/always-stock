"""commands.py parse() 測試：所有指令格式正確解析；錯誤輸入回 unknown/error。"""
import pytest

from app.telegram import commands


# ── help / register ─────────────────────────────────────────────────────────


def test_parse_help():
    assert commands.parse("list help").kind == "help"


def test_parse_help_case_insensitive():
    assert commands.parse("LIST HELP").kind == "help"
    assert commands.parse("List Help").kind == "help"


def test_parse_register_with_password():
    parsed = commands.parse("list register secret-pass")
    assert parsed.kind == "register"
    assert parsed.password == "secret-pass"


def test_parse_register_missing_password():
    parsed = commands.parse("list register")
    assert parsed.kind == "register"
    assert parsed.error is not None


def test_parse_register_strips_password():
    parsed = commands.parse("list register   pass-with-spaces  ")
    assert parsed.password == "pass-with-spaces"


# ── show ────────────────────────────────────────────────────────────────────


def test_parse_show():
    assert commands.parse("list show").kind == "show"


# ── add ─────────────────────────────────────────────────────────────────────


def test_parse_add_single():
    parsed = commands.parse("list add 2330")
    assert parsed.kind == "add"
    assert parsed.stock_ids == ["2330"]


def test_parse_add_multiple():
    parsed = commands.parse("list add 2330, 2317, 2454")
    assert parsed.kind == "add"
    assert parsed.stock_ids == ["2330", "2317", "2454"]


def test_parse_add_no_args():
    parsed = commands.parse("list add")
    assert parsed.kind == "add"
    assert parsed.error is not None


# ── delete ──────────────────────────────────────────────────────────────────


def test_parse_delete_single():
    parsed = commands.parse("list delete 2330")
    assert parsed.kind == "delete"
    assert parsed.stock_ids == ["2330"]


def test_parse_delete_multiple():
    parsed = commands.parse("list delete 2330, 2317")
    assert parsed.kind == "delete"
    assert parsed.stock_ids == ["2330", "2317"]


def test_parse_delete_no_args():
    parsed = commands.parse("list delete")
    assert parsed.kind == "delete"
    assert parsed.error is not None


# ── watch detail ────────────────────────────────────────────────────────────


def test_parse_watch_detail():
    parsed = commands.parse("list watch 2330 detail")
    assert parsed.kind == "watch_detail"
    assert parsed.stock_ids == ["2330"]


def test_parse_watch_detail_missing_keyword():
    parsed = commands.parse("list watch 2330")
    assert parsed.kind == "watch_detail"
    assert parsed.error is not None


def test_parse_watch_detail_missing_stock_id():
    # `list watch detail` 沒帶代號（detail 被當成 stock_id，再加上沒第二個 token）
    parsed = commands.parse("list watch detail")
    assert parsed.kind == "watch_detail"
    assert parsed.error is not None


# ── run / run all ───────────────────────────────────────────────────────────


def test_parse_run_single():
    parsed = commands.parse("list run 2330")
    assert parsed.kind == "run_single"
    assert parsed.stock_ids == ["2330"]


def test_parse_run_all():
    parsed = commands.parse("list run all")
    assert parsed.kind == "run_all"


def test_parse_run_all_case_insensitive():
    parsed = commands.parse("list run ALL")
    assert parsed.kind == "run_all"


def test_parse_run_no_args():
    parsed = commands.parse("list run")
    assert parsed.kind == "unknown"
    assert parsed.error is not None


# ── admin ───────────────────────────────────────────────────────────────────


def test_parse_admin_chats():
    parsed = commands.parse("list admin chats")
    assert parsed.kind == "admin_chats"


def test_parse_admin_show_with_chat_id():
    parsed = commands.parse("list admin show 12345")
    assert parsed.kind == "admin_show"
    assert parsed.target_chat_id == 12345


def test_parse_admin_show_with_negative_chat_id():
    """supergroup chat_id 為負值，需要支援"""
    parsed = commands.parse("list admin show -1001234567890")
    assert parsed.kind == "admin_show"
    assert parsed.target_chat_id == -1001234567890


def test_parse_admin_show_missing_chat_id():
    parsed = commands.parse("list admin show")
    assert parsed.kind == "admin_show"
    assert parsed.error is not None


def test_parse_admin_show_invalid_chat_id():
    parsed = commands.parse("list admin show notanumber")
    assert parsed.kind == "admin_show"
    assert parsed.error is not None


def test_parse_admin_empty_args_disguised_as_unknown():
    """`list admin` 不應洩漏 admin 存在，回 unknown"""
    parsed = commands.parse("list admin")
    assert parsed.kind == "unknown"


def test_parse_admin_unknown_subcommand_disguised():
    parsed = commands.parse("list admin foobar")
    assert parsed.kind == "unknown"


# ── unknown / 邊界 ──────────────────────────────────────────────────────────


def test_parse_unknown_subcommand():
    parsed = commands.parse("list whatever")
    assert parsed.kind == "unknown"


def test_parse_not_list_prefix():
    parsed = commands.parse("hello world")
    assert parsed.kind == "unknown"


def test_parse_empty():
    parsed = commands.parse("")
    assert parsed.kind == "unknown"


def test_parse_whitespace_only():
    parsed = commands.parse("   ")
    assert parsed.kind == "unknown"
