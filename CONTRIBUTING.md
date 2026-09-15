# 参与 RNTS 开发

感谢你愿意一起改进 RNTS。本文说明开发环境、协作流程，以及**这个项目特有的几条红线** —— 它们都是从实际踩坑里总结出来的，动手前请先读完。

---

## 一、准备开发环境

```bash
git clone git@github.com:ZiyueHua/RNTS.git
cd RNTS
git config user.name  "你的GitHub用户名"
git config user.email "你的GitHub注册邮箱"   # 必须是 GitHub 上验证过的，否则贡献不计入你
```

然后双击 `setup_env.bat` 建虚拟环境，双击 `run.bat` 启动，浏览器打开 <http://localhost:8000>。

### 如果你的网络访问 GitHub 不稳定

国内部分网络会出现「`github.com` 的 443 被间歇性丢包」的现象（表现为网页转圈、`git push` 报 `Empty reply from server`）。**SSH 通道不受影响**，建议统一走 SSH 并把端口固定到 443，在 `~/.ssh/config` 里加：

```
Host github.com
    HostName ssh.github.com
    Port 443
    User git
    IdentityFile ~/.ssh/id_ed25519
```

---

## 二、分支与 PR 流程

**不要直接往 `main` 推。** 流程是：

```bash
git switch main && git pull              # 先同步
git switch -c feature/简短描述            # 开分支
# ... 改代码 ...
python check_scripts.py                  # ★ 提交前必跑
git add -A
git commit -m "一句话说明改了什么"
git push -u origin feature/简短描述
```

然后在 GitHub 上开 Pull Request，等待 review 后由维护者 **Squash merge**（保持 `main` 历史干净，一个 PR 一个提交）。

保持你的分支最新：

```bash
git pull --rebase origin main
```

---

## 三、项目红线（务必遵守）

### 1. `.gitattributes` 绝对不能删

它强制 `.bat`/`.ps1` 用 CRLF、`.sh`/`.py` 用 LF。中文 Windows 的 cmd 会**按 GBK 逐字节解析 `.bat`**，换行一旦被 git 改成 LF 就会吞行，脚本直接跑不起来（v1.0.1 时期踩过这个坑）。

### 2. 提交前必须跑 `python check_scripts.py`

它会检查：

| 文件类型 | 要求 | 原因 |
|---|---|---|
| `.bat` / `.cmd` | 纯 ASCII + CRLF | cmd 用 GBK 解释文件，UTF-8 中文的尾字节可能被当成 GBK 前导字节，吞掉换行 |
| `.ps1` | UTF-8 **带 BOM** + CRLF | PowerShell 5.1 无 BOM 会按 ANSI 读，中文变乱码 |
| `.sh` | UTF-8 无 BOM + LF | Linux / macOS |
| `.py` | UTF-8 无 BOM，且 `print` 的内容必须能用 GBK 编码 | 否则输出到中文 Windows 控制台（cp936）会抛 `UnicodeEncodeError` |
| `.bat` | `if`/`for` 的 `( ... )` 块内不得出现括号（**包括 echo 的文本**） | cmd 把 `)` 当块结束符，块被提前截断，脚本一启动就报 `. was unexpected at this time.` |

### 3. 合并后必检这三条（v1.1.0、v1.1.1 两次被覆盖过）

```bash
grep -n "_scan_block_parens" check_scripts.py        # 1. 块内括号检查还在
grep -n 'FINISHED rc=' run_daily_task.bat            # 2. 日志行重定向写在前面
grep -n "timeout.exe" run.bat                        # 3. 用的是 timeout.exe 不是 timeout
```

- **第 2 条**：必须是 `>>"data\daily_task.log" echo [RNTS] FINISHED rc=%RC%`。重定向写在命令后面时，`%RC%` 后面的数字会被 cmd 当成流号吞掉。
- **第 3 条**：PATH 里有 Git Bash 时，`timeout` 会命中 GNU 版并报错，必须写 `timeout.exe`。

### 4. `config/config.yaml` 永不入库

它是**每个人自己的配置**（关键词、关注作者、云盘目录），已在 `.gitignore` 里，仓库只跟踪模板 `config/config.example.yaml`。

- `app/config.py` 的 `_ensure_config_file()` 在 `config.yaml` 不存在时会自动从模板复制一份，所以全新 clone 后直接跑即可；
- 分发 zip 包里**只带 `config.example.yaml`** —— 这样即使整包解压覆盖，也不会冲掉使用者已配好的配置。

### 5. 版本号唯一来源是 `app/__init__.py` 的 `__version__`

网页页脚、报告页脚、接口信息都读它。发新版时只改这一处，别在别的地方硬编码版本号。

---

## 四、数据与调试

- 数据库：`data/rnts.db`（SQLite，首次启动自动建表），不在仓库里；
- 日志：`data/daily_task.log`；
- 报告：`data/reports/`；
- 调试时手动触发一次抓取：`POST /api/v1/papers/fetch`，或直接点网页导航栏的「⟳ 手动抓取」；
- 程序**不会**自动改写 `config.yaml`，需要整理三个列表顺序时手动跑 `sort_config.bat`（加 `--check` 只预览）。

---

## 五、发布新版本

```bash
# 1. 改 app/__init__.py 里的 __version__
# 2. 写一份 更新说明_v1.x.y.md
git commit -am "v1.x.y: 说明改了什么"
git tag -a v1.x.y -m "v1.x.y"
git push && git push origin v1.x.y
```

之后在 GitHub 的 **Releases → Create a new release** 里选该标签，粘贴更新说明、上传分发包。

---

## 六、许可

项目采用 MIT 许可（见 `LICENSE`）。提交代码即表示你同意以该许可分发你的贡献。
