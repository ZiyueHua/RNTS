"""定时任务调度 —— 每日自动抓取 + 过滤 + 入库。"""

import json
import logging
from datetime import date, datetime, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.config import load_config, get_enabled_sources
from app.database import SessionLocal
from app.models import Paper, FetchLog, Favorite
from app.fetcher import fetch_all_sources_sync, fetch_arxiv_window_sync
from app.filters import classify_papers

# 原始词条滚动保留期（天）。
# - 未命中（author_group == 0）的原始词条：保留 RAW_RETENTION_DAYS 天，
#   主要用于支撑月报（30 天窗口）与一个月内 retroactive 调整关键词/作者。
# - 作者匹配（author_group != 0）的词条：保留 AUTHOR_RETENTION_DAYS 天，
#   以支撑"近一年作者精选报告"（365 天窗口），避免一年窗口被 31 天清理掏空。
RAW_RETENTION_DAYS = 31
AUTHOR_RETENTION_DAYS = 365

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _persist_papers(db, classified: list[dict], now: str) -> int:
    """把分类后的论文批量 upsert 入库，返回**真正新增**的条数。

    SQLite 在 ON CONFLICT DO UPDATE 时会把「更新」也计入 rowcount。改用
    arXiv 时间窗抓取后，每次都会重写窗口内的旧条目，直接取 rowcount 会把
    新增数严重高估（例如实际新增 3 篇却报 800 篇）。所以用入库前后的总行数
    差来判断新增。

    同一个 link 在本次批量里出现多次时只保留最后一条（翻页期间若有新论文
    插入，arXiv 的 start 偏移会整体后移，可能导致同一篇被返回两次）。
    """
    by_link = {p["link"]: p for p in classified}
    if not by_link:
        return 0

    before = db.query(func.count(Paper.id)).scalar() or 0

    values = [
        {
            "link": p["link"],
            "title": p["title"],
            "summary": p.get("summary", ""),
            "authors": p.get("authors", ""),
            "date_added": p["date_added"],
            "source": p["source"],
            "source_url": p.get("source_url", ""),
            "matched_keywords": json.dumps(p.get("matched_keywords", []), ensure_ascii=False),
            "matched_authors": json.dumps(p.get("matched_authors", []), ensure_ascii=False),
            "author_group": p.get("author_group", 0),
            "fetched_at": now,
        }
        for p in by_link.values()
    ]

    stmt = sqlite_insert(Paper).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["link"],
        set_={
            "title": stmt.excluded.title,
            "summary": stmt.excluded.summary,
            "authors": stmt.excluded.authors,
            "date_added": stmt.excluded.date_added,
            "source": stmt.excluded.source,
            "source_url": stmt.excluded.source_url,
            "matched_keywords": stmt.excluded.matched_keywords,
            "matched_authors": stmt.excluded.matched_authors,
            "author_group": stmt.excluded.author_group,
            "fetched_at": stmt.excluded.fetched_at,
        },
    )
    db.execute(stmt)
    db.commit()

    after = db.query(func.count(Paper.id)).scalar() or 0
    return max(0, after - before)


def _purge_expired(db, now_dt: datetime) -> int:
    """滚动保留期清理，返回删除条数。

    - 未命中条目（author_group == 0）超过 RAW_RETENTION_DAYS 天即删；
    - 作者匹配条目（author_group != 0）超过 AUTHOR_RETENTION_DAYS 天才删，
      这样一年作者精选报告窗口不被 31 天清理掏空；
    - 被收藏的文献永不清理——用户明确标记过想长期保存的内容。
    """
    raw_cutoff = (now_dt - timedelta(days=RAW_RETENTION_DAYS)).isoformat()
    author_cutoff = (now_dt - timedelta(days=AUTHOR_RETENTION_DAYS)).isoformat()
    purge_q = db.query(Paper).filter(
        ((Paper.author_group == 0) & (Paper.fetched_at < raw_cutoff))
        | ((Paper.author_group != 0) & (Paper.fetched_at < author_cutoff))
    )
    fav_ids = {pid for (pid,) in db.query(Favorite.paper_id).all()}
    if fav_ids:
        purge_q = purge_q.filter(~Paper.id.in_(fav_ids))
    deleted = purge_q.delete(synchronize_session=False)
    db.commit()
    return deleted or 0


