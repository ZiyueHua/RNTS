# -*- coding: utf-8 -*-
"""打包前自检：确认所有命令行脚本不会因编码/换行在 cmd 下报错。

检查项：
  1. .bat / .cmd  -> 必须纯 ASCII + CRLF（中文 Windows 的 cmd 用 GBK 解释文件，
                     UTF-8 中文的尾字节可能被当成 GBK 前导字节，吞掉换行导致脚本乱掉）
  2. .ps1         -> UTF-8 with BOM + CRLF（PowerShell 5.1 无 BOM 会按 ANSI 读中文）
  3. .sh          -> UTF-8 无 BOM + LF（Linux / macOS）
  4. .py          -> 文件本身 UTF-8；另外所有非 ASCII 字符必须能用 GBK 编码，
                     否则 print 到中文 Windows 控制台（cp936）会 UnicodeEncodeError
  5. .bat/.cmd    -> if/for 的 ( ... ) 块内不得出现 "(" 或 ")"（含 echo 文本）。
                     cmd 把 ")" 当作块结束，块会被提前截断并报
                     ". was unexpected at this time."，整个脚本直接失效。

用法：python check_scripts.py   （退出码 0 = 全部通过）
"""

import pathlib
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent

# 需要扫描的项目文件（排除 .venv、备份目录）
TARGETS = [
    "run.bat", "run_daily_task.bat", "sort_config.bat", "setup_env.bat",
    "install_autostart.ps1", "install_daily_task.ps1", "run_daily_task.ps1",
    "run.sh", "setup_env.sh",
    "sort_config.py", "check_scripts.py",
    # app 下的 python 也要扫：任何 print 到中文控制台的非 GBK 字符都会抛
    # UnicodeEncodeError（v1.1.0 新增 daily_run.py / routes_favorites.py）
    "app/main.py", "app/daily_run.py", "app/config.py", "app/filters.py",
    "app/models.py", "app/routes.py", "app/routes_favorites.py",
    "app/scheduler.py", "app/reports.py", "app/fetcher.py",
]


def _scan_block_parens(raw: bytes) -> list[str]:
    """检查 if/for 的 ( ... ) 块内是否混入了括号（含 echo 文本里的）。

    cmd 把块内任意一个 ")" 都当作块结束符，即使是 echo 的文字内容。
   结果就是块被提前截断，脚本启动时直接报 ". was unexpected at this time."
    并退出，一行有效命令都跑不到 —— 所以必须在打包前拦下来。
    """
    import re

    problems = []
    text = raw.decode("ascii", "replace").replace("\r\n", "\n")
    lines = text.split("\n")
    depth = 0
    starts: list[int] = []
    for i, line in enumerate(lines, 1):
        s = line.strip()
        low = s.lower()
        is_opener = bool(re.match(r"^(if|for)\b", low)) and s.endswith("(")

        if depth == 0:
            if is_opener:
                depth = 1
                starts = [i]
            continue

        # ---- 以下都在 if/for 块内 ----
        if is_opener:                      # 合法的嵌套开块
            depth += 1
            starts.append(i)
            continue
        if s.startswith(")"):              # ")" 或 ") else (" 之类的连接行
            rest = s[1:].strip()
            depth -= 1
            if rest.startswith("else") and rest.endswith("("):
                depth += 1                 # 关掉一块又开一块
            continue

        if "(" in s or ")" in s:
            problems.append(
                f"L{i}: if/for 块（始于 L{starts[0]}）内出现括号，"
                f'cmd 会把 ")" 当块结束 -> 脚本一启动就报 '
                f'". was unexpected at this time."  |  {s}'
            )
    return problems


def scan_file(path: pathlib.Path) -> list[str]:
    """返回该文件的问题列表（空列表 = 没问题）。"""
    problems = []
    raw = path.read_bytes()
    ext = path.suffix.lower()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    crlf = raw.count(b"\r\n")
    bare_lf = raw.count(b"\n") - crlf
    nonascii = sorted({b for b in raw if b >= 0x80})

    if ext in (".bat", ".cmd"):
        if nonascii:
            bad = " ".join(f"0x{b:02X}" for b in nonascii[:8])
            problems.append(f"含非 ASCII 字节 {bad}{' ...' if len(nonascii) > 8 else ''}（cmd/GBK 下会解析错乱）")
        if bare_lf:
            problems.append(f"有 {bare_lf} 处裸 LF（应为 CRLF）")
        if has_bom:
            problems.append("不应带 BOM")
        problems.extend(_scan_block_parens(raw))

    elif ext == ".ps1":
        if not has_bom:
            problems.append("缺少 UTF-8 BOM（PowerShell 5.1 会把中文读成乱码）")
        if bare_lf:
            problems.append(f"有 {bare_lf} 处裸 LF（应为 CRLF）")

    elif ext == ".sh":
        if has_bom:
            problems.append("不应带 BOM")
        if crlf:
            problems.append(f"有 {crlf} 处 CRLF（应为 LF）")

    elif ext == ".py":
        if has_bom:
            problems.append("不应带 BOM（建议 UTF-8 无 BOM）")
        # 输出到 GBK 控制台的安全性：非 ASCII 字符必须能编码成 GBK
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            problems.append(f"不是合法 UTF-8: {e}")
            return problems
        # 只看真正会打到控制台的行：写进 Markdown / HTML 的 emoji、以及
        # 变音字母转写表这类常量，落盘时都是 UTF-8，与控制台无关。
        import re
        emit = re.compile(
            r"print\s*\(|logger\.|logging\.|sys\.stdout|sys\.stderr"
            r"|\braise\b|warnings\."
        )
        risky = set()
        for line in text.split("\n"):
            if not emit.search(line):
                continue
            risky.update(c for c in line if ord(c) >= 0x80)
        unencodable = []
        for ch in sorted(risky):
            try:
                ch.encode("gbk")
            except UnicodeEncodeError:
                unencodable.append(f"U+{ord(ch):04X} {unicodedata.name(ch, '?')}")
        if unencodable:
            problems.append("含 GBK 无法表示的字符，print 到中文控制台会报错: " + ", ".join(unencodable))

    return problems


def main() -> int:
    checked, failed = 0, 0
    print("=" * 68)
    print("  RNTS 打包前自检 —— 命令行脚本编码 / 换行")
    print("=" * 68)

    for name in TARGETS:
        path = ROOT / name
        if not path.exists():
            continue
        checked += 1
        problems = scan_file(path)
        raw = path.read_bytes()
        enc = "BOM" if raw.startswith(b"\xef\xbb\xbf") else "no-BOM"
        nl = "CRLF" if raw.count(b"\r\n") and not (raw.count(b"\n") - raw.count(b"\r\n")) else "LF/mixed"
        if problems:
            failed += 1
            print(f"[FAIL] {name:26s} ({enc}, {nl})")
            for p in problems:
                print(f"         - {p}")
        else:
            print(f"[ OK ] {name:26s} ({enc}, {nl})")

    print("-" * 68)
    print(f"共检查 {checked} 个文件，{failed} 个有问题。")
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
