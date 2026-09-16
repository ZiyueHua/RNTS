"""配置加载模块 —— 从 config.yaml 读取，支持在线修改后写回。"""

import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.yaml"
# 配置模板：随版本发布、纳入版本控制；config.yaml 是各人自己的配置，不入库。
# 首次运行（或 config.yaml 被误删）时自动从这里复制一份，保证程序能起来。
EXAMPLE_CONFIG_PATH = Path(__file__).parent.parent / "config" / "config.example.yaml"

# 允许使用者自行加注释的三个列表型配置
LIST_FIELDS = ("keywords", "highlight_authors_1", "highlight_authors_2")


class RSSSource(BaseModel):
    name: str
    display_name: str = ""
    url: str
    extractor: str = "arxiv"
    enabled: bool = True
    # 可选：显式指定该源的 RSS 公告地址。arXiv 源不填时按 url 里 search_query 的
    # cat:xxx 自动推导（cat:quant-ph → https://rss.arxiv.org/rss/quant-ph）；
    # 填了则以它为准（例如想改抓别的分类或多个分类时）。
    rss_url: str = ""


class ScheduleConfig(BaseModel):
    daily_fetch_time: str = "08:00"
    fetch_timeout: int = 30
    max_concurrent: int = 6
    per_page: int = 20
    # arXiv 抓取的回溯天数：每日抓取取 [今天-N, 今天] 提交的全部 quant-ph 论文，
    # 网页端「补抓 arXiv」取 [选定日期-N, 选定日期]。设为 0 则只取当天。
    arxiv_lookback_days: int = 3


class CloudSyncConfig(BaseModel):
    """云盘同步（坚果云 / OneDrive 等）—— 报告生成后自动复制到同步目录。

    enabled: 是否启用（默认关闭）
    dir:     同步目录。**不内置任何默认路径**，由使用者自行填写
             （例如 D:/Nutstore/1/RNTS_报告）。留空则不做任何同步。
    formats: 要同步的报告格式，可选 "html" / "md"

    若不想改 config.yaml，也可用环境变量设置（多机 / Docker 部署更方便）：
        RNTS_CLOUD_SYNC_DIR      同步目录
        RNTS_CLOUD_SYNC_ENABLED  1 / true / yes / on 表示启用
    """

    enabled: bool = False
    dir: str = ""
    formats: list[str] = ["html", "md"]


class AppConfig(BaseModel):
    rss_sources: list[RSSSource]
    keywords: list[str]
    highlight_authors_1: list[str]
    highlight_authors_2: list[str]
    schedule: ScheduleConfig = ScheduleConfig()
    cloud_sync: CloudSyncConfig = CloudSyncConfig()


_config_cache: Optional[AppConfig] = None


def _apply_env_overrides(config: AppConfig) -> AppConfig:
    """用环境变量覆盖云盘同步设置（可选）。

    适合多机部署 / Docker：不改 config.yaml 也能指定同步目录。
    """
    env_dir = os.environ.get("RNTS_CLOUD_SYNC_DIR")
    if env_dir:
        config.cloud_sync.dir = env_dir
    env_enabled = os.environ.get("RNTS_CLOUD_SYNC_ENABLED")
    if env_enabled is not None:
        config.cloud_sync.enabled = env_enabled.strip().lower() in {
            "1", "true", "yes", "on",
        }
    return config


def _ensure_config_file() -> None:
    """config.yaml 不存在时用模板生成一份。

    config.yaml 是个人配置（关键词 / 关注作者 / 云盘目录），不进版本控制，
    所以全新 clone 或解压新包后都可能没有它。缺文件时旧版本会直接
    FileNotFoundError 起不来，这里兜底从 config.example.yaml 复制一份。
    """
    if CONFIG_PATH.exists():
        return
    if not EXAMPLE_CONFIG_PATH.exists():
        return  # 模板也没了就只能让上层照原样报错，避免掩盖真正的问题
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    logger.warning(
        f"config.yaml 不存在，已从模板生成：{CONFIG_PATH}（请改成你自己的关键词与作者）"
    )


