"""Web 路由 —— 页面路由（返回 HTML）+ API 路由（JSON）。"""

import csv
import io
import json
import asyncio
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request, Query, Response
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from app import __version__
from app.config import load_config, save_config, AppConfig
from app.database import get_db
from app.models import Paper, FetchLog, Favorite
from app.scheduler import run_daily_fetch, run_arxiv_date_fetch
from app.filters import classify_paper

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_favorite_ids(db: Session) -> set:
    """全部已收藏的 paper_id 集合，用于渲染论文卡片上的收藏星标。"""
    return {pid for (pid,) in db.query(Favorite.paper_id).all()}


def _apply_current_match(raw_papers, config):
    """对一组 ORM Paper 用【当前】配置实时判定命中，并在内存中覆盖其
    matched_keywords / matched_authors / author_group（不提交数据库），
    使模板与导出接口无需改动即可读取「当前配置下」的命中结果。

    返回 list[(orm_paper, classified_dict)]。classify 基于 title/summary/authors
    重新计算，因此加/删关键词、加/删作者对历史数据 retroactive 生效。
    """
    paired = []
    for p in raw_papers:
        c = classify_paper(
            p.to_dict(),
            config.keywords,
            config.highlight_authors_1,
            config.highlight_authors_2,
        )
        # 内存覆盖（不 commit），模板读取 keyword_list / author_group 即反映当前配置
        p.matched_keywords = json.dumps(c["matched_keywords"], ensure_ascii=False)
        p.matched_authors = json.dumps(c["matched_authors"], ensure_ascii=False)
        p.author_group = c["author_group"]
        paired.append((p, c))
    return paired


