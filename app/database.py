"""SQLAlchemy 数据库引擎、会话工厂和 Base。"""

import re
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base

# 数据库文件路径
DB_DIR = Path(__file__).parent.parent / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "rnts.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},  # FastAPI 多线程访问
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


def get_db():
    """FastAPI 依赖注入：获取数据库会话，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """创建所有表（如果不存在），并执行幂等的数据规范化迁移。"""
    from app import models  # noqa: F401 — 确保模型被导入
    Base.metadata.create_all(bind=engine)
    normalize_arxiv_links()


def normalize_arxiv_links() -> int:
    """把库中 arXiv 论文链接统一为 https://arxiv.org/abs/<id>（去版本号）。

    背景：搜索 API 返回的链接带版本号（…/abs/2609.12345v1），而 RSS 公告源
    返回不带版本号的写法（…/abs/2609.12345）。两种写法会让 UNIQUE(link)
    去重失效、同一篇论文重复入库，因此入库前后都统一为规范形式
    （规范形式定义见 app/fetcher.py 的 canonical_arxiv_link）。

    幂等：可重复调用；只做 UPDATE，不删除任何行（否则会级联删掉收藏）。
    若目标链接已被占用则跳过该行，避免唯一约束冲突。
    返回规范化成功的条数。
    """
    pattern = re.compile(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/"
                         r"([\w.\-/]+?)(?:v\d+)?(?:\.pdf)?/?$")
    updated = 0
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT id, link FROM papers WHERE source = 'arxiv' "
            "AND link LIKE '%arxiv.org/%'"
        )).fetchall()
        if not rows:
            return 0
        existing = {link for (link,) in db.execute(
            text("SELECT link FROM papers")).fetchall()}
        for pid, link in rows:
            m = pattern.match(link or "")
            if not m:
                continue
            canon = f"https://arxiv.org/abs/{m.group(1)}"
            if canon == link or canon in existing:
                continue
            db.execute(text("UPDATE papers SET link = :c WHERE id = :i"),
                       {"c": canon, "i": pid})
            existing.discard(link)
            existing.add(canon)
            updated += 1
        if updated:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return updated
