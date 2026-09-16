# 技术说明：arXiv 抓取设计（v1.1.2）

本文说明 RNTS 抓取 arXiv 的设计取舍与实现位置，供二次开发 / 排查问题时参考。
使用者只需阅读 [《操作说明_每日任务.md》](操作说明_每日任务.md) 与
[《更新说明_v1.1.2.md》](更新说明_v1.1.2.md)。

---

## 一、约束条件（先看清问题，再谈方案）

### 1. 网络层：部分网络对 arxiv 域名做 TLS 拦截

实测：TCP + TLS 握手只要携带 `arxiv.org` 的 SNI 就会被立即重置
（`ConnectionResetError`，httpx 报空 `ConnectError`，标准库 urllib 同样失败）；
纯 HTTP（80 端口）同样被阻断。同一 IP 上的 TLS 不带 SNI 可以握手成功，
但服务端（Fastly）返回 `421 Misdirected Request`，无法取到内容。

**结论**：这类网络下访问 arXiv **只能走 QUIC / HTTP/3（UDP 443）**，
因此 `fetch_via_http3()` 是必需组件，不能移除。已实测 HTTP/3 可正常取到
`export.arxiv.org`（Atom）、`rss.arxiv.org`（RSS）、`arxiv.org`（HTML）的内容。

### 2. arXiv 服务端：搜索后端按出口 IP 限流

- `export.arxiv.org/api/query`、`export.arxiv.org/oai2` 与网站搜索
  （`arxiv.org/search`）**共用同一个搜索后端**，被限流时统一表现为
  `429 Rate exceeded.`，或「拖延不响应」（握手上成功、请求迟迟不返回）。
- 站点主域名的 `/api/query` 只是 302 跳转到 `export.arxiv.org`，没有旁路。
- 而 `rss.arxiv.org`（公告源）、`/list/...`（列表页）、`/abs/...`（论文页）
  走另外的路径，**不受该限流影响**（同一分钟实测：export 429/16.7s，
  rss 200/3s，list 200/8s，abs 200/0.9s）。
- 限流按出口 IP 计，共享出口（移动宽带 CGNAT）容易被同池用户连带触发；
  窗口可达数小时，且会因持续请求被刷新。

### 3. 官方规范（必须遵守）

- Terms of Use：OAI-PMH / RSS / arXiv API **合计每 3 秒不超过 1 个请求，
  且同一时刻只用一条连接**；违规会被进一步限制甚至封禁。
- API 用户手册：单次查询建议不超过 1000 条；
  批量/集合收割应使用 OAI-PMH。

---

## 二、方案：两层抓取 + 合规闸门

### 分层结构（`_fetch_arxiv_source`）

| 层 | 接口 | 作用 | 取舍 |
|----|------|------|------|
| 第一层 公告层 | `https://rss.arxiv.org/rss/<分类>` | 当天该分类的全部公告 | 一次请求（约 500KB），含**完整摘要与完整作者列表**；与搜索限流无关 → 作为**高可用层** |
| 第二层 时间窗层 | `https://export.arxiv.org/api/query` | 按 `submittedDate` 取 `[今天-N, 今天]` 全量 | 用于补齐漏抓日期、与公告层互相校验；被限流时降级为缓存 |

- 分类从源配置 `search_query` 里的 `cat:xxx` 推导（`_arxiv_rss_url`），
  也支持源配置显式写 `rss_url:` 覆盖。
- 两层结果按**规范化链接**去重合并（`_merge_arxiv_papers`），时间窗层在后
  （其提交日期更准确）。
- 公告层跳过 `announce_type` 为 `replace` / `replace-cross` 的条目：
  那是已有论文的版本更新公告，收下会把老论文刷新成「本月新增」，污染月报。

### 合规与限流处理（`app/fetcher.py`）

| 机制 | 实现 | 说明 |
|------|------|------|
| 请求闸门 | `_arxiv_gate()` + `ARXIV_MIN_INTERVAL = 3.0` | 所有 `*.arxiv.org` 请求共用一把锁，串行且相邻间隔 ≥3 秒 |
| 重试策略 | `RETRY_DELAYS = (3,)` | 只做一次 TCP 重试（间隔 3 秒），再失败交给 HTTP/3 |
| 限流退避 | `RateLimitedError` + `RATE_LIMIT_DELAY = 60` | 收到 429 长退避且**最多重试一次**，避免刷新对方惩罚窗口 |
| HTTP/3 时间预算 | `fetch_source_text` 内 `h3_deadline` | 对端「拖延不响应」时不再累加等待（每日 90 秒、快速模式 30 秒） |
| 快速模式 | `fetch_source_text(quick=True)` | 手动补抓专用：TCP/HTTP3 各只试一次、429 不等待，十几秒内必定返回 |
| 按主机冷却 | `_mark_rate_limited(host)` / `_rate_limit_active(host)`，标记文件 `data/cache/<host>.ratelimit` | 冷却 90 分钟内不请求该主机、直接用缓存；成功即清除。公告层与 API 分属不同主机，互不影响 |
| 本地缓存 | `_save_cache` / `_load_fresh_cache`（`data/cache/*.atom`，48 小时） | 网络不可用时保证报告不断档 |

