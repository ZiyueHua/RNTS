"""FastAPI 应用入口 —— 组装路由 + 启动调度器。"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.database import init_db
from app.scheduler import init_scheduler, shutdown_scheduler

# 版本号定义在 app/__init__.py，main / reports / routes / 模板共用同一个值

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库和调度器，关闭时清理。"""
    logger.info("正在初始化数据库...")
    init_db()

    logger.info("正在启动定时任务调度器...")
    init_scheduler()

    logger.info(f"RNTS v{__version__} 启动完成")
    yield

    logger.info("正在关闭调度器...")
    shutdown_scheduler()
    logger.info("RNTS 已关闭")


app = FastAPI(
    title="RNTS — 量子物理学术新闻追踪系统",
    description="Research News Tracking System for Quantum Physics",
    version=__version__,
    lifespan=lifespan,
)

# 静态文件
app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static",
)

# 报告产物（.html / .md）可直接通过浏览器/手机浏览器在线打开
REPORTS_DIR = BASE_DIR / "data" / "reports"
if REPORTS_DIR.exists():
    app.mount(
        "/reports",
        StaticFiles(directory=str(REPORTS_DIR)),
        name="reports",
    )

# Jinja2 模板
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# 版本号注入所有模板（base.html 页脚用 {{ app_version }}），避免页脚与程序版本不一致
templates.env.globals["app_version"] = __version__
app.state.templates = templates

# 注册路由
from app.routes import router  # noqa: E402
from app.routes_favorites import router as favorites_router  # noqa: E402

app.include_router(router)
app.include_router(favorites_router)