def run_daily_fetch() -> dict:
    """执行一次完整的抓取-过滤-入库流程。

    可被定时任务或手动 API 调用。返回统计信息。
    """
    started_at = datetime.now(timezone.utc).isoformat()
    logger.info(f"[{started_at}] 开始每日抓取")

    # 注：这里不再自动排序 config.yaml。自动改写会冲掉使用者手写的注释、
    # 也会打乱他手动调整的顺序；需要整理时手动跑 sort_config.bat 即可。

    config = load_config()
    sources = get_enabled_sources()

    stats = {
        "started_at": started_at,
        "sources_count": len(sources),
        "papers_fetched": 0,
        "papers_matched": 0,
        "papers_new": 0,
        "papers_purged": 0,
        "status": "success",
        "error": None,
    }

    try:
        # 1. 抓取所有源（全量，不做预筛选）
        raw_papers = fetch_all_sources_sync(
            [s.model_dump() for s in sources],
            timeout=config.schedule.fetch_timeout,
        )
        stats["papers_fetched"] = len(raw_papers)

        # 2. 用【当前】配置实时判定命中（仅用于统计与快照，不影响入库范围）
        classified = classify_papers(
            raw_papers,
            config.keywords,
            config.highlight_authors_1,
            config.highlight_authors_2,
        )
        stats["papers_matched"] = sum(1 for c in classified if c["is_match"])

        # 3. 全量入库（UNIQUE(link) 约束去重；冲突时刷新 fetched_at 等字段，
        #    保证重新抓到的词条在保留期内不被误清理）
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc).isoformat()
            new_count = _persist_papers(db, classified, now)
            stats["papers_new"] = new_count

            # 4. 滚动保留期清理（规则见 _purge_expired）
            purged = _purge_expired(db, datetime.now(timezone.utc))
            stats["papers_purged"] = purged

            logger.info(
                f"入库完成：抓取 {len(raw_papers)} 篇，"
                f"当前配置命中 {stats['papers_matched']} 篇，"
                f"新增 {new_count} 篇，清理超期 {stats['papers_purged']} 篇"
            )

            # 4. 记录日志
            log = FetchLog(
                source="ALL",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                status="success",
                papers_fetched=len(raw_papers),
                papers_new=new_count,
            )
            db.add(log)
            db.commit()

            # 5. 抓取成功后自动重新生成 Markdown 报告产物（落盘 data/reports）
            try:
                from app.reports import generate_report_artifacts
                rep = generate_report_artifacts()
                if rep.get("status") == "success":
                    logger.info(
                        f"报告产物已更新：{rep['monthly']} ({rep['monthly_bytes']}B), "
                        f"{rep['authors']} ({rep['authors_bytes']}B)"
                    )
                else:
                    logger.warning(f"报告产物生成失败: {rep.get('error')}")
            except Exception as e:
                logger.warning(f"报告产物生成异常（不影响抓取）: {e}")

        finally:
            db.close()

    except Exception as e:
        stats["status"] = "error"
        stats["error"] = str(e)
        logger.error(f"每日抓取失败: {e}", exc_info=True)

        # 记录错误日志
        db = SessionLocal()
        try:
            log = FetchLog(
                source="ALL",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                papers_fetched=stats["papers_fetched"],
                papers_new=0,
                error_message=str(e),
            )
            db.add(log)
            db.commit()
        finally:
            db.close()

    return stats


