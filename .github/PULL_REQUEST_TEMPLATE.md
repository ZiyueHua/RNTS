## 这个 PR 做了什么

<!-- 一两句话说明改动内容和目的 -->

## 影响范围

- [ ] 抓取逻辑（`app/fetcher.py`）
- [ ] 筛选 / 作者匹配（`app/filters.py`）
- [ ] 网页与样式（`templates/`、`static/`）
- [ ] 报告导出（`app/reports.py`）
- [ ] 调度（`app/scheduler.py`）
- [ ] 命令行脚本（`.bat` / `.ps1` / `.sh`）
- [ ] 文档
- [ ] 其它：

## 自检清单（RNTS 特有，请逐条确认）

- [ ] 本地跑过 `python check_scripts.py`，结果**全部 OK**
- [ ] 没有删除、覆盖或绕过 `.gitattributes`
- [ ] `check_scripts.py` 里的 `_scan_block_parens`（if/for 块内括号检查）仍在
- [ ] `run_daily_task.bat` 的日志行仍是 `>>"data\daily_task.log" echo [RNTS] FINISHED rc=%RC%`（重定向必须在最前）
- [ ] `run.bat` 里用的仍是 `timeout.exe` 而不是 `timeout`
- [ ] 没有提交 `config/config.yaml`、`data/`、`.venv/`（已在 `.gitignore` 中）
- [ ] 如果改了版本号，改的是 `app/__init__.py` 里的 `__version__` 这一处

## 测试情况

<!-- 怎么验证的？例如：本地双击 run.bat，点「⟳ 手动抓取」，检查列表页与 /stats 是否正常 -->

## 相关 Issue

<!-- 如 Closes #12 -->
