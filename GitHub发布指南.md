# 把 RNTS 放到 GitHub —— 操作指南

> 适用版本：v1.1.1。本文所有命令都在项目根目录（`rnts\`）里执行。
> 仓库里已经放好了 `.gitignore` 和 `.gitattributes`，**不要删**。

---

## 零、当前决定与前置说明

**本项目的决定（2026-09-15）：先建 GitHub 私有仓库，小范围测试；以后想公开随时可以转。**

| # | 事项 | 说明 |
|---|------|------|
| 1 | **可见性** | **私有**。GitHub 没有"拿到链接就能看"的分享方式，私有仓库必须把对方加成协作者（见第二·五节的邀请步骤）。以后转公开只需改一个开关，但**转公开时全部提交历史一起公开**，所以私有期间也别往里塞敏感内容。 |
| 2 | **许可证** | 已放 MIT `LICENSE`（代码含合作者贡献，需其同意；leader 已确认获授权）。 |
| 3 | **数据库 / 配置** | 都不传。`data/` 与 `config/config.yaml` 已在 `.gitignore` 里，仓库只跟踪模板 `config/config.example.yaml`。 |
| 4 | **国内网络** | GitHub 在国内是「时通时断」而非完全不通（详见第七节）。如果你或协作者拉不动代码，备选方案见第七节末尾。 |

---

## 一、本地仓库（已就绪）

```
rnts/
├── .gitignore      忽略 .venv/ data/ *.log *.db、config/config.yaml、IDE 目录
├── .gitattributes  ★关键：.bat/.ps1 强制 CRLF，.sh/.py 强制 LF
├── LICENSE         MIT
└── ...（48 个文件已 git add 进暂存区）
```

**为什么必须有 `.gitattributes`**：cmd 在中文 Windows 上按 GBK 逐字节解析 `.bat`，
换行一旦被 git 改成 LF 就会吞行，脚本直接跑不起来（v1.0.1 时期踩过）。
跨机器 clone 时这条规则能保证 `.bat` 永远是 CRLF。

---

## 二、配置身份并提交（做一次即可）

```bash
cd /d <项目根目录>          # 例如 D:\RNTS

git config user.name  "你的 GitHub 用户名"
git config user.email "你的 GitHub 注册邮箱"   # 要用 GitHub 里验证过的那个，否则贡献不计入你

git commit -m "RNTS v1.1.1: arXiv 全量抓取 + 按日期补抓"
git tag -a v1.1.1 -m "v1.1.1"
```

> 邮箱填错也不用慌：push 之前 `git commit --amend --reset-author` 能改。

---

## 二·五、config.yaml 不入库（重要）

`config/config.yaml` 是**每个人自己的配置**（关键词、关注作者、云盘目录），已经在
`.gitignore` 里，仓库只跟踪模板 `config/config.example.yaml`。

- 好处：任何人 `git pull` / 整包覆盖升级，都不会碰到他的个人配置；你的关键词与作者列表也不会被公开。
- 代码兜底：`app/config.py` 的 `_ensure_config_file()` 在 `config.yaml` 不存在时会自动从模板复制一份
  （旧版本缺文件会直接 `FileNotFoundError` 起不来）。所以全新 clone 后直接跑就行。
- 分发的 zip 包里**只带 `config.example.yaml`**，不带 `config.yaml` —— 这样即使整包解压覆盖，
  也不会把使用者已配好的 config 冲掉。

> 后续合并他人版本时，把 `_ensure_config_file()` 是否还在 `app/config.py` 里，
> 并入「每次合并后必检」清单（与那三条 .bat 防护并列）。

---

## 三、创建远端仓库并推送

### 方式 A：用 GitHub CLI（推荐，一条命令）

装 CLI（任选其一）：

```powershell
winget install --id GitHub.cli
```

然后：

```bash
gh auth login            # 浏览器登录，选 GitHub.com → HTTPS
gh repo create RNTS --private --source=. --remote=origin --push
#                  ↑ 要公开就把 --private 换成 --public
```

这一条会同时完成：建仓库 → 关联 origin → 推送 `main`。

### 方式 B：网页手动建

1. 打开 <https://github.com/new>，Repository name 填 `RNTS`，选 Private，**不要勾**任何
   "Add a README / .gitignore / license"（本地已经有了，勾了会冲突）。
2. 建好后 GitHub 会给一段命令，复制粘贴即可：

```bash
git remote add origin https://github.com/<你的用户名>/RNTS.git
git branch -M main
git push -u origin main
git push origin v1.1.1
```

---

## 四、让别人看到代码（私有协作）与以后转公开

### 4.1 私有仓库：邀请协作者（GitHub 没有匿名分享链接）

私有仓库的访问必须**授权到具体账号**，对方也必须有 GitHub 账号：

1. 打开仓库 → **Settings** → 左侧 **Collaborators**（个人账号仓库）→ 绿色 **Add people**；
2. 输入对方的 GitHub 用户名或注册邮箱 → 选中 → 选权限级别 → **Add**：

   | 级别 | 能做什么 | 给人建议 |
   |---|---|---|
   | Read | 只看代码、开 Issue | 只想让他跑一跑、提问题 |
   | Write | 能推代码、开分支、管 Issue | 一起改代码就用这个 |
   | Admin | 能改仓库设置、删仓库 | 只给共同负责人 |

3. 对方会收到邮件通知，**接受后**才能打开。邀请发出后，Collaborators 页面的待接受邀请处可以
   **复制邀请链接**直接发给他（微信/邮件都行），但他仍需登录 GitHub 账号才能接受。
4. 免费版的公开仓库与**私有仓库都支持无限协作者**（更早的版本曾限私有仓库 3 人，现已放开）。

> 只是想让对方「拿去跑跑看」而不参与开发：直接发 zip 包（`RNTS_package_v1.1.1_fixed.zip`）更省事，
> 他不需要 GitHub 账号，包内也不含你的配置和数据库。

### 4.2 以后想公开

Settings → General → 拉到底 **Danger Zone** → **Change repository visibility** → Public。
改之前把下面这份清单过一遍：

- [ ] 明白**转公开时全部提交历史一起公开**（私有期间的提交也在内），确认历史里没有敏感内容
- [ ] 全文搜一遍有没有本机信息：`git grep -n "<你的Windows用户名>\|C:\\\\Users\|<项目绝对路径>"`
- [ ] `LOCAL_DEPLOY.md`、`操作说明_每日任务.md` 里若有你的绝对路径，改成占位符
- [ ] `config/` 目录里只有 `config.example.yaml`（`config.yaml` 必须仍在 `.gitignore` 里）
- [ ] README 顶部写清：项目用途 + 部署方式 + 许可证（LICENSE 已就位）
- [ ] 与合作者确认公开这件事（代码含他的贡献）

> 想稳妥一点：先建一个**临时公开测试仓**验证一遍效果，确认无误再改正式仓库的可见性。

---

## 五、日常怎么维护

```bash
# 改完代码，提交前跑一次自检（脚本编码/换行，防止 .bat 又坏掉）
python check_scripts.py