def load_config(reload: bool = False) -> AppConfig:
    """加载配置文件。默认使用缓存，reload=True 强制重读。"""
    global _config_cache
    if _config_cache is None or reload:
        _ensure_config_file()
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _config_cache = _apply_env_overrides(AppConfig(**data))
    return _config_cache


def _split_entry_comment(raw: str) -> tuple[str, str]:
    """拆分列表条目与其行内注释。

    沿用 YAML 的规则：只有 "#" 前面是空白时才算注释，
    所以 "Andreas Wallraff  # ETH" 会被拆成 ("Andreas Wallraff", "# ETH")，
    而名字里紧挨着的 "#" 不会被误伤。

    Returns:
        (条目正文, 行内注释)；无注释时注释部分为空串。
    """
    if "#" not in raw:
        return raw.strip(), ""
    parts = re.split(r"\s#", raw, maxsplit=1)
    name = parts[0].rstrip()
    note = "# " + parts[1].strip() if len(parts) > 1 else ""
    return name, note


def _strip_entry_comment(value: str) -> str:
    """去掉条目里的行内注释，只保留条目正文。"""
    return _split_entry_comment(value)[0]


def _is_top_level_key(line: str) -> bool:
    """是否为顶层键所在行（行首无缩进、非列表项、非注释）。"""
    if not line or line[0] in " \t-#":
        return False
    return bool(re.match(r"^[^\s#].*:", line))


def _item_value(raw: str) -> str:
    """还原 dump 出来的条目值（处理引号转义），如 "'A: B'" -> "A: B"。"""
    raw = raw.strip()
    try:
        value = yaml.safe_load(raw)
    except Exception:
        return raw
    return value if isinstance(value, str) else str(value)


def _collect_comment_map(text: str) -> dict:
    """从 YAML 文本里提取三个列表的注释，按条目归类。

    归属规则：
    - 条目**上方**连续的注释行，归该条目所有（重排时跟着条目一起走）
    - 条目**行尾**的 "# ..."，归该条目所有
    - 列表块**末尾**没有后继条目的注释行，归为整块的尾部注释

    Returns:
        {字段名: {"items": {条目名: (上方注释行, 行尾注释)}, "trailing": [尾部注释行]}}
    """
    result: dict = {}
    lines = text.split("\n")

    for field in LIST_FIELDS:
        start = None
        for i, line in enumerate(lines):
            if re.match(rf"^{re.escape(field)}\s*:", line):
                start = i
                break
        if start is None:
            continue

        end = len(lines)
        for j in range(start + 1, len(lines)):
            if _is_top_level_key(lines[j]):
                end = j
                break

        result[field] = _parse_block(lines[start + 1:end])

    return result


def _parse_block(body_lines: list[str]) -> dict:
    """解析一个列表块的注释归属。

    Returns:
        {"items": {条目名: (上方注释行, 行尾注释)}, "trailing": [块尾注释行]}
    """
    items: dict = {}
    pending: list[str] = []
    for line in body_lines:
        stripped = line.strip()
        if not stripped:
            # 空行也归给紧随其后的条目（连续的空行只保留一个），
            # 让使用者手写的分组间隔在重排后依然存在
            if (pending or items) and (not pending or pending[-1].strip()):
                pending.append(line)
            continue
        if stripped.startswith("#"):
            pending.append(line)
            continue
        m = re.match(r"^(\s*)-\s*(.*)$", line)
        if not m:
            continue
        name, note = _split_entry_comment(m.group(2))
        if not name:
            continue
        # 重名时保留第一次出现的注释
        items.setdefault(name, (list(pending), note))
        pending = []
    return {"items": items, "trailing": list(pending)}


