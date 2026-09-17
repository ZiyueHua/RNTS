"""报告生成 —— 生成 Markdown 格式的产物报告（近一月更新 / 近一年作者精选）。

把原本写在 routes.py 报告路由里的生成逻辑抽成本模块，
供「Web 下载 API」与「调度器每日自动落盘」两处复用，避免重复代码。
"""

from datetime import datetime, timezone, timedelta
from sqlalchemy import desc
import html
import re
import shutil

from app import __version__
from app.models import Paper
from app.config import load_config
from app.filters import classify_papers


def _fmt_paper_compact(p, source_names, index=None):
    """精简版论文格式化，用于报告。

    接受 dict（classify_papers 的结果），字段：title/link/authors/
    date_added/source/summary/matched_keywords。
    """
    date_str = p.get("date_added", "")[:10] if p.get("date_added") else ""
    source_disp = source_names.get(p.get("source"), p.get("source"))

    keywords = [f"`{kw}`" for kw in p.get("matched_keywords", [])]

    prefix = f"{index}. " if index else "### "
    lines = []
    lines.append(f"{prefix}**[{p.get('title')}]({p.get('link')})**")
    lines.append("")
    # 行尾两个空格是 Markdown 硬换行：html 渲染器已改为每行一个 <p>，
    # 但 md 报告要靠这两个空格，四项才会各占一行（否则会被合并成一段）。
    lines.append(f"**作者**: {p.get('authors') or '未知'}  ")
    lines.append(f"**日期**: {date_str}  ")
    lines.append(f"**来源**: {p.get('source')}  ")
    lines.append(f"**关键词**: " + (" ".join(keywords) if keywords else "无"))
    lines.append("")
    if p.get("summary"):
        lines.append("> " + p.get("summary").replace("\n", "\n> "))
        lines.append("")
    return "\n".join(lines)


def _load_classified_in_window(db, cutoff):
    """取时间窗内全部原始词条，并用【当前】配置实时判定命中。

    返回 list[dict]（classify_papers 结果），按 date_added 降序。
    命中判定在展示时完成，因此加/删关键词、加/删作者对历史数据 retroactive 生效。
    """
    config = load_config()
    rows = (
        db.query(Paper)
        .filter(Paper.date_added >= cutoff)
        .order_by(desc(Paper.date_added))
        .all()
    )
    items = [r.to_dict() for r in rows]
    return classify_papers(
        items,
        config.keywords,
        config.highlight_authors_1,
        config.highlight_authors_2,
    )


