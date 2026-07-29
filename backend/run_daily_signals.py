"""
M23 每日異常訊號清單排程入口（spec §11.6）

GitHub Actions cron 在台北 19:00（UTC 11:00）觸發；
inline 同步跑 pipeline 後 exit。

用法：
    # 由 cron 觸發（target_date 依台北 19:00 分界自動解析）
    python run_daily_signals.py

    # 手動指定日期（YYYY-MM-DD）
    python run_daily_signals.py 2026-04-25

Exit code（spec §11.6）：
    0 = ok
    1 = no_data（DB 無候選資料 / target date 無交易資料）
    2 = llm_error（OpenAI 失敗、prompt 缺檔等）
    3 = db_error（DB 連線 / commit 失敗等其他例外）
"""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent

# 確保 PYTHONPATH 找得到 app.* — 與 run_finmind_etl_sdk.py 一致
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


EXIT_OK = 0
EXIT_NO_DATA = 1
EXIT_LLM_ERROR = 2
EXIT_DB_ERROR = 3
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
SIGNALS_SAME_DAY_READY_TIME = time(hour=19, minute=0)


def _resolve_target_date_from_now() -> date:
    """依台北 19:00 分界決定預設 target date。

    規則與 API 的「當日訊號何時可用」一致：
      - 19:00 後：使用今天
      - 19:00 前：使用昨天

    這樣 cron 在 19:00 盤後跑時會產生當日 snapshot；
    若管理者在 19:00 前手動觸發且未帶日期，則仍保守使用昨天。
    """
    now_tpe = datetime.now(TAIPEI_TZ)
    if now_tpe.time() >= SIGNALS_SAME_DAY_READY_TIME:
        return now_tpe.date()
    return now_tpe.date() - timedelta(days=1)


def _parse_target_date_from_argv(argv: list) -> date:
    if len(argv) > 1 and argv[1].strip():
        return date.fromisoformat(argv[1].strip())
    return _resolve_target_date_from_now()


def _classify_exit_code(exc: BaseException) -> int:
    """把 pipeline 例外映射到 exit code。

    分類規則：
      - ValueError 訊息含 "no candidate" / "no data" / "no trade" → no_data (1)
      - 訊息含 "openai" / "llm" / "prompt" → llm_error (2)
      - 其他全部 → db_error (3)（含 SQLAlchemy / network / 未知）
    """
    msg = str(exc).lower()
    if isinstance(exc, ValueError) and any(
        keyword in msg for keyword in ("no candidate", "no data", "no trade")
    ):
        return EXIT_NO_DATA
    if any(keyword in msg for keyword in ("openai", "llm", "prompt")):
        return EXIT_LLM_ERROR
    return EXIT_DB_ERROR


def main(argv: list) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        target_date = _parse_target_date_from_argv(argv)
    except ValueError as exc:
        logger.error("Invalid target_date argv: %s", exc)
        return EXIT_DB_ERROR

    logger.info("Daily signals pipeline start: target_date=%s", target_date)

    try:
        from app.database import SessionLocal, engine
        from app.models import SignalGenerationJob
        from app.observation_schema import ensure_observation_tables
        from app.signals.pipeline import run_signal_pipeline_sync
    except Exception:
        logger.exception("Failed to import pipeline modules")
        return EXIT_DB_ERROR

    job_id = str(uuid.uuid4())

    try:
        ensure_observation_tables(engine)
    except Exception:
        logger.exception("Failed to ensure P4 observation lifecycle tables")
        return EXIT_DB_ERROR

    # 建 SignalGenerationJob（triggered_by="cron"）
    try:
        with SessionLocal() as db:
            db.add(
                SignalGenerationJob(
                    job_id=job_id,
                    snapshot_date=target_date,
                    triggered_by="cron",
                    status="pending",
                    current_stage=None,
                    progress_pct=0,
                    progress_label="排程中",
                    started_at=datetime.utcnow(),
                )
            )
            db.commit()
    except Exception:
        logger.exception("Failed to create SignalGenerationJob row")
        return EXIT_DB_ERROR

    # Inline 跑 pipeline（cron 環境不需要 BackgroundTasks）
    try:
        run_signal_pipeline_sync(job_id=job_id, target_date=target_date)
    except Exception as exc:
        code = _classify_exit_code(exc)
        logger.error(
            "Pipeline failed: job_id=%s target=%s exit_code=%s",
            job_id,
            target_date,
            code,
        )
        return code

    logger.info("Daily signals pipeline ok: job_id=%s target=%s", job_id, target_date)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
