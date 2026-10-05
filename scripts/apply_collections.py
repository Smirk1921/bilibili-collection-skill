# -*- coding: utf-8 -*-
"""步骤4b: 执行收藏计划（建夹 + 追加收藏）

语义是「追加收藏」而不是「移动」：条目被加入目标收藏夹，原有收藏夹默认不动，
只有收件箱（默认收藏夹）会在归类后被清空。不会删除任何条目。

安全机制:
  - 默认 dry-run，必须显式 --confirm 才调用写接口
  - 每成功一条立刻追加 step4/rollback.jsonl，中断可续、失败可还原
  - 连续失败 3 次自动中止，避免在风控状态下硬撞
  - 重复运行自动跳过已处理过的条目（断点续跑）

用法:
  python scripts/apply_collections.py            # 预览（不改动任何数据）
  python scripts/apply_collections.py --confirm  # 真正执行
"""

from _common import has_flag, load_cfg, make_adapter, usage

from core import apply as apply_mod
from core import store
from core.safety import log

USAGE = __doc__


def main() -> int:
    if has_flag("--help", "-h"):
        usage(USAGE)

    cfg = load_cfg()
    plan = store.load_plan()
    if not plan:
        raise SystemExit("缺少计划，请先运行： python scripts/build_plan.py")

    adapter = make_adapter(cfg, need_login=has_flag("--confirm"))

    log.step("步骤4b 执行收藏计划")
    if has_flag("--confirm"):
        log.warn("即将真实写入 B站 收藏夹。请确认上面的影响面统计无误。")

    apply_mod.apply_plan(plan, adapter, cfg, confirm=has_flag("--confirm"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