def build_monthly_report(db) -> str:
    """近一月更新报告。

    取 date_added 在最近 30 天内的【全部原始词条】，用当前配置实时筛选命中项，
    按日期降序排列（最新在前），不分组。加/删关键词后次日报告即 retroactive 正确。
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=30)).isoformat()

    classified = _load_classified_in_window(db, cutoff)
    papers = [c for c in classified if c["is_match"]]

    config = load_config()
    source_names = {s.name: s.display_name for s in config.rss_sources}

    date_from = cutoff[:10]
    date_to = now.strftime("%Y-%m-%d")

    md = ["# 量子物理学术新闻 — 近一月更新\n"]
    md.append(f"> **生成时间**: {now.strftime('%Y-%m-%d %H:%M')}  ")
    md.append(f"> **时间范围**: {date_from} ~ {date_to}  ")
    md.append(f"> **命中论文数（当前配置）**: {len(papers)} 篇  ")
    md.append(f"> **原始词条数（同窗口）**: {len(classified)} 篇  ")
    md.append("\n---\n\n")

    if not papers:
        md.append("*该时间段内暂无命中论文*\n")
    else:
        # 按日期分天列出
        current_date = ""
        for p in papers:
            pdate = p.get("date_added", "")[:10] if p.get("date_added") else "未知日期"
            if pdate != current_date:
                if current_date:
                    md.append("\n---\n")
                md.append(f"## {pdate}\n")
                current_date = pdate

            md.append(_fmt_paper_compact(p, source_names))
            md.append("")

    # 汇总统计
    md.append("\n---\n\n## 📊 汇总统计\n\n")
    md.append("| 来源 | 论文数 |\n|------|--------|")
    source_count = {}
    for p in papers:
        src = p.get("source")
        source_count[src] = source_count.get(src, 0) + 1
    for src, cnt in sorted(source_count.items(), key=lambda x: -x[1]):
        md.append(f"| {source_names.get(src, src)} | {cnt} |")
    md.append("")
    md.append(f"| **合计** | **{len(papers)}** |")
    md.append("")

    md.append("\n---\n")
    md.append(f"\n*RNTS v{__version__} — Research News Tracking System for Quantum Physics*\n")

    return "\n".join(md)


def build_authors_report(db) -> str:
    """近一年作者精选报告。

    取 date_added 在最近 365 天内的【全部原始词条】，用当前配置实时筛选
    作者命中项（author_group != 0），按当前高亮作者分组。
    同一篇论文可出现在多位高亮作者的区块下。加/删作者后次日报告即 retroactive 正确。
    """
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=365)).isoformat()

    classified = _load_classified_in_window(db, cutoff)
    papers = [c for c in classified if c["author_group"] != 0]

    config = load_config()
    source_names = {s.name: s.display_name for s in config.rss_sources}

    date_from = cutoff[:10]
    date_to = now.strftime("%Y-%m-%d")

    md = ["# 量子物理学术新闻 — 近一年作者精选\n"]
    md.append(f"> **生成时间**: {now.strftime('%Y-%m-%d %H:%M')}  ")
    md.append(f"> **时间范围**: {date_from} ~ {date_to}  ")
    md.append(f"> **作者匹配论文数（当前配置）**: {len(papers)} 篇  ")
    md.append(f"> **国际高亮作者**: {len(config.highlight_authors_1)} 人  ")
    md.append(f"> **国内高亮作者**: {len(config.highlight_authors_2)} 人  ")
    md.append("\n---\n\n")

    if not papers:
        md.append("*该时间段内暂无作者匹配论文*\n")
    else:
        # 国际高亮作者区块
        md.append("## 🌐 国际高亮作者\n\n")
        intl_total = 0
        for author in config.highlight_authors_1:
            author_papers = [
                p for p in papers if author in p.get("matched_authors", [])
            ]
            if not author_papers:
                continue
            intl_total += len(author_papers)
            md.append(f"### {author} ({len(author_papers)} 篇)\n")
            for i, p in enumerate(author_papers, 1):
                md.append(_fmt_paper_compact(p, source_names, index=i))
                md.append("")
            md.append("---\n\n")

        if intl_total == 0:
            md.append("*暂无国际高亮作者命中*\n\n---\n\n")

        # 国内高亮作者区块
        md.append("## 🇨🇳 国内高亮作者\n\n")
        cn_total = 0
        for author in config.highlight_authors_2:
            author_papers = [
                p for p in papers if author in p.get("matched_authors", [])
            ]
            if not author_papers:
                continue
            cn_total += len(author_papers)
            md.append(f"### {author} ({len(author_papers)} 篇)\n")
            for i, p in enumerate(author_papers, 1):
                md.append(_fmt_paper_compact(p, source_names, index=i))
                md.append("")
            md.append("---\n\n")

        if cn_total == 0:
            md.append("*暂无国内高亮作者命中*\n\n---\n\n")

    # 汇总统计
    md.append("## 📊 汇总统计\n\n")
    md.append("| 分组 | 命中论文数 |\n|------|-----------|")
    md.append(f"| 🌐 国际高亮作者 | {intl_total if papers else 0} |")
    md.append(f"| 🇨🇳 国内高亮作者 | {cn_total if papers else 0} |")
    md.append(f"| **作者匹配论文总计** | **{len(papers)}** |")
    md.append("")

    md.append("\n---\n")
    md.append(f"\n*RNTS v{__version__} — Research News Tracking System for Quantum Physics*\n")

    return "\n".join(md)


# 固定文件名（覆盖式更新，作为稳定产物）
MONTHLY_FILENAME = "RNTS_近一月更新报告.md"
AUTHORS_FILENAME = "RNTS_近一年作者精选报告.md"
MONTHLY_HTML = "RNTS_近一月更新报告.html"
AUTHORS_HTML = "RNTS_近一年作者精选报告.html"


def generate_report_artifacts(db=None) -> dict:
    """生成两份 Markdown 报告并落盘到 data/reports/。

    返回各文件绝对路径。调用方负责传入或创建 Session。
    """
    import os

    own_session = False
    if db is None:
        from app.database import SessionLocal
        db = SessionLocal()
        own_session = True

    try:
        # 定位 data/reports 目录（项目根下的 data/reports）
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out_dir = os.path.join(base_dir, "data", "reports")
        os.makedirs(out_dir, exist_ok=True)

        monthly = build_monthly_report(db)
        authors = build_authors_report(db)

        monthly_path = os.path.join(out_dir, MONTHLY_FILENAME)
        authors_path = os.path.join(out_dir, AUTHORS_FILENAME)
        monthly_html_path = os.path.join(out_dir, MONTHLY_HTML)
        authors_html_path = os.path.join(out_dir, AUTHORS_HTML)

        with open(monthly_path, "w", encoding="utf-8") as f:
            f.write(monthly)
        with open(authors_path, "w", encoding="utf-8") as f:
            f.write(authors)

        # 同步生成 HTML 版本（零依赖），便于手机端直接预览
        monthly_html = build_html_report(monthly, "量子物理学术新闻 — 近一月更新")
        authors_html = build_html_report(authors, "量子物理学术新闻 — 近一年作者精选")
        with open(monthly_html_path, "w", encoding="utf-8") as f:
            f.write(monthly_html)
        with open(authors_html_path, "w", encoding="utf-8") as f:
            f.write(authors_html)

        # 云盘同步（坚果云等）：把报告复制到同步目录，手机端用云盘 App 打开。
        # 放在 try/except 中，任何失败只记录、不影响报告生成与返回。
        sync_extra: dict = {}
        try:
            cfg = load_config()
            cs = cfg.cloud_sync
            if getattr(cs, "enabled", False):
                target = (getattr(cs, "dir", "") or "").strip()
                if not target:
                    # 用户启用了云同步但没填目录：跳过，不影响报告生成
                    sync_extra = {
                        "cloud_sync_error": (
                            "云同步已启用但未设置 cloud_sync.dir（同步目录），本次跳过同步"
                        )
                    }
                else:
                    target_dir = os.path.abspath(os.path.expanduser(target))
                    os.makedirs(target_dir, exist_ok=True)
                    fmts = getattr(cs, "formats", ["html"])
                    src_by_fmt = {
                        "html": [monthly_html_path, authors_html_path],
                        "md": [monthly_path, authors_path],
                    }
                    synced = []
                    for fmt in fmts:
                        for src in src_by_fmt.get(fmt, []):
                            if src and os.path.exists(src):
                                dst = os.path.join(target_dir, os.path.basename(src))
                                shutil.copy2(src, dst)
                                synced.append(os.path.basename(src))
                    sync_extra = {"cloud_sync_dir": target_dir, "cloud_sync_files": synced}
        except Exception as e:  # noqa: BLE001
            sync_extra = {"cloud_sync_error": str(e)}

        res = {
            "monthly": monthly_path,
            "authors": authors_path,
            "monthly_html": monthly_html_path,
            "authors_html": authors_html_path,
            "monthly_bytes": len(monthly.encode("utf-8")),
            "authors_bytes": len(authors.encode("utf-8")),
            "status": "success",
        }
        res.update(sync_extra)
        return res
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        if own_session:
            db.close()


# ---------------------------------------------------------------------------
# Markdown -> HTML（零依赖，覆盖报告用到的固定语法子集）
# 仅用于生成可在手机端直接预览的 .html 产物；.md 仍保留供桌面端下载。
# ---------------------------------------------------------------------------
_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { width: min(92vw, 1600px); margin: 0 auto; padding: 24px 18px 60px;
       font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
         Roboto, "Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans GB",
         "Microsoft YaHei", sans-serif;
       line-height: 1.7; color: #1f2933; background: #ffffff; }
h1 { font-family: inherit; font-size: 1.6rem; font-weight: 700;
     letter-spacing: .01em; border-bottom: 2px solid #2563eb; padding-bottom: .4em; }
h2 { font-family: inherit; font-size: 1.25rem; font-weight: 600; margin-top: 2em; color: #0f172a; }
h3 { font-family: inherit; font-size: 1.05rem; font-weight: 600; margin-top: 1.4em; color: #0f172a; }
blockquote { margin: .6em 0; padding: .4em .9em; background: #f1f5f9;
             border-left: 4px solid #2563eb; color: #475569; border-radius: 4px; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: .92rem; }
th, td { border: 1px solid #d8dee9; padding: 6px 10px; text-align: left; }
th { background: #f8fafc; }
tr:nth-child(even) td { background: #fbfcfe; }
a { color: #2563eb; text-decoration: none; word-break: break-all; }
a:hover { text-decoration: underline; }
code { background: #eef2f7; padding: .1em .4em; border-radius: 4px;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        font-size: .88em; }
hr { border: none; border-top: 1px solid #e2e8f0; margin: 1.6em 0; }
ul, ol { padding-left: 1.4em; }
.meta { color: #64748b; font-size: .9rem; background: #f8fafc;
        padding: 10px 14px; border-radius: 8px; margin-bottom: 1.2em; }
.meta p { margin: .2em 0; }
.foot { margin-top: 2em; color: #94a3b8; font-size: .82rem; text-align: center; }
"""


