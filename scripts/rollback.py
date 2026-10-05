# -*- coding: utf-8 -*-
"""步骤4c: 回滚——把加进去的移除、移出来的加回去

依据 step4/rollback.jsonl（每次写入逐条追加的完整账目）。
本工具创建的收藏夹不会被自动删除，只会列出来由用户决定。

用法:
  python scripts/rollback.py                     # 预览会还原什么
  python scripts/rollback.py --confirm           # 真正还原
  python scripts/rollback.py --run-id <id>       # 只还原某一次运行
"""

from _common import arg_val, has_flag, load_cfg, make_adapter, usage

from core import apply as apply_mod
from core.safety import log

USAGE = __doc__


def main() -> int:
    if has_flag("--help", "-h"):
        usage(USAGE)

    cfg = load_cfg()
    confirm = has_flag("--confirm")
    adapter = make_adapter(cfg, need_login=confirm)

    log.step("回滚")
    apply_mod.rollback(adapter, confirm=confirm, run_id=arg_val("--run-id"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
