"""RSS 抓取模块 —— 异步并发抓取多个学术期刊 RSS 源。

相比原系统的改进：
1. 异步并发（httpx + asyncio）替代串行 feedparser.parse(url)，12 源从 ~60s 降到 ~8s
2. 修复 Science 源 summary='unknown'：尝试从 description/content 提取
3. 修复 APL 源 authors='unknown'：尝试从 entry.author 提取
4. 统一日期格式：用 feedparser 的 published_parsed 转 ISO 8601
5. Nature summary 正则提取增加 try-except 回退
6. Nature 系列：RSS 摘要不可靠时（如 npj QI 只给标题），回退抓取论文页面 meta description
"""

import os
import re
import time
import asyncio
import logging
import ssl
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import httpx
import feedparser

logger = logging.getLogger(__name__)


# ============================================================
# 日期解析工具
# ============================================================
def parse_entry_date(entry) -> str:
    """从 feedparser entry 解析日期，统一输出 ISO 8601 格式。

    feedparser 的 published_parsed / updated_parsed 返回 time.struct_time。
    """
    import time

    tp = entry.get("published_parsed") or entry.get("updated_parsed")
    if tp:
        try:
            dt = datetime(*tp[:6], tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            pass
    # 回退：尝试原始字符串
    raw = entry.get("published") or entry.get("updated")
    if raw:
        return raw
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# HTML 清理工具
# ============================================================
def strip_html(text: str) -> str:
    """去除 HTML 标签，保留纯文本。"""
    if not text:
        return ""
    from bs4 import BeautifulSoup

    return BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)


def extract_text_after_p(field: str) -> str:
    """原系统 Nature 提取逻辑：提取 </p> 之后的文本。增加容错。"""
    if not field:
        return ""
    try:
        pattern = r"</p>(.*?)$"
        match = re.search(pattern, field, re.DOTALL)
        if match:
            return strip_html(match.group(1))
    except Exception:
        pass
    return strip_html(field)


def extract_text_between_p(field: str) -> str:
    """原系统 PRL 提取逻辑：提取 <p></p> 之间的文本。增加容错。"""
    if not field:
        return ""
    try:
        pattern = r"<p>(.*?)</p>"
        match = re.search(pattern, field, re.DOTALL)
        if match:
            return strip_html(match.group(1))
    except Exception:
        pass
    return strip_html(field)


# ============================================================
# meta description 抓取（Nature 系列回退方案）
# ============================================================
async def fetch_meta_description(client: httpx.AsyncClient, url: str) -> str:
    """访问论文页面，从 <meta name="description"> 标签提取摘要。

    用于 Nature 系列 RSS 摘要不可靠时的回退（如 npj QI 的 RSS 只给标题）。

    已验证：
    - 不需要登录、不需要 CAPTCHA
    - 即使论文正文被 paywall，meta description 始终公开可见
    - Nature 所有子刊论文页面都有此标签
    """
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(resp.text, "html.parser")

        # 优先 meta name="description"
        meta = soup.find("meta", {"name": "description"})
        if meta and meta.get("content"):
            return meta["content"].strip()

        # 回退 og:description
        og = soup.find("meta", {"property": "og:description"})
        if og and og.get("content"):
            return og["content"].strip()

        # 回退页面内 Abstract 段落
        abs_div = soup.find("div", {"id": "Abs1-content"})
        if abs_div:
            return abs_div.get_text(strip=True)

    except Exception as e:
        logger.warning(f"抓取 meta description 失败 ({url}): {e}")

    return ""


