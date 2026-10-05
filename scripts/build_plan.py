# -*- coding: utf-8 -*-
"""步骤4a: 生成收藏计划 -> step4/plan.json（不碰线上数据）

把 L1 判定 + 步骤3 复核结果合并成可执行的操作清单：
  - 哪些收藏夹需要新建
  - 每条目要加入哪些收藏夹、从哪个收件箱移出
默认 dry-run，只打印影响面统计，不做任何改动。

用法:
  python scripts/build_plan.py              # 生成计划并预览
  python scripts/build_plan.py --report     # 额外导出 Excel/Markdown
"""

from _common import has_flag, load_cfg, require_step1, usage

from core import plan as plan_mod
from core import report as report_mod
from core import store
from core.safety import log

USAGE = __doc__


def main() -> int:
    if has_flag("--help", "-h"):
        usage(USAGE)

    cfg = load_cfg()
    collections, items = require_step1()

    verdicts = store.load_verdicts()
    if not verdicts:
        raise SystemExit("缺少 L1 判定结果，请先运行： python scripts/build_taxonomy.py")

    taxonomy = store.load_taxonomy()
    if taxonomy is None:
        raise SystemExit("缺少类目定义，请先运行： python scripts/build_taxonomy.py")

    labels = store.load_review_labels()
    if labels:
        log.info(f"合并复核结果 {len(labels)} 条")
    else:
        log.warn("未找到复核结果（step3/review_labels.json），本次计划仅含 L1 判定。")
        log.info("  如需复核： python scripts/review_tools.py prepare")

    log.step("步骤4a 生成收藏计划")
    plan = plan_mod.build_plan(verdicts, labels, items, collections, taxonomy, cfg)
    plan_mod.print_summary(plan)
    store.save_plan(plan)
    log.ok(f"已写入 {store.PLAN_PATH}")

    if has_flag("--report"):
        report_mod.report(verdicts, plan, labels, items, collections)

    log.info("")
    log.info(f"**汇报**：需新建 {plan['summary']['collections_to_create']} 个收藏夹、"
             f"处理 {plan['summary']['items_to_touch']} 个条目"
             f"（{plan['summary']['total_add_ops']} 次收藏操作）。**等待确认**。")
    log.info("确认后进入写入： python scripts/apply_collections.py --confirm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