def run_arxiv_date_fetch(target_date: str) -> dict:
    """按指定日期单独补抓 arXiv 论文（提交日期区间 D-lookback ~ D）。

    用于某天没开机、错过当天 arXiv 公告后补齐。与每日抓取的区别：
    - 只抓 arXiv 一个源，不碰其它 RSS 源；
    - 窗口以用户选定日期为终点向前回溯 arxiv_lookback_days 天
      （arXiv 公告比提交晚 1–2 天，回溯可保证"那天刷到的文章"被覆盖）；
    - **不做保留期清理**：补抓只增不删，避免顺带删掉别的数据；
    - 重复补抓靠 UNIQUE(link) 去重，无副作用。

    Args:
        target_date: 目标日期，格式 YYYY-MM-DD

    Returns:
        stats 字典（status/date/papers_fetched/papers_matched/papers_new/error）
    """
    started_at = datetime.now(timezone.utc).isoformat()
    stats = {
        "started_at": started_at,
        "date": target_date,
        "papers_fetched": 0,
        "papers_matched": 0,
        "papers_new": 0,
        "status": "success",
        "error": None,
    }

    config = load_config()
    source = next(
        (s for s in config.rss_sources if s.enabled and s.extractor == "arxiv"),
        None,
    )
    if source is None:
        stats["status"] = "error"
        stats["error"] = "config.yaml 中没有启用的 arXiv 源"
        logger.error("按日期补抓失败: 没有启用的 arXiv 源")
        return stats

    try:
        end_date = date.fromisoformat(target_date)
    except ValueError:
        stats["status"] = "error"
        stats["error"] = f"日期格式无效: {target_date}（应为 YYYY-MM-DD）"
        return stats

    lookback = max(0, int(config.schedule.arxiv_lookback_days))
    start_date = end_date - timedelta(days=lookback)
    logger.info(f"[{started_at}] 按日期补抓 arXiv: {start_date} ~ {end_date}")

    db = SessionLocal()
    try:
        raw_papers = fetch_arxiv_window_sync(
            source.model_dump(), start_date, end_date
        )
        stats["papers_fetched"] = len(raw_papers)

        classified = classify_papers(
            raw_papers,
            config.keywords,
            config.highlight_authors_1,
            config.highlight_authors_2,
        )
        stats["papers_matched"] = sum(1 for c in classified if c["is_match"])
        stats["papers_new"] = _persist_papers(
            db, classified, datetime.now(timezone.utc).isoformat()
        )

        db.add(
            FetchLog(
                source=f"arxiv:{target_date}",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                status="success",
                papers_fetched=len(raw_papers),
                papers_new=stats["papers_new"],
            )
        )
        db.commit()

        logger.info(
            f"补抓完成 [{start_date} ~ {end_date}]：抓取 {len(raw_papers)} 篇，"
            f"当前配置命中 {stats['papers_matched']} 篇，"
            f"新增 {stats['papers_new']} 篇"
        )
    except Exception as e:
        stats["status"] = "error"
        stats["error"] = str(e)
        logger.error(f"按日期补抓 arXiv 失败: {e}", exc_info=True)
        try:
            db.add(
                FetchLog(
                    source=f"arxiv:{target_date}",
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    status="error",
                    papers_fetched=stats["papers_fetched"],
                    papers_new=0,
                    error_message=str(e),
                )
            )
            db.commit()
        except Exception as log_err:
            logger.warning(f"补抓错误日志写入失败: {log_err}")
            db.rollback()
    finally:
        db.close()

    # 补抓成功后刷新报告产物（与每日抓取保持一致）
    if stats["status"] == "success":
        try:
            from app.reports import generate_report_artifacts

            rep = generate_report_artifacts()
            if rep.get("status") == "success":
                logger.info(f"报告产物已更新：{rep['monthly']}, {rep['authors']}")
            else:
                logger.warning(f"报告产物生成失败: {rep.get('error')}")
        except Exception as e:
            logger.warning(f"报告产物生成异常（不影响抓取）: {e}")

    return stats


def _already_fetched_today() -> bool:
    """检查今天是否已经成功抓取过（用于启动时补跑判断）。"""
    db = SessionLocal()
    try:
        from datetime import timezone as _tz
        today = datetime.now(_tz.utc).strftime("%Y-%m-%d")
        log = (
            db.query(FetchLog)
            .filter(FetchLog.source == "ALL")
            .filter(FetchLog.status == "success")
            .filter(FetchLog.started_at.like(f"{today}%"))
            .first()
        )
        return log is not None
    finally:
        db.close()


def init_scheduler():
    """初始化并启动后台调度器（在 FastAPI lifespan 中调用）。"""
    global _scheduler

    if _scheduler is not None:
        return _scheduler

    config = load_config()
    time_str = config.schedule.daily_fetch_time
    hour, minute = time_str.split(":")

    _scheduler = BackgroundScheduler()

    # misfire_grace_time=3600：允许进程中断最多 1 小时后补跑错过的调度
    # coalesce=True：积压的多次错过合并为一次执行
    _scheduler.add_job(
        run_daily_fetch,
        trigger=CronTrigger(hour=int(hour), minute=int(minute)),
        id="daily_fetch",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )

    _scheduler.start()
    logger.info(f"调度器已启动，每日 {time_str} 自动抓取（misfire_grace_time=3600s）")

    # 启动时补跑检查：如果今天还没抓过，立即补跑一次
    # 解决进程在调度时刻未运行导致跳过的问题
    try:
        if not _already_fetched_today():
            logger.info("启动时发现今天尚未抓取，立即补跑一次每日抓取")
            _scheduler.add_job(
                run_daily_fetch,
                id="daily_fetch_catchup",
                replace_existing=True,
            )
    except Exception as e:
        logger.warning(f"启动补跑检查失败（不影响主调度）: {e}")

    return _scheduler


def shutdown_scheduler():
    """关闭调度器。"""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