async def fetch_crossref_abstract(client: httpx.AsyncClient, doi: str) -> str:
    """通过 Crossref API 查询 DOI 获取论文摘要。

    用于 APS 系列（PRL/PRApplied/PRX/PRX Quantum）的摘要补全：
    - APS 的 RSS 把摘要截断到 300 字符（末尾带 …）
    - APS 的论文页面（journals.aps.org）对服务器请求返回 403，无法直接抓取
    - Crossref API 免费、无需 key、不受反爬限制

    摘要格式为 JATS XML（<jats:p> 标签），需清理为纯文本。
    约 60-70% 的 APS 论文在 Crossref 有摘要。

    注意：Crossref 有频率限制（无 key 约 50 req/min），用信号量控制并发。
    """
    if not doi:
        return ""
    try:
        resp = await client.get(
            f"https://api.crossref.org/works/{doi}",
            headers={"User-Agent": "RNTS/2.0 (mailto:rnts@example.com)"},
        )
        if resp.status_code == 429:
            # 被限流，等待后重试一次
            await asyncio.sleep(2)
            resp = await client.get(
                f"https://api.crossref.org/works/{doi}",
                headers={"User-Agent": "RNTS/2.0 (mailto:rnts@example.com)"},
            )
        resp.raise_for_status()
        data = resp.json().get("message", {})
        abstract = data.get("abstract", "")
        if abstract:
            return strip_html(abstract)
    except Exception as e:
        logger.warning(f"Crossref 查询失败 (DOI:{doi}): {e}")

    return ""


# 注意：信号量在 fetch_single_source 中按需创建，避免模块级初始化问题

async def fetch_semantic_scholar_abstract(
    client: httpx.AsyncClient, doi: str
) -> str:
    """通过 Semantic Scholar API 查询 DOI 获取论文摘要。

    作为 Crossref 的二级回退：
    - Crossref 对 APS 短链 DOI 经常没有 abstract 字段
    - Semantic Scholar 有独立的摘要来源，覆盖部分 Crossref 没有的论文
    - 免费无需 key，但有限流（约 100 req/5min）

    注意：新发表的论文可能 Semantic Scholar 尚未收录摘要。
    """
    if not doi:
        return ""
    try:
        resp = await client.get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            params={"fields": "title,abstract"},
            headers={"User-Agent": "RNTS/2.0"},
        )
        if resp.status_code == 404:
            # 新发表论文尚未被 Semantic Scholar 收录，属正常情况，静默跳过
            logger.debug(f"Semantic Scholar 未收录 (DOI:{doi})")
            return ""
        if resp.status_code == 429:
            await asyncio.sleep(3)
            resp = await client.get(
                f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                params={"fields": "title,abstract"},
                headers={"User-Agent": "RNTS/2.0"},
            )
        resp.raise_for_status()
        data = resp.json()
        abstract = data.get("abstract", "")
        if abstract:
            return abstract.strip()
    except Exception as e:
        logger.warning(f"Semantic Scholar 查询失败 (DOI:{doi}): {e}")

    return ""


async def fetch_abstract_multi_fallback(
    client: httpx.AsyncClient, doi: str, semaphore: asyncio.Semaphore
) -> str:
    """多级回退获取摘要：Crossref → Semantic Scholar。

    用于 APS 系列摘要补全。两个 API 都是免费、无需 key。
    Crossref 覆盖率约 30%，Semantic Scholar 覆盖约 20%，
    合计覆盖约 40-50% 的被截断论文。
    """
    async with semaphore:
        # 第一级：Crossref
        abstract = await fetch_crossref_abstract(client, doi)
        if abstract and len(abstract) > 50:
            return abstract

        # 第二级：Semantic Scholar
        await asyncio.sleep(0.5)  # 避免触发限流
        abstract = await fetch_semantic_scholar_abstract(client, doi)
        if abstract and len(abstract) > 50:
            return abstract

    return ""


def is_summary_valid(summary: str, title: str) -> bool:
    """判断 RSS 提取的摘要是否有效。

    判定无效的情况：
    - 摘要为空
    - 摘要=标题（npj QI 的问题）
    - 摘要末尾有省略号 …（APS 系列被 RSS 截断到 300 字符）
    - 摘要过短（<50 字符）
    """
    if not summary or not summary.strip():
        return False
    # 去除空格和标点后比较
    s1 = re.sub(r"\s+", "", summary.lower().strip())
    s2 = re.sub(r"\s+", "", title.lower().strip())
    if s1 == s2:
        return False
    # 摘要末尾有省略号，说明被 RSS 截断
    if summary.rstrip().endswith("…") or summary.rstrip().endswith("..."):
        return False
    # 摘要太短（少于 50 字符）也判定为可疑
    if len(summary.strip()) < 50:
        return False
    return True


