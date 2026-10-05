# -*- coding: utf-8 -*-
"""增量模式: 已有一轮完整分类后, 收藏夹里新增了条目, 只处理新增部分。

设计要点（沿用 steam-collection-skill 的结论）:
  step2/preclassification.json 是「已分类基准」的唯一来源, 不另设 state 文件,
  因此重复运行天然幂等。

用法 (在技能根目录运行):
  python scripts/incremental.py detect [--refresh]
      -> step5/diff.json + step5/diff_report.txt
         新增条目写入 step1/items.json（合并后）, 并列出已消失的条目
  位置参数 item_id: 额外强制重分类的条目（上次待定的、用户点名的）
  python scripts/incremental.py prepare [--size 30]
      -> step5/batches/batch_NNN.txt + manifest.json（协议同步骤3）
  python scripts/incremental.py merge
      -> step5/review_labels.json + step5/change_report.txt
  python scripts/incremental.py apply [--dry-run] [--prune] [--confirm]
      -> 备份后合并进 step2/preclassification.json
         --prune 移除已不在收藏夹中的条目（默认保留, 仅报告）
"""

from __future__ import annotations

from _common import (arg_val, has_flag, load_cfg, make_adapter,
                     positional, require_step1, usage)

from core import store
from core.safety import backup, log, read_json, write_json

USAGE = __doc__
STEP5 = store.STEP5


# ----------------------------------------------------------------------
def cmd_detect() -> int:
    cfg = load_cfg()
    collections, items = require_step1()
    force = [p for p in positional() if p != "detect"]

    baseline = {r["item_id"]: r for r in store.load_preclassification()}
    known = set(baseline)

    log.step("步骤5 detect：对比当前收藏夹与已分类基准")
    adapter = make_adapter(cfg)
    adapter.authenticate()

    live_collections = adapter.list_collections()
    store.save_collections(live_collections)

    current = adapter.fetch_items([])
    adapter.enrich_many(current, cfg, refresh=has_flag("--refresh"))
    store.save_items(current)

    current_ids = {it.id for it in current}
    new_ids = sorted(current_ids - known)
    gone_ids = sorted(known - current_ids)
    forced = [i for i in force if i in current_ids]

    log.ok(f"收藏夹现有 {len(current_ids)} 条 · 已分类基准 {len(known)} 条")
    log.info(f"  新增 {len(new_ids)} 条 · 已消失 {len(gone_ids)} 条 · 强制重分类 {len(forced)} 条")

    new_rows = []
    for iid in new_ids + forced:
        it = next((x for x in current if x.id == iid), None)
        if it is None:
            continue
        new_rows.append({
            "item_id": iid,
            "title": it.title,
            "creator_name": it.creator_name,
            "category_name": it.category_name,
            "tags": it.tags or [],
            "current_collections": [live_collections[c].name for c in it.containers
                                    if c in live_collections],
            "targets": (baseline.get(iid) or {}).get("targets") or [],
            "layer": "incremental",
            "reason": "",
            "link": it.link,
        })

    write_json(store.INCREMENTAL_PATH, {
        "new": new_ids, "gone": gone_ids, "forced": forced,
        "new_rows": new_rows,
    })
    store.save_pending([{
        "item_id": r["item_id"], "title": r["title"],
        "current_collections": r["current_collections"],
    } for r in new_rows])

    report = [
        "# 增量差异报告", "",
        f"- 收藏夹现有：{len(current_ids)}",
        f"- 已分类基准：{len(known)}",
        f"- 新增：{len(new_ids)}",
        f"- 已消失：{len(gone_ids)}（默认保留在总表中，不影响已有收藏夹）",
        "",
    ]
    if gone_ids:
        report += ["## 已消失的条目", ""] + [f"- {i}" for i in gone_ids[:200]] + [""]
    (STEP5 / "diff_report.txt").write_text("\n".join(report), encoding="utf-8")

    log.ok(f"已写入 {store.INCREMENTAL_PATH} 与 step5/diff_report.txt")
    log.info("")
    log.info(f"**汇报**：新增 {len(new_ids)} 条、消失 {len(gone_ids)} 条。**等待确认**。")
    log.info("确认后： python scripts/incremental.py prepare")
    return 0


# ----------------------------------------------------------------------
def cmd_prepare() -> int:
    size = max(5, int(arg_val("--size", 30)))
    diff = read_json(store.INCREMENTAL_PATH, {}) or {}
    rows = diff.get("new_rows") or []
    if not rows:
        log.warn("没有新增条目，无需处理")
        return 0

    batch_dir = STEP5 / "batches"
    (STEP5 / "results").mkdir(parents=True, exist_ok=True)
    batch_dir.mkdir(parents=True, exist_ok=True)

    taxonomy = store.load_taxonomy()
    cat_names = [c.name for c in taxonomy.categories if not c.is_fallback] if taxonomy else []

    items = {it.id: it for it in store.load_items()}
    from review_tools import _render_batch  # noqa: PLC0415 - 与步骤3 共用渲染逻辑

    manifest, n = [], 0
    for start in range(0, len(rows), size):
        n += 1
        chunk = rows[start : start + size]
        path = batch_dir / f"batch_{n:03d}.txt"
        path.write_text(_render_batch(n, chunk, items, cat_names), encoding="utf-8")
        manifest.append({"batch": n, "file": path.name, "count": len(chunk),
                         "item_ids": [c["item_id"] for c in chunk]})

    write_json(batch_dir / "manifest.json",
               {"batch_size": size, "batches": manifest, "total": len(rows)})
    log.ok(f"已切分 {n} 个批次，共 {len(rows)} 条 -> {batch_dir}")
    log.info("**汇报**：批次数量。**等待确认**后复核。")
    return 0


def cmd_merge() -> int:
    import importlib

    rt = importlib.import_module("review_tools")
    store.REVIEW_LABELS_PATH = STEP5 / "review_labels.json"
    return rt.cmd_merge()


def cmd_apply() -> int:
    import importlib

    rt = importlib.import_module("review_tools")
    store.REVIEW_LABELS_PATH = STEP5 / "review_labels.json"
    rc = rt.cmd_apply()
    if rc:
        return rc
    if has_flag("--prune"):
        log.info("--prune：从总表中移除已不在收藏夹中的条目")
        if has_flag("--confirm") and not has_flag("--dry-run"):
            baseline = {r["item_id"]: r for r in store.load_preclassification()}
            live = {it.id for it in store.load_items()}
            kept = [r for r in baseline.values() if r["item_id"] in live]
            removed = len(baseline) - len(kept)
            backup(store.PRECLASSIFICATION_PATH)
            store.save_preclassification(kept)
            log.ok(f"已移除 {removed} 条，保留 {len(kept)} 条")
    return 0


# ----------------------------------------------------------------------
COMMANDS = {"detect": cmd_detect, "prepare": cmd_prepare,
            "merge": cmd_merge, "apply": cmd_apply}


def main() -> int:
    pos = positional()
    cmd = pos[0] if pos and pos[0] in COMMANDS else ""
    if has_flag("--help", "-h") or not cmd:
        usage(USAGE)
    return COMMANDS[cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