def _render_item(name: str) -> str:
    """渲染单个列表条目（让 PyYAML 负责引号转义）。"""
    dumped = yaml.safe_dump(
        [name], allow_unicode=True, default_flow_style=False, sort_keys=False
    )
    return dumped.split("\n")[0]


def _render_list_block(info: dict, names: list[str]) -> list[str]:
    """按给定顺序渲染列表块，把注释贴回各自条目。"""
    rendered: list[str] = []
    for name in names:
        leading, note = info["items"].get(name, ([], ""))
        rendered.extend(leading)
        item = _render_item(name)
        if note:
            item += "  " + note
        rendered.append(item)
    rendered.extend(info["trailing"])
    return rendered


def _replace_list_blocks(text: str, data: dict) -> str:
    """原地替换 YAML 文本里的三个列表块，其余内容一字不动。

    这样使用者手写的分组空行、缩进、引号风格、rss_sources 的排列等
    都不会被改写，只有关键词 / 作者三个列表被更新。
    """
    lines = text.split("\n")
    out: list[str] = []
    handled: set[str] = set()
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        m = re.match(r"^([A-Za-z_][\w]*)\s*:", line)
        field = m.group(1) if m and m.group(1) in LIST_FIELDS else None

        if field is None:
            out.append(line)
            i += 1
            continue

        j = i + 1
        while j < n and not _is_top_level_key(lines[j]):
            j += 1

        out.append(line)
        out.extend(_render_list_block(_parse_block(lines[i + 1:j]), data.get(field, [])))
        handled.add(field)
        i = j

    # 原文件里没出现过的列表字段，补在文件末尾，避免丢配置
    for field in LIST_FIELDS:
        if field in handled:
            continue
        out.append(f"{field}:")
        out.extend(_render_list_block({"items": {}, "trailing": []}, data.get(field, [])))

    return "\n".join(out)


def _reapply_comments(text: str, comment_map: dict) -> str:
    """把 _collect_comment_map 收集的注释写回到新生成的 YAML 文本。"""
    if not comment_map:
        return text

    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        m = re.match(r"^([A-Za-z_][\w]*)\s*:", line)
        field = m.group(1) if m and m.group(1) in comment_map else None

        if field is None:
            out.append(line)
            i += 1
            continue

        # 收集该块的全部行（直到下一个顶层键或文件末尾）
        j = i + 1
        while j < n and not _is_top_level_key(lines[j]):
            j += 1
        body = lines[i + 1:j]

        info = comment_map[field]
        out.append(line)
        for bl in body:
            mi = re.match(r"^(\s*)-\s*(.*)$", bl)
            if mi and mi.group(2).strip():
                name = _item_value(mi.group(2))
                leading, note = info["items"].get(name, ([], ""))
                out.extend(leading)
                out.append(bl + (f"  {note}" if note else ""))
            else:
                out.append(bl)
        out.extend(info["trailing"])

        i = j

    return "\n".join(out)


def _render_config_text(data: dict, old_text: str | None) -> str:
    """生成要写回的 YAML 文本，尽量保留使用者手写的格式与注释。

    优先「原地只替换三个列表块」——分组空行、缩进、引号风格、
    rss_sources 的排列等一律不动，只有关键词 / 作者列表被更新。

    仅当配置里列表以外的部分（rss_sources / schedule / cloud_sync 等）
    也发生了变化时，才退回 yaml.dump 全量重写，并把注释重新贴回去。
    """
    if old_text is None:
        return yaml.dump(
            data, allow_unicode=True, default_flow_style=False, sort_keys=False
        )

    try:
        old_data = yaml.safe_load(old_text) or {}
    except Exception as e:
        logger.warning(f"解析原配置失败，将全量重写: {e}")
        old_data = None

    if isinstance(old_data, dict):
        # 两边都过一遍 AppConfig，把省略的默认值补齐后再比较，
        # 否则"文件里省略了可选字段"会被误判成结构变化。
        try:
            old_norm = AppConfig(**old_data).model_dump()
        except Exception as e:
            logger.warning(f"原配置无法通过校验，将全量重写: {e}")
            old_norm = None

        if old_norm is not None:
            tail = lambda d: {k: v for k, v in d.items() if k not in LIST_FIELDS}  # noqa: E731
            if tail(old_norm) == tail(data):
                # 只有列表变了 —— 原地替换，格式和注释原样保留
                return _replace_list_blocks(old_text, data)

    # 结构部分也变了 —— 全量重写，但把注释按条目贴回去
    text = yaml.dump(
        data, allow_unicode=True, default_flow_style=False, sort_keys=False
    )
    try:
        text = _reapply_comments(text, _collect_comment_map(old_text))
    except Exception as e:
        logger.warning(f"回填注释失败: {e}")
    return text