def extract_doi_from_entry(entry) -> str:
    """从 feedparser entry 提取 DOI。"""
    # APS 系列：dc_identifier 格式为 "doi:10.1103/xxx"
    doi = entry.get("dc_identifier", "").replace("doi:", "").strip()
    if doi:
        return doi
    # 尝试从 prism_doi 字段
    doi = entry.get("prism_doi", "").strip()
    if doi:
        return doi
    # 尝试从 link URL 提取
    link = entry.get("link", "") or entry.get("prism_url", "")
    return extract_doi_from_url(link)


def extract_doi_from_url(url: str) -> str:
    """从论文页面 URL 中提取 DOI。"""
    if not url:
        return ""
    # nature 的 link 格式: https://www.nature.com/articles/s41534-026-01333-9
    match = re.search(r"/articles/(s\d+[-\w]+)", url)
    if match:
        return f"10.1038/{match.group(1)}"
    # APS 的 link 格式: http://link.aps.org/doi/10.1103/xxx
    match = re.search(r"/doi/(10\.\d+/[^\s]+)", url)
    if match:
        return match.group(1)
    return ""


def extract_doi_from_entry_simple(url: str) -> str:
    """从 URL 提取 DOI 的简化接口（供 fetch_single_source 调用）。"""
    return extract_doi_from_url(url)


# ============================================================
# 5 种提取器 —— 每个源的 entry -> dict 映射
# ============================================================
def extract_arxiv(entry, source_name: str, source_url: str) -> dict:
    """arXiv: entry.title, entry.link, entry.summary, authors 列表。"""
    author_names = [a.get("name", "") for a in entry.get("authors", [])]
    return {
        "title": strip_html(entry.get("title", "")),
        "link": entry.get("link", ""),
        "summary": strip_html(entry.get("summary", "")),
        "authors": ", ".join(author_names),
        "date_added": parse_entry_date(entry),
        "source": source_name,
        "source_url": source_url,
    }


def extract_nature(entry, source_name: str, source_url: str) -> dict:
    """Nature 系列: prism_url + 正则提取摘要。

    注意：npj QI 的 RSS summary 在 </p> 后只给标题，不给真正摘要。
    这里先用 RSS 提取，后续在 fetch_single_source 中检测到摘要无效时
    会回退抓取论文页面的 meta description。
    """
    authors_str = ""
    authors = entry.get("authors")
    if authors:
        author_names = [a.get("name", "") for a in authors]
        authors_str = ", ".join(author_names)

    summary_raw = entry.get("summary", "") or entry.get("description", "")
    summary = extract_text_after_p(summary_raw)

    title = strip_html(entry.get("title", ""))
    link = entry.get("prism_url") or entry.get("link", "")

    # 标记摘要是否可疑（供后续回退抓取判断）
    needs_meta_fetch = not is_summary_valid(summary, title)

    return {
        "title": title,
        "link": link,
        "summary": summary,
        "authors": authors_str,
        "date_added": parse_entry_date(entry),
        "source": source_name,
        "source_url": source_url,
        "_needs_meta_fetch": needs_meta_fetch,  # 内部标记
    }


def extract_science(entry, source_name: str, source_url: str) -> dict:
    """Science 系列: 原代码 summary='unknown'，修复为尝试提取。"""
    # 尝试多个字段提取摘要
    summary = ""
    for field in ["description", "summary"]:
        val = entry.get(field)
        if val:
            summary = strip_html(val)
            break
    if not summary:
        # 尝试 content 字段
        content = entry.get("content", [])
        if content and isinstance(content, list) and len(content) > 0:
            summary = strip_html(content[0].get("value", ""))

    return {
        "title": strip_html(entry.get("title", "")),
        "link": entry.get("prism_url") or entry.get("link", ""),
        "summary": summary,  # 修复：原为 'unknown'
        "authors": entry.get("author", ""),
        "date_added": parse_entry_date(entry),
        "source": source_name,
        "source_url": source_url,
    }


