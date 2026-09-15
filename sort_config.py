"""手动整理 config.yaml —— 把关键词 / 高亮作者列表按字母顺序重排。

每日抓取流程**不再自动改写** config.yaml（自动改写会冲掉使用者手写的注释，
也会打乱手动调整过的顺序）。需要整理时，自己跑一次本脚本即可：

    python sort_config.py             排序并写回（写回前自动备份）
    python sort_config.py --check     只预览会不会变动，不动文件
    python sort_config.py --no-backup 排序但不留备份

排序规则：大小写、变音字母不敏感（"Jürgen" 按 "jurgen" 参与排序，
不会跑到列表末尾）。每个条目上方和行尾的注释会跟着条目一起走，不会丢。

Windows 上直接双击 sort_config.bat 即可运行。
"""

import argparse
import shutil
import sys
from pathlib import Path

# 中文 Windows 控制台（cp936）若遇到极个别 GBK 表示不了的字符，
# print 会直接抛 UnicodeEncodeError 让整个脚本挂掉。
# 这里只放宽错误处理方式，不改动编码（编码交给 Python 按控制台情况自选），
# 最坏情况只是显示一个问号，不影响执行。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import (  # noqa: E402
    CONFIG_PATH,
    LIST_FIELDS,
    _sort_key,
    load_config,
    normalize_config,
)

FIELD_LABELS = {
    "keywords": "关键词",
    "highlight_authors_1": "高亮作者（国际）",
    "highlight_authors_2": "高亮作者（国内）",
}


def _moved_items(old: list, new: list) -> list:
    """列出重排后位置发生变化的条目。"""
    moved = []
    for i, name in enumerate(new):
        if i < len(old) and old[i] == name:
            continue
        moved.append(name)
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把 config.yaml 的关键词 / 高亮作者列表按字母顺序重排（手动执行）"
    )
    parser.add_argument(
        "--config",
        default=str(CONFIG_PATH),
        help=f"配置文件路径（默认 {CONFIG_PATH}）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只预览变动，不写回文件",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="写回前不做备份（默认会备份为 config.yaml.bak）",
    )
    args = parser.parse_args()

    import app.config as config_module

    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"[错误] 找不到配置文件：{config_path}")
        return 1
    config_module.CONFIG_PATH = config_path

    cfg = load_config(reload=True)

    print("=" * 60)
    print(f"配置文件：{config_path}")
    print("=" * 60)

    any_change = False
    for field in LIST_FIELDS:
        old = list(getattr(cfg, field))
        new = sorted(old, key=_sort_key)
        label = FIELD_LABELS.get(field, field)
        if new == old:
            print(f"{label}（{len(old)} 项）：顺序已正确，无需变动")
            continue

        any_change = True
        moved = _moved_items(old, new)
        print(f"{label}（{len(old)} 项）：将重排，{len(moved)} 项位置变动")
        for name in moved[:20]:
            print(f"    - {name}")
        if len(moved) > 20:
            print(f"    ... 其余 {len(moved) - 20} 项略")

    print("-" * 60)

    if not any_change:
        print("无需整理，配置文件保持原样。")
        if args.check:
            print("（--check 预览模式，未修改任何文件）")
        return 0

    if args.check:
        print("以上为预览（--check），未修改任何文件。")
        return 0

    if not args.no_backup:
        backup_path = config_path.with_suffix(config_path.suffix + ".bak")
        shutil.copy2(config_path, backup_path)
        print(f"已备份原文件 -> {backup_path.name}")

    changed, _ = normalize_config(reload=True)
    if changed:
        print("已按字母顺序重排并写回（注释已保留）。")
    else:
        print("写回后无变化。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
