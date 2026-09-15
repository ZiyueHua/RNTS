# RNTS 本地部署说明（Local Deploy）

将云端 RNTS v2.0 重新部署到本地 Windows 机器的完整记录。
项目根目录：`E:\WorkBuddy\RNTS\rnts\`

> 云端版本因服务器不能 24 小时开机，故迁回本地常驻运行。
> 本地环境无 Docker，因此采用 **Python 虚拟环境 + uvicorn 单进程** 部署，
> 并通过 Windows 任务计划程序实现登录自启（等效 24/7）。

---

## 1. 环境要求

- Python 3.12+（本机使用受管 Python 3.13）
- 可访问外网（抓取 12 个 RSS 源 + Crossref / Semantic Scholar 摘要补全）
- 无需 Docker

## 2. 项目已还原的目录结构

```
rnts/
├── app/                 # FastAPI 应用（__init__/config/database/fetcher/filters/main/models/routes/scheduler）
├── config/config.yaml   # RSS 源 / 关键词 / 高亮作者 / 调度配置
├── templates/           # Jinja2 模板（base/index/paper_detail/settings/stats + partials/）
├── static/css/custom.css
├── data/                # SQLite 数据库（rnts.db，首次启动自动建表）
├── .venv/               # 本机虚拟环境（已创建并装好依赖）
├── requirements.txt     # 依赖（已补充 beautifulsoup4，见下方说明）
├── run.bat              # 手动启动脚本（前台）
├── install_autostart.ps1# 注册为「登录自启」后台任务
├── Dockerfile / docker-compose.yml / run.sh / README.md   # 云端原文件，保留备用
```

## 3. 已修复的问题

原 `requirements.txt` **缺少 `beautifulsoup4`**（`app/fetcher.py` 用到了
`from bs4 import BeautifulSoup`，但云端靠预装环境侥幸运行）。本地部署已显式
加入 `beautifulsoup4>=4.12.0`，否则启动抓取会 `ModuleNotFoundError`。

## 4. 启动方式

### 方式 A：手动前台运行（调试/临时）
双击 `run.bat`，或命令行：
```bat
cd E:\WorkBuddy\RNTS\rnts
.venv\Scripts\activate.bat
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```
关闭窗口即停止。

### 方式 B：开机自启（推荐，常驻 24/7）
以 PowerShell 运行：
```powershell
cd E:\WorkBuddy\RNTS\rnts
powershell -ExecutionPolicy Bypass -File install_autostart.ps1
```
注册后，每次用户登录自动在后台启动（无控制台窗口）。
- 手动启动任务：`Start-ScheduledTask -TaskName "RNTS"`
- 卸载：`Unregister-ScheduledTask -TaskName "RNTS" -Confirm:$false`

> 若希望「即使不登录也运行」，可在任务计划程序中把该任务改为以
> `SYSTEM` 账户运行（AtStartup 触发），但需确保 `E:\` 对 SYSTEM 可写。

## 5. 访问地址

| 页面 | 地址 |
|------|------|
| 论文列表 | http://localhost:8000/ |
| 统计仪表盘 | http://localhost:8000/stats |
| 配置管理 | http://localhost:8000/settings |
| API 文档 (Swagger) | http://localhost:8000/docs |
| 手动触发抓取 | POST /api/v1/papers/fetch |
| 统计总览 API | GET /api/v1/stats/overview |

## 6. 行为与注意事项

- **每日 08:00（北京时间）自动抓取** 12 个 RSS 源，按关键词/高亮作者过滤后入库。
- **启动补跑**：进程启动时若发现「今天尚未成功抓取」，会立即补跑一次，避免停机漏抓。
- **单进程**：务必 `--workers 1`（调度器在进程内运行，多 worker 会导致重复调度）。
- **数据库**：`data/rnts.db`，SQLite。迁移云端数据时，把该文件复制过来即可（去重靠 UNIQUE(link)）。
- **配置热改**：在「设置」页增删关键词/作者后点「重新加载配置文件」；RSS 源与调度时间在 `config/config.yaml` 修改后同样重新加载。
- **云同步（可选）**：`config/config.yaml` 的 `cloud_sync.dir` 默认为空（不含任何固定路径），需要时自己填同步目录并把 `enabled` 改成 `true`；也可用环境变量 `RNTS_CLOUD_SYNC_DIR` / `RNTS_CLOUD_SYNC_ENABLED=1` 设置。目录留空则不进行同步。
- **配置不再被自动改写**：每日抓取只更新数据库和报告，不会再重排 `config/config.yaml`（否则会冲掉手写注释和手动调好的顺序）。需要整理时手动跑 `sort_config.bat`（或 `python sort_config.py`，加 `--check` 只预览）。
- **注释会保留**：`keywords` / `highlight_authors_1` / `highlight_authors_2` 里，条目上方和行尾的 `#` 注释在保存、重排时都会跟着条目一起走。
- **作者名匹配**：大小写与变音字母不敏感，`Jürgen Lisenfeld` 可匹配 `Juergen Lisenfeld` / `Jurgen Lisenfeld`（含 Unicode 分解写法）；也兼容首字母缩写 `R. J. Schoelkopf` ↔ `RJ Schoelkopf`。整名比较，不会子串误伤。
- **首次启动实测**：抓取 657 篇原始论文，命中关键词/作者 109 篇并入库，各页面与 API 均返回 HTTP 200。

## 7. 重新从云端导出文件还原（备用）

若需再次从 `.txt` 导出包还原源码，运行：
```bat
cd E:\WorkBuddy\RNTS
python restore_project.py
```
（该脚本按 `_INDEX.txt` 的 `__`→`/` 映射还原；`app/__init__.py` 的特殊 `__` 已单独处理。）
