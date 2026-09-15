"""每日任务命令行入口 —— 由 run_daily_task.bat / run_daily_task.ps1 调用。

独立于 Web 服务运行：先配置好 logging（此前 bat 内联 python -c 从未执行
logging.basicConfig，导致 INFO 级抓取日志全部丢失、异常只有裸行），再执行
一次完整的抓取-过滤-入库-报告流程。

输出约定（bat 脚本依赖，勿改）：
- stdout 打印 stats 字典与 RNTS_STATUS|... 摘要行
- 向 data/daily_status.log 追加一行摘要
- 退出码 0=success，1=error
"""

import logging
import sys
from datetime import datetime


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    from app.database import init_db
    from app.scheduler import run_daily_fetch

    init_db()
    r = run_daily_fetch()
    s = 0 if r.get("status") == "success" else 1
    print(r)
    print(
        f"RNTS_STATUS|fetched={r.get('papers_fetched')}|matched={r.get('papers_matched')}"
        f"|new={r.get('papers_new')} purged={r.get('papers_purged')}"
        f"|status={r.get('status')}|error={r.get('error')}"
    )
    ts = datetime.now().replace(microsecond=0).isoformat()
    with open("data/daily_status.log", "a", encoding="utf-8") as f:
        f.write(
            f"{ts} status={r.get('status')} fetched={r.get('papers_fetched')} "
            f"matched={r.get('papers_matched')} new={r.get('papers_new')} "
            f"purged={r.get('papers_purged')} rc={s}\n"
        )
    return s


if __name__ == "__main__":
    sys.exit(main())
