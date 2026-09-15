"""过滤模块 —— 关键词 + 高亮作者匹配。

相比原系统 filter_csv.py 的修复：
1. 【严重 bug】summary_flag 过滤从未生效（isinstance(summary_flag, str) 永远 False）
   → 修复为同时搜索 title 和 summary
2. 关键词带空格匹配边界问题 → 改用正则 \b 单词边界
3. 空值安全处理（title/summary/authors 可能为 None）
"""

import re
import unicodedata
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 变音字母 -> ASCII 转写（德语为主，兼顾北欧/法语常见字母）
_TRANSLITERATION = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "ae", "Ö": "oe", "Ü": "ue",
    "å": "a", "Å": "a", "ø": "o", "Ø": "o",
    "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
    "é": "e", "è": "e", "ê": "e", "à": "a", "ç": "c",
}


def _strip_inline_comment(name: str) -> str:
    """剥掉配置项里的行内注释，便于匹配。

    使用者习惯在 config.yaml 里给作者加注记，例如：
        - Juergen Lisenfeld  # Ulm, 德语变音
    若注记被当成名字的一部分，就会永远匹配不上。这里按 YAML 的规则，
    只在 "#" 前面是空白时才视为注释（名字里紧挨着的 # 不受影响）。

    "Andreas Wallraff  # ETH" -> "Andreas Wallraff"
    """
    if "#" not in name:
        return name.strip()
    return re.split(r"\s#", name, maxsplit=1)[0].strip()


def _deaccent(s: str) -> str:
    """去掉变音符号：NFKD 分解后丢弃组合记号。"Jürgen" -> "Jurgen"。"""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", s)
        if not unicodedata.combining(ch)
    )


def _transliterate(s: str) -> str:
    """德语等变音字母转写为 ASCII："Jürgen" -> "Juergen"，"ß" -> "ss"。"""
    return "".join(_TRANSLITERATION.get(ch, ch) for ch in s)


def _split_authors(authors_str: str) -> list[str]:
    """将论文作者字符串按逗号/分号分割为单个作者名列表。

    处理常见格式：
    - "A, B, C" → ["A", "B", "C"]
    - "A, B, and C" → ["A", "B", "C"]
    - "A; B; C" → ["A", "B", "C"]
    """
    if not authors_str:
        return []
    # 按逗号或分号分割
    parts = re.split(r"[,;]", authors_str)
    result = []
    for p in parts:
        name = p.strip()
        # 去掉 "and" 前缀
        if name.lower().startswith("and "):
            name = name[4:].strip()
        if name:
            result.append(name)
    return result


def _normalize_author_name(name: str) -> str:
    """规范化作者名用于比较：小写、去多余空格。

    "John A. Smith" → "john a. smith"
    """
    return " ".join(name.lower().split())


def _name_variants(name: str) -> set:
    """生成一个作者名的所有可比较变体，用于跨写法匹配。

    同一位作者的姓名在不同数据源里写法常常不一致，典型情况：
    - 变音字母：    "Jürgen Lisenfeld" / "Juergen Lisenfeld" / "Jurgen Lisenfeld"
    - Unicode 分解： "ü"（预组合 NFC）与 "u¨"（分解 NFD）是两种不同的码位
    - 首字母缩写：   "R. J. Schoelkopf" / "RJ Schoelkopf" / "R J Schoelkopf"

    这里统一生成「原样 / 去变音 / 德语转写 / 去点 / 去空格」几组变体，
    两侧各取变体集合后求交集，只要有一种写法对上就算匹配。
    """
    base = " ".join(name.lower().split())
    if not base:
        return set()

    variants = {base}
    # 去变音（同时把 NFC / NFD 两种码位写法统一）
    variants.add(_deaccent(base))
    # 德语转写（ü→ue、ß→ss），以及转写后再去变音
    trans = _transliterate(base)
    variants.add(trans)
    variants.add(_deaccent(trans))

    # 首字母缩写的不同写法：去点、再去所有空格
    for v in list(variants):
        nodot = " ".join(v.replace(".", " ").split())
        variants.add(nodot)
        variants.add(nodot.replace(" ", ""))

    return {v for v in variants if v}


def _is_author_match(
    highlight_author: str,
    paper_authors: list[str],
    paper_authors_norm: set,
) -> bool:
    """检查高亮作者是否在论文作者列表中精确匹配。

    匹配规则（大小写不敏感、变音字母不敏感）：
    1. 全名精确匹配："Han Wang" == "Han Wang"
    2. 姓/名顺序互换："Han Wang" == "Wang Han"
    3. 变音字母兼容："Jürgen Lisenfeld" 匹配 "Juergen Lisenfeld" / "Jurgen Lisenfeld"
    4. 首字母缩写："R. J. Schoelkopf" 匹配 "RJ Schoelkopf" 或 "R.J. Schoelkopf"

    另外会先剥掉配置项里的行内注释（"Andreas Wallraff  # ETH" → "Andreas Wallraff"），
    这样使用者在 config.yaml 里加的注记不会把匹配搞坏。
    """
    # 配置里可能带行内注释，先剥掉再参与匹配
    target = _strip_inline_comment(highlight_author)
    target_variants = _name_variants(target)
    if not target_variants:
        return False

    if target_variants & paper_authors_norm:
        return True

    # 姓/名顺序互换匹配（对每种写法都试一次）
    parts = target.split()
    if len(parts) >= 2:
        reversed_name = " ".join([parts[-1]] + parts[:-1])
        if _name_variants(reversed_name) & paper_authors_norm:
            return True

    return False