def extract_prl(entry, source_name: str, source_url: str) -> dict:
    """APS 系列 (PRL/PRApplied/PRX/PRX-Quantum): <p>之间提取摘要。

    问题：APS 的 RSS 把摘要截断到 300 字符（末尾带 …）。
    标记需要回退的论文，后续在 fetch_single_source 中用 Crossref API 补全。
    """
    summary_raw = entry.get("summary", "") or entry.get("description", "")
    summary = extract_text_between_p(summary_raw)

    title = strip_html(entry.get("title", ""))
    doi = extract_doi_from_entry(entry)

    # APS RSS 摘要被截断到 300 字符，末尾带 …，需要 Crossref 补全
    needs_meta_fetch = not is_summary_valid(summary, title)

    return {
        "title": title,
        "link": entry.get("prism_url") or entry.get("link", ""),
        "summary": summary,
        "authors": entry.get("author", ""),
        "date_added": parse_entry_date(entry),
        "source": source_name,
        "source_url": source_url,
        "_needs_meta_fetch": needs_meta_fetch,
        "_doi": doi,
    }


def extract_apl(entry, source_name: str, source_url: str) -> dict:
    """APL: 原代码 authors='unknown'，修复为尝试提取。"""
    # 尝试提取作者
    authors = ""
    if entry.get("author"):
        authors = entry.get("author")
    elif entry.get("authors"):
        author_names = [a.get("name", "") for a in entry.get("authors", [])]
        authors = ", ".join(author_names)

    return {
        "title": strip_html(entry.get("title", "")),
        "link": entry.get("link", ""),
        "summary": strip_html(entry.get("summary", "")),
        "authors": authors,  # 修复：原为 'unknown'
        "date_added": parse_entry_date(entry),
        "source": source_name,
        "source_url": source_url,
    }


# 提取器注册表
EXTRACTORS: dict[str, Callable] = {
    "arxiv": extract_arxiv,
    "nature": extract_nature,
    "science": extract_science,
    "prl": extract_prl,
    "apl": extract_apl,
}


# ============================================================
# 传输层回退 —— 重试 + HTTP/3（QUIC）
# ============================================================
# httpx（TCP+TLS）请求失败后的重试等待秒数
RETRY_DELAYS = (2, 5)


async def fetch_via_http3(url: str, timeout: float = 30.0) -> str:
    """用 QUIC/HTTP3 抓取 URL 正文，返回响应文本。

    用途：部分网络环境（防火墙）会对 TCP+TLS 握手中的 SNI 域名做阻断
    （连接被立即重置，httpx 报空 ConnectError），但 QUIC（UDP 443）通道
    不受影响。实测 export.arxiv.org 在此类网络下仅 HTTP/3 可达。

    aioquic 为可选依赖，未安装时抛 RuntimeError。
    """
    try:
        from aioquic.asyncio.client import connect
        from aioquic.asyncio.protocol import QuicConnectionProtocol
        from aioquic.h3.connection import H3_ALPN, H3Connection
        from aioquic.h3.events import DataReceived, HeadersReceived
        from aioquic.quic.configuration import QuicConfiguration
    except ImportError as e:
        raise RuntimeError("aioquic 未安装，无法使用 HTTP/3 回退") from e

    u = httpx.URL(url)
    host = u.host
    path = u.path
    if u.query:
        path += "?" + u.query.decode("ascii")

    class _H3Client(QuicConnectionProtocol):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._http = H3Connection(self._quic)
            self._waiter = None
            self._status = None
            self._headers: dict = {}
            self._body = b""

        async def get(self, authority: str, path: str):
            sid = self._quic.get_next_available_stream_id()
            self._http.send_headers(
                sid,
                [
                    (b":method", b"GET"),
                    (b":scheme", b"https"),
                    (b":authority", authority.encode()),
                    (b":path", path.encode()),
                    (b"user-agent",
                     b"RNTS/2.0 (Academic Paper Tracker; +https://github.com/rnts)"),
                ],
                end_stream=True,
            )
            self._waiter = asyncio.get_event_loop().create_future()
            self.transmit()
            return await asyncio.wait_for(self._waiter, timeout=timeout)

        def quic_event_received(self, event):
            for e in self._http.handle_event(event):
                if isinstance(e, HeadersReceived):
                    for k, v in e.headers:
                        if k == b":status":
                            self._status = v.decode()
                        else:
                            self._headers[k.decode("latin-1")] = v.decode("latin-1")
                elif isinstance(e, DataReceived):
                    self._body += e.data
                    if e.stream_ended and self._waiter and not self._waiter.done():
                        self._waiter.set_result(
                            (self._status, self._headers, self._body)
                        )

    conf = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN)
    # 与 httpx verify=False 保持一致（遵循现有项目的证书策略）
    conf.verify_mode = ssl.CERT_NONE

    async def _run() -> str:
        async with connect(
            host, 443, configuration=conf, create_protocol=_H3Client
        ) as client:
            status, headers, body = await client.get(host, path)
            if status == "429":
                # arXiv 对共享出口 IP 常见限流：按 Retry-After 等待后在同连接重试一次
                delay = 10
                ra = headers.get("retry-after", "")
                if ra.isdigit():
                    delay = max(3, min(int(ra), 60))
                logger.info(f"HTTP/3 收到 429 限流，等待 {delay}s 后重试")
                await asyncio.sleep(delay)
                status, headers, body = await client.get(host, path)
            if status != "200":
                raise httpx.HTTPStatusError(
                    f"HTTP/3 响应状态码 {status}",
                    request=httpx.Request("GET", url),
                    response=httpx.Response(int(status), content=body),
                )
            return body.decode("utf-8", errors="replace")

    return await asyncio.wait_for(_run(), timeout=timeout + 90)


