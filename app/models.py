"""数据库模型定义。"""

import json
from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    Text,
    Index,
    DateTime,
    Table,
    ForeignKey,
)
from sqlalchemy.orm import relationship
from app.database import Base


class Paper(Base):
    """论文条目 —— 对应原系统 CSV 中的每条记录，新增命中信息字段。"""

    __tablename__ = "papers"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # 论文唯一标识，用 link 去重（UNIQUE 约束，INSERT OR IGNORE 自动处理）
    link = Column(Text, nullable=False, unique=True)

    # 基本字段（对应原系统 5 个字段）
    title = Column(Text, nullable=False)
    summary = Column(Text)
    authors = Column(Text)
    date_added = Column(Text, nullable=False)  # ISO 8601 格式

    # 来源相关
    source = Column(Text, nullable=False)  # 'arxiv', 'nature', 'science', 'prl', 'apl' 等
    source_url = Column(Text)

    # 过滤命中信息（JSON 字符串存储）
    matched_keywords = Column(Text)  # JSON 数组: ["qubit", "circuit QED"]
    matched_authors = Column(Text)  # JSON 数组: ["Liang Jiang", "Haohua Wang"]
    author_group = Column(Integer, default=0)  # 0=未命中, 1=国际, 2=国内, 3=两组都命中

    # 元数据
    fetched_at = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_papers_date_added", "date_added"),
        Index("idx_papers_source", "source"),
        Index("idx_papers_author_group", "author_group"),
    )

    # —— 便捷属性：把 JSON 字符串解析为 Python 列表 ——
    @property
    def keyword_list(self):
        return json.loads(self.matched_keywords) if self.matched_keywords else []

    @property
    def author_list(self):
        return json.loads(self.matched_authors) if self.matched_authors else []

    # 收藏（一篇论文最多一条收藏记录；论文被删除时级联删除收藏）
    favorite = relationship(
        "Favorite",
        back_populates="paper",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def to_dict(self):
        """转为字典（API 响应用）。"""
        return {
            "id": self.id,
            "title": self.title,
            "link": self.link,
            "summary": self.summary,
            "authors": self.authors,
            "date_added": self.date_added,
            "source": self.source,
            "source_url": self.source_url,
            "matched_keywords": self.keyword_list,
            "matched_authors": self.author_list,
            "author_group": self.author_group,
            "fetched_at": self.fetched_at,
        }


class FetchLog(Base):
    """抓取日志 —— 记录每次抓取的运行情况。"""

    __tablename__ = "fetch_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(Text, nullable=False)  # 'ALL' 或具体源名
    started_at = Column(Text, nullable=False)
    finished_at = Column(Text)
    status = Column(Text, nullable=False)  # 'success', 'error', 'partial'
    papers_fetched = Column(Integer, default=0)  # 抓取到的总数
    papers_new = Column(Integer, default=0)  # 去重后新增数
    error_message = Column(Text)


class FavoriteGroup(Base):
    """收藏分组 —— 收藏文献的归类容器，一篇收藏可同时属于多个分组。"""

    __tablename__ = "favorite_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# 收藏 × 分组 多对多关联表
favorite_group_link = Table(
    "favorite_group_link",
    Base.metadata,
    Column(
        "favorite_id",
        Integer,
        ForeignKey("favorites.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "group_id",
        Integer,
        ForeignKey("favorite_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Favorite(Base):
    """收藏 —— 对某篇论文的收藏记录，可备注、可挂本地 PDF、可归入多个分组。"""

    __tablename__ = "favorites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(
        Integer, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # 备注（可选）
    note = Column(Text)

    # PDF 附件：
    #   pdf_origin = 'upload' 时 pdf_path 是 data/attachments/ 内的文件名；
    #   pdf_origin = 'local'  时 pdf_path 是本机绝对路径（不复制文件）。
    pdf_path = Column(Text)
    pdf_origin = Column(Text)  # 'upload' / 'local' / None
    pdf_name = Column(Text)  # 展示用文件名

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    paper = relationship("Paper", back_populates="favorite")
    groups = relationship(
        "FavoriteGroup",
        secondary=favorite_group_link,
        backref="favorites",
        lazy="selectin",
    )

    def group_names(self):
        return [g.name for g in self.groups]