def _md_inline(text: str) -> str:
    """行内 Markdown：`**粗体**`、`` `代码` ``、`[文字](链接)`。

    先转义 HTML 特殊字符，再还原上述行内语法（链接/代码需保留真实字符）。
    """
    # 保护链接与代码，避免其中内容被误转义
    tokens = []

    def _stash(s):
        tokens.append(s)
        return f"\x00{len(tokens) - 1}\x00"

    # 链接 [text](url)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: _stash(f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>'),
        text,
    )
    # 行内代码 `code`
    text = re.sub(r"`([^`]+)`", lambda m: _stash(f"<code>{html.escape(m.group(1))}</code>"), text)
    # 转义其余 HTML
    text = html.escape(text)
    # 粗体 **x**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # 还原占位符
    text = re.sub(r"\x00(\d+)\x00", lambda m: tokens[int(m.group(1))], text)
    return text


def md_to_html(md: str, title: str = "") -> str:
    """将报告使用的 Markdown 子集转换为完整自包含 HTML 文档。"""
    lines = md.split("\n")
    out = []
    i = 0
    n = len(lines)
    in_table = False

    def _close_table():
        nonlocal in_table
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    while i < n:
        line = lines[i]

        # 表格：以 | 开头且下一行是分隔行
        if line.strip().startswith("|") and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]):
            _close_table()
            header_cells = [c.strip() for c in line.strip().strip("|").split("|")]
            out.append('<table><thead><tr>')
            for c in header_cells:
                out.append(f"<th>{_md_inline(c)}</th>")
            out.append("</tr></thead><tbody>")
            i += 2  # 跳过表头与分隔行
            in_table = True
            continue

        if in_table:
            if line.strip().startswith("|") and line.strip().endswith("|"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{_md_inline(c)}</td>" for c in cells) + "</tr>")
                i += 1
                continue
            else:
                _close_table()

        # 水平线
        if re.match(r"^\s*---\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_md_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        # 引用块（连续 > 行）
        if line.lstrip().startswith(">"):
            quote = []
            while i < n and lines[i].lstrip().startswith(">"):
                quote.append(_md_inline(lines[i].lstrip()[1:].strip()))
                i += 1
            out.append('<blockquote>' + "<br>".join(quote) + "</blockquote>")
            continue

        # 空行
        if not line.strip():
            i += 1
            continue

        # 普通段落
        # 每一行单独处理，避免作者、日期、来源等信息被合并
        if line.strip():
            out.append("<p>" + _md_inline(line) + "</p>")
            i += 1
            continue
        else:
            # 兜底：若本行未被任何分支消费（例如孤立的 | 行），
            # 必须推进 i，避免主循环死循环。
            i += 1

    _close_table()

    body_html = "\n".join(out)
    page_title = title or "RNTS 报告"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(page_title)}</title>
<style>{_CSS}</style>
</head>
<body>
{body_html}
<div class="foot">RNTS v{__version__} — Research News Tracking System for Quantum Physics</div>
</body>
</html>"""


def build_html_report(md: str, title: str) -> str:
    """由 Markdown 报告生成 HTML 文档字符串。"""
    return md_to_html(md, title=title)
