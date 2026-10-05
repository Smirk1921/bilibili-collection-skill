"""执行与回滚。平台无关——所有平台差异走 Adapter 协议。

安全设计（全部保留自实测有效的版本）：
- 默认 dry-run，必须显式 confirm 才调用写接口
- 每成功一个条目立刻追加回滚日志，中断可续、失败可还原
- 连续失败 3 次自动中止，避免在风控状态下硬撞
- 重复运行会跳过已处理过的条目（断点续跑）
"""

from __future__ import annotations

import time
import uuid

from .paths import STEP4
from .safety import append_jsonl, log, read_json, read_jsonl, write_json

ROLLBACK_PATH = STEP4 / "rollback.jsonl"
CREATED_PATH = STEP4 / "created_collections.json"
MAX_CONSECUTIVE_FAILURES = 3


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def apply_plan(plan: dict, adapter, cfg: dict, confirm: bool = False) -> None:
    ops = plan.get("operations") or []
    to_create = plan.get("collections_to_create") or []
    name_to_id = {k: v for k, v in (plan.get("target_map") or {}).items() if v}

    done_keys = {
        (e.get("item_id"), tuple(sorted(e.get("added_ids") or [])))
        for e in read_jsonl(ROLLBACK_PATH)
    }
    pending_ops = [
        op for op in ops
        if (op["item_id"], tuple(sorted(i for i in op["add_ids"] if i))) not in done_keys
    ]

    log.step("即将执行的改动（追加收藏模式）：")
    log.info(f"  新建收藏夹   {len(to_create)} 个：{to_create}")
    log.info(f"  需处理条目   {len(pending_ops)} 个"
             f"（计划共 {len(ops)}，已完成 {len(ops) - len(pending_ops)}）")
    log.info(f"  移出收件箱   {sum(1 for op in pending_ops if op['remove'])} 个")

    if not confirm:
        log.warn("当前为 DRY-RUN，不会做任何改动。")
        _preview(pending_ops)
        log.info("确认无误后加 --confirm 重新执行。")
        return

    adapter.authenticate()
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    log.step(f"执行 run_id={run_id}")

    # ---- 1) 新建收藏夹 ----
    created = read_json(CREATED_PATH, []) or []
    for name in to_create:
        if name in name_to_id:
            continue
        log.info(f"  新建收藏夹「{name}」…")
        cid = adapter.create_collection(name)
        name_to_id[name] = cid
        created.append({"name": name, "id": cid, "run_id": run_id, "ts": _now()})
        write_json(CREATED_PATH, created)
        log.ok(f"  -> id={cid}")

    # ---- 2) 逐条追加收藏 ----
    done = failed = consecutive = 0
    total = len(pending_ops)

    for op in pending_ops:
        add_ids = [name_to_id.get(n) or old for n, old in zip(op["add"], op["add_ids"])]
        add_ids = [i for i in add_ids if i]
        remove_ids = [i for i in (op.get("remove_ids") or []) if i]
        if not add_ids and not remove_ids:
            continue

        try:
            adapter.assign(op["item_id"], add_ids, remove_ids, extra=op.get("extra") or {})
        except Exception as exc:  # noqa: BLE001 - 单条失败不中断整体
            failed += 1
            consecutive += 1
            log.err(f"  「{(op.get('title') or op['item_id'])[:34]}」失败：{exc}")
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                log.err(f"连续 {consecutive} 次失败，已中止。可能触发风控，请稍后再试。")
                _finish(done, total, failed)
                return
            continue

        consecutive = 0
        done += 1
        append_jsonl(ROLLBACK_PATH, {
            "ts": _now(), "run_id": run_id,
            "item_id": op["item_id"], "title": op.get("title"),
            "added_ids": add_ids, "added_names": op["add"],
            "removed_ids": remove_ids, "removed_names": op.get("remove") or [],
        })
        if done % 10 == 0 or done == total:
            log.info(f"  已处理 {done}/{total}  (失败 {failed})")

    _finish(done, total, failed)


def _finish(done: int, total: int, failed: int) -> None:
    log.info("")
    log.ok(f"完成：处理 {done}/{total} 个条目，失败 {failed} 个")
    if done:
        log.info(f"  回滚日志：{ROLLBACK_PATH}")
        log.info("  如需还原： python scripts/rollback.py --confirm")


def _preview(ops: list[dict], limit: int = 25) -> None:
    log.info("")
    log.info("  处理预览：")
    for op in ops[:limit]:
        add = " + ".join(op["add"]) or "-"
        rm = f"   (移出 {'/'.join(op['remove'])})" if op["remove"] else ""
        log.info(f"    {(op.get('title') or op['item_id'])[:40]:42} -> {add}{rm}")
    if len(ops) > limit:
        log.info(f"    …另有 {len(ops) - limit} 个")


# ----------------------------------------------------------------------
def rollback(adapter, confirm: bool = False, run_id: str | None = None) -> None:
    """把加进去的移除、移出来的加回去。"""
    entries = read_jsonl(ROLLBACK_PATH)
    if not entries:
        log.warn("没有回滚日志，无需操作")
        return
    if run_id:
        entries = [e for e in entries if e.get("run_id") == run_id]
        if not entries:
            log.warn(f"没有 run_id={run_id} 的记录")
            return

    log.step(f"将还原 {len(entries)} 个条目的收藏状态")
    log.info(f"  移除本次加入的收藏 {sum(len(e.get('added_ids') or []) for e in entries)} 处")
    log.info(f"  恢复本次移出的收藏 {sum(len(e.get('removed_ids') or []) for e in entries)} 处")

    if not confirm:
        log.warn("当前为 DRY-RUN。确认后加 --confirm 重新执行。")
        return

    adapter.authenticate()
    restored = failed = 0
    for e in entries:
        try:
            adapter.assign(
                e["item_id"],
                add_ids=[i for i in (e.get("removed_ids") or []) if i],
                remove_ids=[i for i in (e.get("added_ids") or []) if i],
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.err(f"  还原「{(e.get('title') or e['item_id'])[:34]}」失败：{exc}")
            continue
        restored += 1
        if restored % 10 == 0:
            log.info(f"  已还原 {restored}/{len(entries)}")

    log.ok(f"回滚完成：{restored}/{len(entries)}，失败 {failed}")

    created = read_json(CREATED_PATH, []) or []
    if created:
        log.info("")
        log.info("以下收藏夹是本工具创建的，若确认不需要可手动删除：")
        for c in created:
            log.info(f"  {c['name']}  (id={c['id']})")