async def fetch_source_text(
    client: httpx.AsyncClient,
    name: str,
    url: str,
    timeout: float | None = None,
) -> str:
    """抓取源 URL 的响应正文：httpx 重试若干次，全部失败后尝试 HTTP/3。

    重试间隔见 RETRY_DELAYS（arXiv API 要求请求间隔 ≥3 秒，末次间隔取 5s）。
    所有途径均失败时抛出最后一次 httpx 异常。

    timeout 为空时沿用 client 自身的超时设置；arXiv 时间窗查询响应体更大，
    会显式传入更宽的超时。
    """
    last_exc: Exception | None = None
    for attempt in range(len(RETRY_DELAYS) + 1):
        if attempt:
            await asyncio.sleep(RETRY_DELAYS[attempt - 1])
        try:
            kwargs: dict = {"follow_redirects": True}
            if timeout is not None:
                kwargs["timeout"] = timeout
            response = await client.get(url, **kwargs)
            response.raise_for_status()
            return response.text
        except Exception as e:
            last_exc = e
            # httpx 传输层异常的 str() 常为空串，必须带上异常类型与 repr
            logger.warning(
                f"[{name}] 第 {attempt + 1} 次请求失败: {type(e).__name__}: {e!r}"
            )

    logger.info(f"[{name}] 直连失败，尝试 HTTP/3（QUIC）回退")
    # CGNAT 共享出口 IP 容易连带触发 arXiv 的 429 限流，多试几次拉开间隔
    for h3_attempt in range(3):
        if h3_attempt:
            await asyncio.sleep(10)
        try:
            if timeout is not None:
                h3_timeout = timeout
            else:
                h3_timeout = (
                    client.timeout.connect
                    if hasattr(client.timeout, "connect")
                    else 30
                )
            text = await fetch_via_http3(url, timeout=h3_timeout)
            logger.info(f"[{name}] HTTP/3 回退抓取成功")
            return text
        except Exception as e:
            logger.warning(
                f"[{name}] HTTP/3 回退第 {h3_attempt + 1} 次失败: "
                f"{type(e).__name__}: {e!r}"
            )
    assert last_exc is not None
    raise last_exc


# ============================================================
# 本地缓存兜底 —— arXiv 限流惩罚可能持续较久，缓存保证报告不断档
# ============================================================
CACHE_MAX_AGE_HOURS = 48


def _cache_path(name: str) -> str:
    return os.path.join("data", "cache", f"{name}.atom")


