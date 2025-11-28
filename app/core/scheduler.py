from __future__ import annotations
import asyncio
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import get_settings

_scheduler: Optional[AsyncIOScheduler] = None

async def job_crawl_daangn():
    # 여기에 실제 크롤러 호출 로직
    # 예: await crawl_all()
    print("[sched] run daangn")

def start_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    settings = get_settings()
    _scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    if settings.SCHED_ENABLE:
        # 24시간마다 전체 수집 예시 (settings에서 cron 문자열 가져옴)
        _scheduler.add_job(
            job_crawl_daangn,
            CronTrigger.from_crontab(settings.SCHED_CRON_DAANGN)
        )
        # 필요 시 다른 잡도 등록
        # _scheduler.add_job(...)

    _scheduler.start()

def shutdown_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