def save_config(config: AppConfig):
    """将配置写回 YAML 文件并清空缓存。

    使用者常在这三个列表里手写注释（例如 "- Juergen Lisenfeld  # Ulm"）。
    yaml.dump 会把注释连同分组空行一起抹掉，所以这里做原地更新：
    只重写 keywords / highlight_authors_1 / highlight_authors_2 三个块，
    条目上方与行尾的注释跟着条目一起走（重排后也不丢）。
    """
    data = config.model_dump()
    # 条目里若混进了行内注释，先剥掉，免得被当成名字的一部分写回
    for field in LIST_FIELDS:
        data[field] = [_strip_entry_comment(x) for x in data.get(field, [])]

    old_text = None
    if CONFIG_PATH.exists():
        try:
            old_text = CONFIG_PATH.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"读取原配置文件失败: {e}")

    text = _render_config_text(data, old_text)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(text)

    global _config_cache
    _config_cache = None  # 清缓存，下次读取重新加载


def get_enabled_sources() -> list[RSSSource]:
    """获取所有启用的 RSS 源。"""
    config = load_config()
    return [s for s in config.rss_sources if s.enabled]


def _sort_key(s: str) -> tuple[str, str]:
    """排序键：小写 + 去变音，让 "Jürgen" 排在 "Julian" 附近而不是末尾。

    对中文条目（国内作者名按拼音写）没有影响，仍是字母序。
    """
    lowered = s.lower()
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", lowered)
        if not unicodedata.combining(ch)
    )
    return folded, lowered


def normalize_config(reload: bool = True) -> tuple[bool, AppConfig]:
    """对 keywords / highlight_authors_1 / highlight_authors_2 按字母顺序排序。

    【注意】本函数**只由手动脚本 sort_config.py 调用**，每日抓取流程不再自动排序：
    自动排序会在使用者没预期的时候改写 config.yaml，把他加的注记冲掉，
    也打乱他手动调整过的顺序。需要整理时自己跑一次 sort_config.bat 即可。

    排序为大小写、变音不敏感（"GKP" 排在 "quantum" 之前，"Jürgen" 按 "jurgen" 排）。
    仅当顺序真的变化时才写回；写回时会保留注释。

    Returns:
        (changed, config)：changed 表示本次是否改动并写回了文件。
    """
    config = load_config(reload=reload)
    changed = False

    def _sort(field: str) -> None:
        nonlocal changed
        orig = list(getattr(config, field))
        new = sorted(orig, key=_sort_key)
        if new != orig:
            setattr(config, field, new)
            changed = True

    _sort("keywords")
    _sort("highlight_authors_1")
    _sort("highlight_authors_2")

    if changed:
        save_config(config)
        config = load_config(reload=True)  # 重新读取已落盘的排序结果
        logger.info("config.yaml 关键词/作者列表已按字母顺序重新排序")
    return changed, config


def clear_cache():
    """清空配置缓存（配置文件被外部修改后调用）。"""
    global _config_cache
    _config_cache = None