def _save_cache(name: str, text: str) -> None:
    try:
        os.makedirs(os.path.dirname(_cache_path(name)), exist_ok=True)
        with open(_cache_path(name), "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        logger.warning(f"[{name}] 缓存写入失败: {e}")


def _load_fresh_cache(name: str) -> str:
    """返回 max age 内的缓存正文，过期/不存在返回空串。"""
    try:
        p = _cache_path(name)
        age_h = (time.time() - os.path.getmtime(p)) / 3600
        if age_h <= CACHE_MAX_AGE_HOURS:
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    except OSError:
        pass
    return ""


# ============================================================
# 异步抓取
# ============================================================
async def _extract_papers(
    client: httpx.AsyncClient,
    name: str,
    url: str,
    extractor_name: str,
    text: str,
) -> list[dict]:
    """解析源正文并提取论文列表，含按提取器类型的摘要回退补全。"""
    extractor = EXTRACTORS.get(extractor_name, extract_arxiv)
    feed = feedparser.parse(text)
    papers = []
    for entry in feed.entries:
        try:
            paper = extractor(entry, name, url)
            if paper["title"] and paper["link"]:
                papers.append(paper)
        except Exception as e:
            logger.warning(f"[{name}] 提取条目失败: {e}")

    # 对需要回退抓取的论文，根据提取器类型选择策略
    needs_fetch = [p for p in papers if p.get("_needs_meta_fetch")]
    if needs_fetch:
        if extractor_name == "prl":
            # APS 系列：用 Crossref + Semantic Scholar 多级回退（页面 403 无法直接抓取）
            logger.info(f"[{name}] {len(needs_fetch)} 篇摘要被截断，用多级 API 回退补全")
            # 为没有 DOI 的论文尝试从 link 提取
            for p in needs_fetch:
                if not p.get("_doi"):
                    p["_doi"] = extract_doi_from_entry_simple(p.get("link", ""))

            # 并发限流：最多 2 并发，避免 429
            api_sem = asyncio.Semaphore(2)
            tasks = [
                fetch_abstract_multi_fallback(client, p.get("_doi", ""), api_sem)
                for p in needs_fetch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for paper, abstract in zip(needs_fetch, results):
                if isinstance(abstract, str) and abstract.strip():
                    paper["summary"] = abstract.strip()
                    logger.info(f"[{name}] API 补全成功: {paper['title'][:50]}")
                else:
                    # API 也没摘要，保留 RSS 的截断摘要（比没有好）
                    logger.info(f"[{name}] API 无摘要，保留 RSS 截断版: {paper['title'][:50]}")
                paper.pop("_needs_meta_fetch", None)
                paper.pop("_doi", None)

        elif extractor_name == "nature":
            # Nature 系列：抓取论文页面 meta description
            logger.info(f"[{name}] {len(needs_fetch)} 篇摘要无效，抓取 meta description")
            meta_tasks = [fetch_meta_description(client, p["link"]) for p in needs_fetch]
            meta_results = await asyncio.gather(*meta_tasks, return_exceptions=True)

            for paper, meta_summary in zip(needs_fetch, meta_results):
                if isinstance(meta_summary, str) and meta_summary.strip():
                    paper["summary"] = meta_summary.strip()
                    logger.info(f"[{name}] meta description 补充成功: {paper['title'][:50]}")
                paper.pop("_needs_meta_fetch", None)
        else:
            for p in needs_fetch:
                p.pop("_needs_meta_fetch", None)
    else:
        # 清除所有内部标记
        for p in papers:
            p.pop("_needs_meta_fetch", None)
            p.pop("_doi", None)

    logger.info(f"[{name}] 抓取到 {len(papers)} 篇")
    return papers


# ============================================================
# arXiv 时间窗抓取 —— 按 submittedDate 区间取全量
# ============================================================
# 原实现把 start=0&max_results=50 写死在 config.yaml 的 URL 里，quant-ph 每日
# 新提交常超过 50 篇，多出的条目被静默丢弃。这里改为按提交日期区间查询并翻页，
# 取回窗口内全部条目。
#
# arXiv API 单次请求的 max_results 上限为 2000，超出需用 start 翻页。
ARXIV_PAGE_SIZE = 2000
# 安全阀：配置写错（如窗口拉得过大）时不至于把内存拖爆。
ARXIV_MAX_PAPERS = 8000
# arXiv API 要求同源请求间隔 ≥3 秒。
ARXIV_PAGE_DELAY = 3.0
# 时间窗查询的响应体远大于普通 RSS，单独给更宽的超时。
ARXIV_REQUEST_TIMEOUT = 90.0


def build_arxiv_window_url(
    base_url: str,
    start_date: date,
    end_date: date,
    start: int = 0,
    max_results: int = ARXIV_PAGE_SIZE,
) -> str:
    """在源配置的 search_query 上追加 submittedDate 区间，生成单页查询 URL。

    - 保留配置里的分类条件（如 cat:quant-ph），网页端改分类时自动跟随；
    - 覆盖 start / max_results / 排序，保证区间内全部条目按提交日期倒序返回；
    - arXiv API 不支持括号分组，日期条件以 AND 直接追加，所以配置中的
      search_query 应保持为单一条件（多个条件请自行用 AND 连接）。
    - 若 search_query 已含 submittedDate，则不重复追加。

    Args:
        base_url: config.yaml 中 arxiv 源的 url
        start_date / end_date: 提交日期区间，含首尾，按 UTC 日历日
        start: 翻页偏移
        max_results: 本页最多返回条数
    """
    u = httpx.URL(base_url)
    params = dict(u.params)
    search_query = params.get("search_query", "cat:quant-ph").strip()
    if "submittedDate" not in search_query:
        window = (
            f"submittedDate:[{start_date.strftime('%Y%m%d')}0000"
            f" TO {end_date.strftime('%Y%m%d')}2359]"
        )
        search_query = f"{search_query} AND {window}"
    params.update(
        {
            "search_query": search_query,
            "start": str(start),
            "max_results": str(max_results),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
    )
    return str(u.copy_with(params=params))


async def fetch_arxiv_window(
    client: httpx.AsyncClient,
    source: dict,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """按 submittedDate 区间抓取 arXiv 源的全部条目（自动翻页）。

    与通用 fetch_single_source 的区别：
    - 普通 RSS 一次只能拿到固定条数，本函数循环翻页取回窗口内全量；
    - 第一页正文写入本地缓存，保留断网/限流时的 48h 兜底能力；
    - 整窗失败且一条都没抓到（例如 429 限流），回退到缓存解析。

    已入库条目由数据库 UNIQUE(link) 去重，因此窗口可以适当回溯、重复抓取无副作用。
    """
    name = source["name"]
    base_url = source["url"]
    papers: list[dict] = []
    start = 0
    total: int | None = None
    first_page_text = ""

    while start < ARXIV_MAX_PAPERS:
        url = build_arxiv_window_url(base_url, start_date, end_date, start=start)
        try:
            text = await fetch_source_text(
                client, name, url, timeout=ARXIV_REQUEST_TIMEOUT
            )
        except Exception as e:
            logger.error(
                f"[{name}] 时间窗抓取失败（start={start}）: "
                f"{type(e).__name__}: {e!r}",
                exc_info=True,
            )
            if not papers:
                cached = _load_fresh_cache(name)
                if cached:
                    logger.warning(
                        f"[{name}] 网络抓取失败，改用 {CACHE_MAX_AGE_HOURS}h "
                        f"内的本地缓存继续"
                    )
                    return await _extract_papers(
                        client, name, base_url, "arxiv", cached
                    )
            break

        if start == 0:
            first_page_text = text

        feed = feedparser.parse(text)
        if total is None:
            try:
                total = int(feed.feed.get("opensearch_totalresults") or 0)
            except (TypeError, ValueError):
                total = 0

        # 窗口内没有论文（如周末/节假日），或 XML 解析失败，都按结束处理
        if not feed.entries:
            if not papers:
                logger.info(
                    f"[{name}] 时间窗 {start_date} ~ {end_date} 内没有条目"
                )
            break

        papers.extend(
            await _extract_papers(client, name, base_url, "arxiv", text)
        )
        start += len(feed.entries)

        if total and start >= total:
            break
        await asyncio.sleep(ARXIV_PAGE_DELAY)

    # 只缓存第一页：feedparser 无法解析拼接后的多份 Atom 文档
    if first_page_text:
        _save_cache(name, first_page_text)

    logger.info(
        f"[{name}] 时间窗 {start_date} ~ {end_date} 共抓取 {len(papers)} 篇"
        f"（arXiv 报告总数 {total}）"
    )
    return papers


async def _fetch_arxiv_source(
    client: httpx.AsyncClient, source: dict
) -> list[dict]:
    """每日抓取时的 arXiv 入口：按配置的回溯天数计算滚动窗口。

    使用本地日期，窗口为 [今天 - arxiv_lookback_days, 今天]。之所以回溯而非只取
    当天：arXiv 的公告要晚于提交 1–2 天，且周一早上要覆盖上周五与周末的提交。
    """
    from app.config import load_config

    try:
        lookback = int(load_config().schedule.arxiv_lookback_days)
    except Exception:
        lookback = 3
    today = datetime.now().date()
    start_date = today - timedelta(days=max(0, lookback))
    return await fetch_arxiv_window(client, source, start_date, today)


def fetch_arxiv_window_sync(
    source: dict,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """同步包装：单源时间窗抓取（供手动补抓 API 在线程池中调用）。"""
    headers = {
        "User-Agent": "RNTS/2.0 (Academic Paper Tracker; +https://github.com/rnts)"
    }

    async def _run() -> list[dict]:
        async with httpx.AsyncClient(
            timeout=ARXIV_REQUEST_TIMEOUT, headers=headers, verify=False
        ) as client:
            return await fetch_arxiv_window(client, source, start_date, end_date)

    def _in_thread() -> list[dict]:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()

    try:
        return _in_thread()
    except RuntimeError:
        # 当前线程已有运行中的 event loop，换线程执行
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(_in_thread).result()


async def fetch_single_source(
    client: httpx.AsyncClient, source: dict
) -> list[dict]:
    """抓取单个 RSS 源，返回提取后的论文字典列表。

    arXiv 源单独走时间窗全量抓取（见 fetch_arxiv_window），其余源按配置 URL 抓取。

    摘要回退策略（根据提取器类型选择）：
    - nature: 抓取论文页面 meta description（Nature 页面可公开访问）
    - prl: 用 Crossref API 查 DOI 获取摘要（APS 页面返回 403，无法直接抓取）
    - 其他: 不回退

    网络兜底：httpx 重试 + HTTP/3 回退（见 fetch_source_text）全部失败时，
    若存在 {CACHE_MAX_AGE_HOURS}h 内的成功缓存则用缓存继续解析，
    保证 arXiv 被限流时当天报告不至于整体缺源。
    """
    name = source["name"]
    url = source["url"]
    extractor_name = source.get("extractor", "arxiv")

    # arXiv：按提交日期区间翻页取全量，不再受 max_results 截断
    if extractor_name == "arxiv":
        return await _fetch_arxiv_source(client, source)

    try:
        text = await fetch_source_text(client, name, url)
        _save_cache(name, text)
    except Exception as e:
        # 带异常类型与 repr，避免 httpx 传输层异常 str() 为空导致日志无信息
        logger.error(
            f"[{name}] 抓取失败: {type(e).__name__}: {e!r}", exc_info=True
        )
        text = _load_fresh_cache(name)
        if not text:
            return []
        logger.warning(
            f"[{name}] 网络抓取失败，使用 {CACHE_MAX_AGE_HOURS}h 内的本地缓存继续"
        )
    return await _extract_papers(client, name, url, extractor_name, text)


async def fetch_all_sources(sources: list[dict], timeout: int = 30) -> list[dict]:
    """异步并发抓取所有启用的 RSS 源。

    Args:
        sources: 启用的 RSS 源配置列表
        timeout: 单个请求超时秒数

    Returns:
        所有源的论文列表合并（未去重，去重由数据库 UNIQUE 约束处理）
    """
    headers = {
        "User-Agent": "RNTS/2.0 (Academic Paper Tracker; +https://github.com/rnts)"
    }
    async with httpx.AsyncClient(
        timeout=timeout, headers=headers, verify=False
    ) as client:
        tasks = [fetch_single_source(client, s) for s in sources]
        results = await asyncio.gather(*tasks, return_exceptions=False)

    all_papers = []
    for papers in results:
        all_papers.extend(papers)

    logger.info(f"所有源合计抓取 {len(all_papers)} 篇（去重前）")
    return all_papers


def fetch_all_sources_sync(sources: list[dict], timeout: int = 30) -> list[dict]:
    """同步包装器：在非异步上下文中调用异步抓取。

    使用 new_event_loop 而非 asyncio.run()，以兼容在已有 event loop
    的环境（如 FastAPI 线程池）中调用。
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(fetch_all_sources(sources, timeout))
        loop.close()
        return result
    except RuntimeError:
        # 如果在主线程已有 loop，用线程池跑
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(_run_in_thread, sources, timeout)
            return future.result()


def _run_in_thread(sources: list[dict], timeout: int) -> list[dict]:
    """在新线程中运行异步抓取。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(fetch_all_sources(sources, timeout))
    finally:
        loop.close()
