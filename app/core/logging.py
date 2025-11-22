# app/core/logging.py
from __future__ import annotations
import logging
from logging.config import dictConfig
from pathlib import Path
from app.core.config import get_settings

def setup_logging() -> None:
    """
    프로젝트 전역 로깅 설정을 초기화한다.
    - 콘솔 스트림 핸들러
    - 회전 파일 핸들러 (logs/app.log)
    - uvicorn / sqlalchemy 로거 레벨 정렬
    """
    settings = get_settings()
    log_dir = Path("logs"); log_dir.mkdir(parents=True, exist_ok=True)

    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "access": {
                "format": '%(asctime)s | %(levelname)s | %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": settings.LOG_LEVEL if hasattr(settings, "LOG_LEVEL") else "INFO",
                "formatter": "default",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": settings.LOG_LEVEL if hasattr(settings, "LOG_LEVEL") else "INFO",
                "formatter": "default",
                "filename": str(log_dir / "app.log"),
                "maxBytes": 5 * 1024 * 1024,
                "backupCount": 5,
                "encoding": "utf-8",
            },
        },
        "loggers": {
            "": {  # root
                "handlers": ["console", "file"],
                "level": settings.LOG_LEVEL if hasattr(settings, "LOG_LEVEL") else "INFO",
            },
            "uvicorn": {"level": "INFO"},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"level": "INFO", "propagate": False, "handlers": ["console"]},
            "sqlalchemy.engine": {"level": "WARNING"},
            "apscheduler": {"level": "INFO"},
        },
    })

def get_logger(name: str) -> logging.Logger:
    """모듈에서 가져다 쓰는 헬퍼."""
    return logging.getLogger(name)