# ============================================================
# 页面路由（返回 HTML）
# ============================================================
@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(0, ge=0),
    source: str = Query(""),
    author_group: int = Query(0),
    keyword: str = Query(""),
    search: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    matched_only: int = Query(1),
):
    """论文列表页 —— 支持筛选/搜索/分页，HTMX 局部刷新。

    matched_only=1（默认）只显示命中关键词或高亮作者的文献；
    matched_only=0 显示库中全部原始文献。
    """
    config = load_config()
    if per_page == 0:
        per_page = config.schedule.per_page

    query = db.query(Paper)

    # 基础筛选（不依赖命中判定，可下推到 SQL）
    if source:
        query = query.filter(Paper.source == source)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Paper.title.like(search_term)) | (Paper.summary.like(search_term))
        )
    if date_from:
        query = query.filter(Paper.date_added >= date_from)
    if date_to:
        query = query.filter(Paper.date_added <= date_to + "T23:59:59")

    # 取全部原始词条，按【当前】配置实时判定命中（不预筛选，展示全部原始）
    raw = query.order_by(desc(Paper.date_added)).all()
    paired = _apply_current_match(raw, config)

    # 显示范围：只保留命中（关键词或高亮作者任一）
    if matched_only:
        paired = [(p, c) for (p, c) in paired if c["is_match"]]

    # 命中相关筛选改在 classified 结果上进行（当前配置，retroactive 生效）
    if author_group:
        allowed = {1: [1, 3], 2: [2, 3], 3: [3]}.get(author_group, [])
        paired = [(p, c) for (p, c) in paired if c["author_group"] in allowed]
    if keyword:
        paired = [(p, c) for (p, c) in paired if keyword in c["matched_keywords"]]

    papers_all = [p for (p, c) in paired]

    # 内存分页（已按当前配置过滤）
    total = len(papers_all)
    total_pages = max(1, (total + per_page - 1) // per_page)
    papers = papers_all[(page - 1) * per_page: page * per_page]

    # 获取所有来源列表（用于筛选面板）
    sources = [(s.name, s.display_name) for s in config.rss_sources if s.enabled]
    keywords = config.keywords

    # 计算分页范围（当前页前后各 2 页）
    page_range = list(range(max(1, page - 2), min(total_pages + 1, page + 3)))

    # 已收藏集合（星标渲染）
    fav_ids = _get_favorite_ids(db)

    # HTMX 请求只返回列表片段
    if request.headers.get("HX-Request"):
        return request.app.state.templates.TemplateResponse(
            request,
            "partials/paper_list.html",
            {
                "papers": papers,
                "favorite_ids": fav_ids,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "page_range": page_range,
                "filters": {
                    "source": source,
                    "author_group": author_group,
                    "keyword": keyword,
                    "search": search,
                    "date_from": date_from,
                    "date_to": date_to,
                    "per_page": per_page,
                    "matched_only": matched_only,
                },
            },
        )

    return request.app.state.templates.TemplateResponse(
        request,
        "index.html",
        {
            "papers": papers,
            "favorite_ids": fav_ids,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "page_range": page_range,
            "sources": sources,
            "keywords": keywords,
            "filters": {
                "source": source,
                "author_group": author_group,
                "keyword": keyword,
                "search": search,
                "date_from": date_from,
                "date_to": date_to,
                "per_page": per_page,
                "matched_only": matched_only,
            },
        },
    )


@router.get("/paper/{paper_id}", response_class=HTMLResponse)
async def paper_detail(
    paper_id: int, request: Request, db: Session = Depends(get_db)
):
    """单篇论文详情页。"""
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        return HTMLResponse("论文不存在", status_code=404)
    fav = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    return request.app.state.templates.TemplateResponse(
        request,
        "paper_detail.html",
        {
            "paper": paper,
            "favorite": fav,
            "faved": fav is not None,
        },
    )


@router.get("/authors", response_class=HTMLResponse)
async def authors_page(request: Request, db: Session = Depends(get_db)):
    """作者精选页 —— 国际/国内两大区块，每位高亮作者一个标签页。

    取近 365 天 author_group != 0 的论文，用当前配置实时按作者聚合
    （同一篇论文可出现在多位作者名下），加/删作者 retroactive 生效。
    """
    from datetime import timezone
    from app.filters import _strip_inline_comment

    config = load_config()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()

    rows = (
        db.query(Paper)
        .filter(Paper.date_added >= cutoff)
        .order_by(desc(Paper.date_added))
        .all()
    )
    # 用当前配置实时判定命中（复用列表页的 retroactive 逻辑）
    paired = _apply_current_match(rows, config)
    papers = [p for (p, _c) in paired if p.author_group != 0]

    def collect(author_list):
        """按配置顺序聚合每位作者的论文（跳过无命中的作者）。"""
        result = []
        for author in author_list:
            name = _strip_inline_comment(author)
            author_papers = [p for p in papers if author in p.author_list]
            if author_papers:
                result.append({"name": name, "papers": author_papers})
        return result

    source_names = {s.name: s.display_name for s in config.rss_sources}

    return request.app.state.templates.TemplateResponse(
        request,
        "authors.html",
        {
            "intl_authors": collect(config.highlight_authors_1),
            "cn_authors": collect(config.highlight_authors_2),
            "source_names": source_names,
            "date_from": cutoff[:10],
            "total_papers": len(papers),
            "favorite_ids": _get_favorite_ids(db),
        },
    )


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request, db: Session = Depends(get_db)):
    """统计仪表盘。"""
    # 总数（全部原始词条）
    total = db.query(Paper).count()

    # 加载当前配置，用于实时判定命中
    config = load_config()

    # 用【当前】配置实时判定全部保留期内词条的命中情况
    all_papers = db.query(Paper).all()
    classified = [
        classify_paper(
            p.to_dict(),
            config.keywords,
            config.highlight_authors_1,
            config.highlight_authors_2,
        )
        for p in all_papers
    ]

    # 今日 / 本周新增（按发布日期，原始词条）
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_new = sum(
        1 for p in all_papers if (p.date_added or "").startswith(today_str)
    )
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    week_new = sum(1 for p in all_papers if (p.date_added or "") >= week_ago)

    # 高亮作者命中数（当前配置）
    highlighted = sum(1 for c in classified if c["author_group"] != 0)

    # 按来源统计（原始词条）
    _src = {}
    for p in all_papers:
        _src[p.source] = _src.get(p.source, 0) + 1
    by_source = sorted(_src.items(), key=lambda x: -x[1])

    # 按作者组统计（当前配置）
    _grp = {}
    for c in classified:
        _grp[c["author_group"]] = _grp.get(c["author_group"], 0) + 1
    by_group = sorted(_grp.items())

    # 近 30 天趋势（原始词条，按发布日期）
    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    _trend = {}
    for p in all_papers:
        d = (p.date_added or "")[:10]
        if d >= thirty_days_ago:
            _trend[d] = _trend.get(d, 0) + 1
    trend = sorted(_trend.items())

    # 关键词命中排行（当前配置）
    kw_count = {}
    for c in classified:
        for k in c["matched_keywords"]:
            kw_count[k] = kw_count.get(k, 0) + 1
    top_keywords = sorted(kw_count.items(), key=lambda x: -x[1])[:15]

    # 高亮作者命中排行（当前配置）
    author_count = {}
    for c in classified:
        for a in c["matched_authors"]:
            author_count[a] = author_count.get(a, 0) + 1
    top_authors = sorted(author_count.items(), key=lambda x: -x[1])[:15]

    # 获取来源显示名
    config = load_config()
    source_names = {s.name: s.display_name for s in config.rss_sources}

    return request.app.state.templates.TemplateResponse(
        request,
        "stats.html",
        {
            "total": total,
            "today_new": today_new,
            "week_new": week_new,
            "highlighted": highlighted,
            "by_source": [(source_names.get(s, s), c) for s, c in by_source],
            "by_group": by_group,
            "trend": trend,
            "top_keywords": top_keywords,
            "top_authors": top_authors,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """配置管理页。"""
    config = load_config()
    return request.app.state.templates.TemplateResponse(
        request, "settings.html", {"config": config}
    )


# ============================================================
# API 路由（JSON）
# ============================================================
@router.post("/api/v1/papers/fetch")
async def api_trigger_fetch():
    """手动触发抓取。在线程池中运行以避免阻塞 event loop。"""
    import concurrent.futures
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        stats = await loop.run_in_executor(pool, run_daily_fetch)
    return JSONResponse(stats)


@router.post("/api/v1/papers/fetch_arxiv")
async def api_fetch_arxiv_by_date(date: str = Query(..., description="目标日期 YYYY-MM-DD")):
    """按指定日期单独补抓 arXiv（补上某天没开机而错过的文章）。

    抓取窗口为 [date - schedule.arxiv_lookback_days, date]，只抓 arXiv，
    不做保留期清理，重复补抓靠 UNIQUE(link) 去重。与 /api/v1/papers/fetch 一样
    在线程池中运行，避免阻塞 event loop。
    """
    import concurrent.futures

    target_date = date.strip()
    try:
        target = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return JSONResponse(
            {"status": "error", "error": f"日期格式无效: {date}（应为 YYYY-MM-DD）"},
            status_code=400,
        )
    if target > datetime.now().date():
        return JSONResponse(
            {"status": "error", "error": f"不能抓取未来日期: {target_date}"},
            status_code=400,
        )

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        stats = await loop.run_in_executor(
            pool, run_arxiv_date_fetch, target_date
        )
    return JSONResponse(stats)


@router.get("/api/v1/papers/export")
async def api_export_csv(
    db: Session = Depends(get_db),
    source: str = Query(""),
    author_group: int = Query(0),
    search: str = Query(""),
    matched_only: int = Query(0),
):
    """导出为 CSV（兼容原系统格式）。"""
    config = load_config()
    query = db.query(Paper)
    if source:
        query = query.filter(Paper.source == source)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Paper.title.like(search_term)) | (Paper.summary.like(search_term))
        )
    # 取全部原始词条，按【当前】配置实时判定命中；默认导出全部原始（命中的带标签）
    raw = query.order_by(desc(Paper.date_added)).all()
    paired = _apply_current_match(raw, config)
    if matched_only:
        paired = [(p, c) for (p, c) in paired if c["is_match"]]
    if author_group:
        allowed = {1: [1, 3], 2: [2, 3], 3: [3]}.get(author_group, [])
        paired = [(p, c) for (p, c) in paired if c["author_group"] in allowed]
    papers = [p for (p, c) in paired]

    # 生成 CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["title", "link", "summary", "authors", "date_added", "source", "matched_keywords"]
    )
    for p in papers:
        writer.writerow(
            [
                p.title,
                p.link,
                p.summary or "",
                p.authors or "",
                p.date_added,
                p.source,
                ", ".join(p.keyword_list),
            ]
        )

    output.seek(0)
    filename = f"rnts_papers_{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/api/v1/papers/export_html")