git add -A
git commit -m "说明改了什么"
git push
```

**发新版**：改 `app/__init__.py` 里的 `__version__`（网页页脚、报告页脚、接口信息都读它），
然后：

```bash
git commit -am "v1.1.2" && git tag -a v1.1.2 -m "v1.1.2" && git push && git push origin v1.1.2
```

**合并合作者的新包后**，务必复检这三条（v1.1.0 / v1.1.1 两次都被覆盖过）：

1. `check_scripts.py` 里还有 `_scan_block_parens`（if/for 块内括号检查）
2. `run_daily_task.bat` 的日志行是 `>>"data\daily_task.log" echo [RNTS] FINISHED rc=%RC%`
   （重定向必须写在前面，否则 `%RC%` 后的数字会被当流号吞掉）
3. `run.bat` 里用的是 `timeout.exe` 而不是 `timeout`（PATH 里有 Git Bash 时会命中 GNU timeout 报错）

---

## 六、别人拿到仓库怎么跑

README 里已经写了三分钟部署，核心两条：

```
双击 setup_env.bat    # 建 .venv 并装依赖
双击 run.bat          # 启动，浏览器打开 http://localhost:8000
```

数据库、报告、日志都是首次运行时自动生成，不在仓库里。

**注意**：仓库里只有 `config/config.example.yaml`，首次运行会自动生成 `config.yaml`，
但那份是示例（9 条通用关键词 + 占位作者），要让他自己去「⚙️ 设置」页改成自己的。

---

## 七、国内访问 GitHub 与备选方案

### 7.1 现状：不是上不去，是不稳定

| 现象 | 说明 |
|---|---|
| 网页时通时断 | 能打开但经常转圈或 502，重试多半能成 |
| `git clone/push` 慢或超时 | 小仓库一般能过；大仓库容易断在中途 |
| `raw.githubusercontent.com` | 基本不通（下载单文件常用不了） |
| SSH（22 端口） | 经常被阻；改用 HTTPS 或让 SSH 走 443 端口成功率高 |
| 2025-04 | 曾出现短时「封禁中国 IP」事件（官方称技术故障）；GitLab 已退出中国市场 |

**关键**：私有仓库**不能**用 ghproxy 那类公开加速镜像（只对公开内容有效）。
私有协作要么自己解决网络，要么换国内平台。

### 7.2 备选一：GitHub 主仓 + Gitee 镜像（推荐，改动最小）

本地仓库配两个远端，一次推两边（本项目代码不到 1MB，推两处零成本）：

```bash
git remote add origin https://github.com/<用户名>/RNTS.git
git remote add gitee  https://gitee.com/<用户名>/RNTS.git

git push -u origin main     # GitHub（正本）
git push gitee main         # Gitee（国内通道 + 备份）
```

以后拉对方的改动：`git pull origin main`，再 `git push gitee main` 同步过去。

### 7.3 备选二：主仓直接放 Gitee

| 项 | Gitee 社区版（免费） |
|---|---|
| 仓库数 | 1000 个，公私有不限 |
| 单仓库容量 | 500MB（单文件最大 50MB，本项目远低于上限） |
| 私有仓库协作人数 | 个人账号下所有私有仓库**合计 5 人** |
| 要求 | 需实名（国内手机号）；公开仓库要过 1-3 天审核 |

Gitee 支持「从 GitHub 导入仓库」一键搬，也有仓库镜像同步功能，主次可以随时调换。

### 7.4 其它可选

- **腾讯云 CNB（cnb.cool）**：100GiB 代码存储 + 1600 核时/月免费算力，国内速度快（功能对这个小项目过剩）
- **阿里云云效 Codeup**：基础版免费、人数不限、单仓 10GB
- **GitCode（CSDN）/ CODING（腾讯云）**：免费额度约 10GB、5 人
- **自建 Gitea**：有 NAS / 服务器时最自主，但要自己负责备份与运维

> RNTS 运行本身**不依赖 GitHub 网络**（只有拉代码那一步需要），所以发 zip 包、
> 走 Gitee、或双 remote 都能满足小范围测试。