### 链接规范化（去重关键）

`canonical_arxiv_link()` 统一为 `https://arxiv.org/abs/<id>`（去版本号、统一 https）。
搜索 API 给 `.../abs/2609.12345v1`，RSS 给 `.../abs/2609.12345`，不统一会让
`UNIQUE(link)` 去重失效、同一篇论文重复入库。

升级旧库的迁移在 **`app/database.py: normalize_arxiv_links()`**，由 `init_db()`
调用（因此 `run.bat` 与每日任务启动时都会执行）：幂等、只 UPDATE 不删除，
若规范形式已被占用则跳过该行（避免唯一约束冲突与收藏级联删除）。

### 入库合并策略（`app/scheduler.py: _persist_papers`）

元数据「只增不减」：

- `authors` / `summary` 取**更长**的一方 —— APS 等源的 RSS 摘要被截断（末尾 `…`），
  补全后的完整摘要不能被后续抓取覆盖回截断版；
- `matched_authors` / `author_group` 仅在新作者列表**至少同样完整**时更新
  （防御任何一层返回不完整作者时抹掉已算出的命中）；
- `date_added` 取**更早**的一方，避免同一篇论文的日期在提交日与公告日之间跳动。

---

## 三、实测与核对结论

- 公告层当天：266 条公告 → 145 篇新论文（115 `new` + 30 `cross`）；
  两层合并（时间窗层用缓存兜底）195 篇，按链接去重无重复。
- 公告层作者字段是**完整**的：每条只有一个 `<dc:creator>` 元素，但元素内容是
  逗号分隔的完整作者列表（266 条中 207 条为多作者）。已与 abs 页面逐条比对一致
  （8 位、5 位作者样例均吻合），feedparser 会把整串放进 `authors[0].name`，
  `_extract_rss_items` 原样保留。
- 每日任务：`rc=0`，arXiv 环节在限流状态下 10 秒内完成（此前会拖到 3–5 分钟）。
- 手动补抓：12.4 秒返回；重复补抓新增 0 篇（幂等）。

---

## 四、可调项与扩展点

| 项 | 位置 / 配置 | 说明 |
|----|------------|------|
| 回溯天数 | `schedule.arxiv_lookback_days`（默认 3） | 时间窗层窗口；漏抓多日或 API 长期受限时可临时调大 |
| 公告源地址 | 源配置 `rss_url`（可选） | 不写则按 `cat:xxx` 自动推导 |
| 单页条数 | `ARXIV_PAGE_SIZE = 1000` | 官方建议单次不超过 1000 条 |
| 缓存时长 | `CACHE_MAX_AGE_HOURS = 48` | 断网/限流时的兜底数据新鲜度 |
| 冷却时长 | `RATE_LIMIT_COOLDOWN_MIN = 90` | 命中 429 后该主机静默时长 |

**若要接入 OAI-PMH**（官方推荐的批量/集合收割接口）：它与搜索 API 同主机、
共享同一限流，因此被限流时同样不可用；接入时沿用 `_arxiv_gate()` 与按主机冷却即可，
参考 `fetch_arxiv_window()` 的结构（`resumptionToken` 翻页、
`metadataPrefix=arXiv`、`set=physics:quant-ph`）。

---

## 五、维护提示

- 上游新版本若改动了 `app/fetcher.py` 的 arXiv 部分，合并时请保留：
  `_arxiv_gate` / `canonical_arxiv_link` / `_arxiv_rss_url` / `_extract_rss_items` /
  `_fetch_arxiv_rss_layer` / `_fetch_arxiv_window_layer` / `_merge_arxiv_papers`
  以及 `fetch_source_text` 的 429 退避与时间预算。
- `app/scheduler.py` 的本地改动集中在 `_persist_papers()` 的 upsert `set_`
  （搜索「元数据只增不减」可定位）。
- `app/database.py` 新增 `normalize_arxiv_links()`，被 `init_db()` 调用。
- 网络现实：该网络只能经 QUIC 访问 arXiv，**不要移除 HTTP/3 回退**。
