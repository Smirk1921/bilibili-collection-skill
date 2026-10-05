# -*- coding: utf-8 -*-
"""步骤1: 抓取收藏夹与视频列表 -> step1/collections.json + step1/items.json

数据来源分层（详见 references/methods-fetch.md）:
  [登录] 浏览器扫码    Playwright 驱动系统 Chrome, 抓 Cookie 存本地
  [列表] 收藏夹接口    需登录; 未登录时接口返回 code=0 且 data=null, 必须显式校验
  [富化] 视频详情接口  逐条补 标签/分区/简介; 分区名需靠 data/partition_map.json 反查

用法:
  python scripts/fetch_library.py                # 抓取（已有缓存则复用）
  python scripts/fetch_library.py --login        # Cookie 缺失/过期时, 浏览器登录一次
  python scripts/fetch_library.py --refresh      # 忽略缓存, 全量重抓
  python scripts/fetch_library.py --dry-run      # 只报告将要做什么, 不写文件
依赖: python3 + requests ; --login 另需 playwright (浏览器用系统 Chrome)
"""

from _common import arg_val, has_flag, load_cfg, make_adapter, usage

from core import store
from core.safety import log

USAGE = __doc__


def main() -> int:
    if has_flag("--help", "-h"):
        usage(USAGE)

    cfg = load_cfg()

    if has_flag("--login"):
        from core.adapters.bilibili import browser_login

        browser_login(cfg)
        log.ok("登录完成。现在可以运行： python scripts/fetch_library.py")
        return 0

    refresh = has_flag("--refresh")
    dry_run = has_flag("--dry-run")

    log.step("步骤1 抓取收藏夹与视频列表")
    adapter = make_adapter(cfg)
    adapter.authenticate()

    log.info("读取收藏夹列表…")
    collections = adapter.list_collections()
    log.ok(f"发现 {len(collections)} 个收藏夹")
    for c in collections:
        tag = "  [收件箱]" if c.is_inbox else ""
        log.info(f"    {c.count:>5}  {c.name}{tag}")

    if dry_run:
        log.warn("DRY-RUN：未写入任何文件。")
        return 0

    store.save_collections(collections)

    log.info("读取条目（去重后按 id 归并所属收藏夹）…")
    items = adapter.fetch_items([])
    log.ok(f"共 {len(items)} 个去重条目")

    log.info("富化标签/分区/简介…")
    adapter.enrich_many(items, cfg, refresh=refresh)

    store.save_items(items)
    log.ok(f"已写入 {store.COLLECTIONS_PATH}")
    log.ok(f"已写入 {store.ITEMS_PATH}")

    unavailable = sum(1 for it in items if it.unavailable)
    if unavailable:
        log.warn(f"{unavailable} 个条目已失效（被删除/设为私密），将原样保留、不做任何改动")

    log.info("")
    log.info("**汇报内容**：收藏夹数量、条目总数、失效条目数。**等待确认**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