def filter_paper(
    paper: dict,
    keywords: list[str],
    highlight_authors_1: list[str],
    highlight_authors_2: list[str],
) -> Optional[dict]:
    """对单篇论文执行关键词 + 作者匹配。

    命中任一条件即收录。返回带 matched_keywords/matched_authors/author_group
    字段的 paper 副本；未命中则返回 None。

    Args:
        paper: 论文字典（含 title/summary/authors 等字段）
        keywords: 关键词列表
        highlight_authors_1: 国际高亮作者列表
        highlight_authors_2: 国内高亮作者列表

    Returns:
        命中则返回更新后的 paper dict，未命中返回 None
    """
    title = paper.get("title") or ""
    summary = paper.get("summary") or ""
    authors = paper.get("authors") or ""

    # —— 关键词匹配：同时搜索 title 和 summary（修复原 bug）——
    # 用正则 \b 单词边界，避免 "cat" 匹配 "category"
    search_text = f"{title} {summary}"
    matched_keywords = []
    for kw in keywords:
        kw_clean = kw.strip()
        if not kw_clean:
            continue
        # 转义正则特殊字符，用单词边界匹配
        pattern = rf"\b{re.escape(kw_clean)}\b"
        if re.search(pattern, search_text, re.IGNORECASE):
            matched_keywords.append(kw_clean)

    # —— 作者匹配 ——
    # 按逗号分割论文作者列表，逐个做精确匹配（大小写不敏感）
    # 避免子串误匹配：如 "Han Wang" 误匹配 "Zihan Wang"
    matched_authors = []
    author_group = 0

    paper_author_names = _split_authors(authors)
    # 构建作者名变体集合（小写 / 去变音 / 德语转写 / 缩写），便于快速查找
    paper_authors_norm = set()
    for an in paper_author_names:
        paper_authors_norm |= _name_variants(an)

    for author in highlight_authors_1:
        if _is_author_match(author, paper_author_names, paper_authors_norm):
            matched_authors.append(author)
            author_group = max(author_group, 1)

    for author in highlight_authors_2:
        if _is_author_match(author, paper_author_names, paper_authors_norm):
            matched_authors.append(author)
            author_group = max(author_group, 2)

    # —— 判断是否命中 ——
    if not matched_keywords and not matched_authors:
        return None  # 不收录

    # 返回更新后的 paper（不修改原 dict）
    result = dict(paper)
    result["matched_keywords"] = matched_keywords
    result["matched_authors"] = matched_authors
    result["author_group"] = author_group
    return result


def filter_papers(
    papers: list[dict],
    keywords: list[str],
    highlight_authors_1: list[str],
    highlight_authors_2: list[str],
) -> list[dict]:
    """批量过滤论文。返回命中列表，按 date_added 降序排序。"""
    filtered = []
    for paper in papers:
        result = filter_paper(paper, keywords, highlight_authors_1, highlight_authors_2)
        if result is not None:
            filtered.append(result)

    # 按日期降序
    filtered.sort(key=lambda p: p.get("date_added", ""), reverse=True)
    logger.info(f"过滤完成：{len(papers)} 篇 -> {len(filtered)} 篇命中")
    return filtered


def classify_paper(
    paper: dict,
    keywords: list[str],
    highlight_authors_1: list[str],
    highlight_authors_2: list[str],
) -> dict:
    """用【当前】配置判定单篇论文的命中情况。

    与 filter_paper 的区别：无论命中与否都返回 dict（含 is_match 字段），
    未命中的论文返回原字段 + 空命中信息。这样便于「全量存储原始词条、
    仅在展示时按当前配置实时筛选」——加/删关键词、加/删作者都能对历史
    数据 retroactive 生效。

    Args / Returns 同 filter_paper，额外返回：
        is_match: bool，是否命中任一关键词或高亮作者
    """
    result = filter_paper(paper, keywords, highlight_authors_1, highlight_authors_2)
    if result is not None:
        result["is_match"] = True
        return result

    # 未命中：保留原字段，命中信息置空
    classified = dict(paper)
    classified["matched_keywords"] = []
    classified["matched_authors"] = []
    classified["author_group"] = 0
    classified["is_match"] = False
    return classified


def classify_papers(
    papers: list[dict],
    keywords: list[str],
    highlight_authors_1: list[str],
    highlight_authors_2: list[str],
) -> list[dict]:
    """批量判定。返回与输入等长的列表，每项是 classify_paper 的结果。"""
    return [
        classify_paper(p, keywords, highlight_authors_1, highlight_authors_2)
        for p in papers
    ]
