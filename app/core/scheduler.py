# app/core/scheduler.py  (또는 기존 위치 유지 가능)
from __future__ import annotations
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone
from app.core.config import get_settings
from app.core.logging import get_logger
from tasks.crawler import run_crawler
from tasks.sku_generator import run_sku_generation

logger = get_logger(__name__)
_scheduler: BackgroundScheduler | None = None

def crawl_and_update() -> None:
    """
    크롤링 → DB 저장 → SKU 생성 → 통계 업데이트 (순차 실행)
    실패 시 예외를 로깅하고 다음 런에서 재시도.
    """
    logger.info("=== 크롤링/통계 업데이트 시작 ===")
    try:
        logger.info("[1/2] 크롤링 실행")
        run_crawler()

        logger.info("[2/2] SKU/통계 갱신")
        run_sku_generation()

        logger.info("=== 크롤링/통계 업데이트 완료 ===")
    except Exception as e:
        logger.exception("크롤링/통계 업데이트 실패: %s", e)

def start_scheduler() -> None:
    """
    스케줄러 시작.
    - 환경설정으로 실행 여부/주기 제어
    - Asia/Seoul 타임존
    - 다중 프로세스 중복 실행 주의(배포 시 한 프로세스에서만 실행)
    """
    global _scheduler
    settings = get_settings()
    if getattr(settings, "SCHED_ENABLE", True) is False:
        logger.info("스케줄러 비활성화(SCHED_ENABLE=false)")
        return

    if _scheduler and _scheduler.running:
        logger.info("스케줄러가 이미 실행 중입니다.")
        return

    tz = timezone("Asia/Seoul")
    _scheduler = BackgroundScheduler(timezone=tz)

    # 크론 표현식 사용 예: 매일 03:00 실행 (env로 조정)
    cron_expr = getattr(settings, "SCHED_CRON_FULL", "0 3 * * *")  # 분 시 일 월 요일
    _scheduler.add_job(
        crawl_and_update,
        CronTrigger.from_crontab(cron_expr, timezone=tz),
        id="crawl_full_pipeline",
        replace_existing=True,
        max_instances=1,  # 중복 실행 방지
        coalesce=True,    # 밀린 작업 합치기
        misfire_grace_time=60 * 10,
    )

    _scheduler.start()
    logger.info("✅ 스케줄러 시작: CRON=%s TZ=Asia/Seoul", cron_expr)

def stop_scheduler() -> None:
    """스케줄러 종료."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("✅ 스케줄러 종료")
        _scheduler = None