async def api_export_html(
    db: Session = Depends(get_db),
    source: str = Query(""),
    author_group: int = Query(0),
    search: str = Query(""),
    matched_only: int = Query(0),
):
    """导出为独立 HTML 报告文件（替代原系统的 filtered_paper_list_YYMM.html）。"""
    config = load_config()
    query = db.query(Paper)
    if source:
        query = query.filter(Paper.source == source)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Paper.title.like(search_term)) | (Paper.summary.like(search_term))
        )
    # 取全部原始词条，按【当前】配置实时判定命中；默认导出全部原始（命中的带标签）
    raw = query.order_by(desc(Paper.date_added)).all()
    paired = _apply_current_match(raw, config)
    if matched_only:
        paired = [(p, c) for (p, c) in paired if c["is_match"]]
    if author_group:
        allowed = {1: [1, 3], 2: [2, 3], 3: [3]}.get(author_group, [])
        paired = [(p, c) for (p, c) in paired if c["author_group"] in allowed]
    papers = [p for (p, c) in paired]

    # 获取来源显示名
    config = load_config()
    source_names = {s.name: s.display_name for s in config.rss_sources}

    # 生成独立 HTML（内联 CSS，可直接双击打开）
    html_parts = [f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RNTS 论文报告 — {datetime.now().strftime('%Y-%m-%d')}</title>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', Roboto, Arial, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; color: #333; }}
  h1 {{ text-align: center; color: #1565c0; margin-bottom: 5px; }}
  .meta {{ text-align: center; color: #888; font-size: 14px; margin-bottom: 20px; }}
  .paper {{ background: white; border-radius: 8px; padding: 16px 20px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  .paper-title {{ font-size: 16px; font-weight: 600; margin-bottom: 6px; }}
  .paper-title a {{ color: #1565c0; text-decoration: none; }}
  .paper-title a:hover {{ text-decoration: underline; }}
  .paper-meta {{ font-size: 13px; color: #666; margin-bottom: 4px; }}
  .paper-summary {{ font-size: 14px; line-height: 1.6; color: #555; margin-top: 6px; }}
  .tag {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; margin-right: 4px; }}
  .tag-source {{ background: #e3f2fd; color: #1565c0; }}
  .tag-keyword {{ background: #fff3e0; color: #e65100; }}
  .tag-author {{ background: #e8f5e9; color: #2e7d32; }}
  .tag-author-intl {{ background: #fce4ec; color: #c62828; }}
</style>
</head>
<body>
<h1>量子物理论文追踪报告</h1>
<div class="meta">共 {len(papers)} 篇 · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
"""]

    for p in papers:
        tags_html = f'<span class="tag tag-source">{source_names.get(p.source, p.source)}</span>'
        for kw in p.keyword_list:
            tags_html += f'\n  <span class="tag tag-keyword">{kw}</span>'
        for au in p.author_list:
            tag_class = "tag-author-intl" if p.author_group in [1, 3] else "tag-author"
            tags_html += f'\n  <span class="tag {tag_class}">{au}</span>'

        date_str = p.date_added[:10] if p.date_added else ""
        authors_str = p.authors or ""
        if authors_str and len(authors_str) > 300:
            authors_str = authors_str[:300] + "..."
        summary_str = p.summary or ""
        if summary_str and len(summary_str) > 500:
            summary_str = summary_str[:500] + "..."

        html_parts.append(f"""
<div class="paper">
  <div class="paper-title"><a href="{p.link}" target="_blank">{p.title}</a></div>
  <div class="paper-meta">作者: {authors_str}</div>
  <div class="paper-meta">日期: {date_str}</div>
  <div class="paper-summary">{summary_str}</div>
  <div style="margin-top:8px;">{tags_html}</div>
</div>""")

    html_parts.append("\n</body>\n</html>\n")
    html_content = "\n".join(html_parts)

    filename = f"filtered_paper_list_{datetime.now().strftime('%Y%m%d')}.html"
    return StreamingResponse(
        iter([html_content]),
        media_type="text/html",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/api/v1/papers/export_md")
async def api_export_markdown(
    db: Session = Depends(get_db),
    source: str = Query(""),
    author_group: int = Query(0),
    search: str = Query(""),
    matched_only: int = Query(0),
):
    """导出为 Markdown 报告，按作者组分组，摘要完整显示。

    分组方式：
    - 国际+国内团队：作者列表中同时包含国际和国内高亮作者
    - 国际团队：仅包含国际高亮作者
    - 国内团队：仅包含国内高亮作者
    - 关键词命中：未命中任何高亮作者，但标题/摘要命中关键词
    """
    config = load_config()
    query = db.query(Paper)
    if source:
        query = query.filter(Paper.source == source)
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Paper.title.like(search_term)) | (Paper.summary.like(search_term))
        )
    # 取全部原始词条，按【当前】配置实时判定命中；默认导出全部原始（命中的带标签）
    raw = query.order_by(desc(Paper.date_added)).all()
    paired = _apply_current_match(raw, config)
    if matched_only:
        paired = [(p, c) for (p, c) in paired if c["is_match"]]
    if author_group:
        allowed = {1: [1, 3], 2: [2, 3], 3: [3]}.get(author_group, [])
        paired = [(p, c) for (p, c) in paired if c["author_group"] in allowed]
    papers = [p for (p, c) in paired]

    # 获取来源显示名
    config = load_config()
    source_names = {s.name: s.display_name for s in config.rss_sources}

    # 按作者组分组
    intl_only = []  # 仅国际
    cn_only = []  # 仅国内
    both = []  # 国际+国内
    kw_only = []  # 仅关键词

    for p in papers:
        authors = p.author_list
        in_intl = any(a in config.highlight_authors_1 for a in authors)
        in_cn = any(a in config.highlight_authors_2 for a in authors)

        if in_intl and in_cn:
            both.append(p)
        elif in_intl:
            intl_only.append(p)
        elif in_cn:
            cn_only.append(p)
        else:
            kw_only.append(p)

    # 生成 Markdown
    today = datetime.now()
    md = ["# 量子物理学术新闻追踪报告\n"]
    md.append(f"> **生成时间**: {today.strftime('%Y-%m-%d %H:%M')}  ")
    md.append(f"> **总论文数**: {len(papers)} 篇  ")
    if source:
        md.append(f"> **来源筛选**: {source_names.get(source, source)}  ")
    md.append(f"> **作者组筛选**: {author_group}  ")
    md.append("\n---\n\n")

    def fmt_paper(p, level=3):
        """生成单篇论文的 Markdown 块。"""
        date_str = (p.date_added[:10] if p.date_added else "")
        source_disp = source_names.get(p.source, p.source)

        tags = [f"`{source_disp}`"]
        for kw in p.keyword_list:
            tags.append(f"`{kw}`")

        lines = []
        lines.append(f"{'#' * level} [{p.title}]({p.link})")
        lines.append("")
        lines.append(f"**作者**: {p.authors or '未知'}")
        lines.append("")
        lines.append(f"**日期**: {date_str}  ")
        lines.append(f"**来源**: {p.source}  ")
        lines.append(f"**链接**: {p.link}")
        lines.append("")
        if tags:
            lines.append(" ".join(tags))
            lines.append("")
        if p.summary:
            lines.append("**摘要**:")
            lines.append("")
            # 用块引用格式（>），避免长摘要占据大块
            lines.append("> " + p.summary.replace("\n", "\n> "))
            lines.append("")
        return "\n".join(lines)

    sections = [
        ("🌍 国际+国内团队 (同时命中)", both, "国际+国内高亮作者都在论文作者列表中"),
        ("🌐 国际团队", intl_only, "作者列表中包含国际高亮作者（但不含国内作者）"),
        ("🇨🇳 国内团队", cn_only, "作者列表中包含国内高亮作者（但不含国际作者）"),
        ("🔑 关键词命中", kw_only, "未命中任何高亮作者，但标题/摘要命中关键词"),
    ]

    for title, items, desc_text in sections:
        if not items:
            continue
        md.append(f"## {title} ({len(items)} 篇)\n")
        md.append(f"*{desc_text}*\n")
        for p in items:
            md.append(fmt_paper(p, level=3))
            md.append("---\n")

    # 汇总统计
    md.append("\n## 📊 汇总统计\n\n")
    md.append("| 分组 | 论文数 |\n|------|--------|")
    md.append(f"| 🌍 国际+国内团队 | {len(both)} |")
    md.append(f"| 🌐 国际团队 | {len(intl_only)} |")
    md.append(f"| 🇨🇳 国内团队 | {len(cn_only)} |")
    md.append(f"| 🔑 关键词命中 | {len(kw_only)} |")
    md.append(f"| **合计** | **{len(papers)}** |")
    md.append("")

    # 按来源统计
    md.append("\n### 按来源分布\n\n")
    md.append("| 来源 | 论文数 |\n|------|--------|")
    source_count = {}
    for p in papers:
        source_count[p.source] = source_count.get(p.source, 0) + 1
    for src, cnt in sorted(source_count.items(), key=lambda x: -x[1]):
        md.append(f"| {source_names.get(src, src)} | {cnt} |")
    md.append("")

    # 高亮作者命中排行
    md.append("\n### 高亮作者命中排行\n\n")
    author_count = {}
    for p in papers:
        for a in p.author_list:
            author_count[a] = author_count.get(a, 0) + 1
    if author_count:
        md.append("| 作者 | 命中论文数 |\n|------|-----------|")
        for a, c in sorted(author_count.items(), key=lambda x: -x[1]):
            md.append(f"| {a} | {c} |")
    md.append("")

    md.append("\n---\n")
    md.append(f"\n*RNTS v{__version__} — Research News Tracking System for Quantum Physics*\n")

    md_content = "\n".join(md)

    filename = f"rnts_report_{today.strftime('%Y%m%d_%H%M')}.md"
    return StreamingResponse(
        iter([md_content]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ============================================================
# 新报告路由 —— 近一月更新 + 近一年作者精选
# ============================================================

from app.reports import build_monthly_report, build_authors_report, build_html_report


@router.get("/api/v1/papers/report_monthly")
async def api_report_monthly(format: str = "md", db: Session = Depends(get_db)):
    """近一月更新报告。

    包含 date_added 在最近 30 天内的所有命中文献，
    按日期降序排列（最新在前），不分组。
    format=html 时返回可直接在浏览器/手机端预览的 HTML；
    默认返回 Markdown 下载。
    """
    from datetime import timezone

    md_content = build_monthly_report(db)
    now = datetime.now(timezone.utc)
    if format == "html":
        html_content = build_html_report(md_content, "量子物理学术新闻 — 近一月更新")
        filename = f"rnts_monthly_{now.strftime('%Y%m%d_%H%M')}.html"
        return StreamingResponse(
            iter([html_content]),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    filename = f"rnts_monthly_{now.strftime('%Y%m%d_%H%M')}.md"
    return StreamingResponse(
        iter([md_content]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/api/v1/papers/report_authors")
async def api_report_authors(format: str = "md", db: Session = Depends(get_db)):
    """近一年作者精选报告。

    包含 date_added 在最近 365 天内、作者匹配（author_group != 0）的论文，
    按高亮作者分组，每位作者一个区块列出其参与的论文。
    同一篇论文可出现在多位高亮作者的区块下。
    format=html 时返回可直接在浏览器/手机端预览的 HTML；
    默认返回 Markdown 下载。
    """
    from datetime import timezone

    md_content = build_authors_report(db)
    now = datetime.now(timezone.utc)
    if format == "html":
        html_content = build_html_report(md_content, "量子物理学术新闻 — 近一年作者精选")
        filename = f"rnts_author_picks_{now.strftime('%Y%m%d_%H%M')}.html"
        return StreamingResponse(
            iter([html_content]),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    filename = f"rnts_author_picks_{now.strftime('%Y%m%d_%H%M')}.md"
    return StreamingResponse(
        iter([md_content]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/api/v1/stats/overview")
async def api_stats_overview(db: Session = Depends(get_db)):
    """统计总览 API。"""
    total = db.query(Paper).count()
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_new = (
        db.query(Paper).filter(Paper.date_added.like(f"{today_str}%")).count()
    )
    by_source = (
        db.query(Paper.source, func.count(Paper.id))
        .group_by(Paper.source)
        .all()
    )

    # 最近抓取日志
    last_log = (
        db.query(FetchLog).order_by(desc(FetchLog.id)).first()
    )

    return {
        "total": total,
        "today_new": today_new,
        "by_source": {s: c for s, c in by_source},
        "last_fetch": {
            "status": last_log.status if last_log else None,
            "finished_at": last_log.finished_at if last_log else None,
            "papers_new": last_log.papers_new if last_log else 0,
        }
        if last_log
        else None,
    }


# ============================================================
# 配置管理 API
# ============================================================
@router.post("/api/v1/config/keywords")
async def api_add_keyword(keyword: str = Query(...)):
    """添加关键词。"""
    config = load_config(reload=True)
    keyword = keyword.strip()
    if keyword and keyword not in config.keywords:
        config.keywords.append(keyword)
        save_config(config)
    return {"keywords": config.keywords}


@router.delete("/api/v1/config/keywords/{keyword}")
async def api_delete_keyword(keyword: str):
    """删除关键词。"""
    config = load_config(reload=True)
    config.keywords = [k for k in config.keywords if k.strip() != keyword]
    save_config(config)
    return {"keywords": config.keywords}


@router.post("/api/v1/config/authors")
async def api_add_author(
    author: str = Query(...), group: int = Query(1)
):
    """添加高亮作者。group=1 国际, group=2 国内。"""
    config = load_config(reload=True)
    author = author.strip()
    if author:
        if group == 1 and author not in config.highlight_authors_1:
            config.highlight_authors_1.append(author)
        elif group == 2 and author not in config.highlight_authors_2:
            config.highlight_authors_2.append(author)
        save_config(config)
    return {
        "highlight_authors_1": config.highlight_authors_1,
        "highlight_authors_2": config.highlight_authors_2,
    }


@router.delete("/api/v1/config/authors/{author}")
async def api_delete_author(author: str, group: int = Query(1)):
    """删除高亮作者。"""
    config = load_config(reload=True)
    if group == 1:
        config.highlight_authors_1 = [
            a for a in config.highlight_authors_1 if a != author
        ]
    else:
        config.highlight_authors_2 = [
            a for a in config.highlight_authors_2 if a != author
        ]
    save_config(config)
    return {
        "highlight_authors_1": config.highlight_authors_1,
        "highlight_authors_2": config.highlight_authors_2,
    }


@router.post("/api/v1/config/reload")
async def api_reload_config():
    """重新加载配置文件。"""
    config = load_config(reload=True)
    return {"status": "ok", "keywords_count": len(config.keywords)}
