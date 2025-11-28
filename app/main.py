from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.scheduler import start_scheduler
from app.api.v1.analytics import router as analytics_router
from contextlib import asynccontextmanager
from app.core.scheduler import start_scheduler, shutdown_scheduler

app = FastAPI(
    title="HowMuch API",
    description="중고 애플 제품 시세 조회 API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: 프론트엔드 도메인으로 제한 필요
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()

app = FastAPI(lifespan=lifespan)

# 라우터 등록
app.include_router(analytics_router, prefix="/api/v1")

