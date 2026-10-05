"""计划阶段：把 L1 结果 + 复核结果合并成可执行的收藏计划。平台无关。

语义是「追加收藏」而不是「移动」：条目被加入目标收藏夹，原有收藏夹默认不动。
唯一例外是收件箱（B站默认收藏夹）——归类后会从中移除。
"""

from __future__ import annotations

import time
from collections import defaultdict

from .model import Collection, Item, Taxonomy
from .safety import log
from .text import norm_name

UNDECIDED = "待定"


def targets_of(label: dict) -> list[str]:
    """兼容两种写法：targets 列表，或单个 target。"""
    ts = label.get("targets")
    if isinstance(ts, list):
        out = [t.strip() for t in ts if t and t.strip()]
        if out:
            return out
    single = (label.get("target") or "").strip()
    return [single] if single else []


def build_plan(
    verdicts: dict,
    review_labels: list[dict] | None,
    items: list[Item],
    collections: dict[str, Collection],
    taxonomy: Taxonomy,
    cfg: dict,
) -> dict:
    ex = cfg.get("execute", {})
    cls = cfg.get("classify", {})
    min_conf = float(cls.get("ai_min_confidence", 0.5))
    max_targets = int(ex.get("max_targets_per_item", 3))
    empty_inbox = bool(ex.get("empty_inbox", True))

    by_id = {it.id: it for it in items}
    by_name = {norm_name(c.name): c for c in collections.values()}
    inbox_ids = {c.id for c in collections.values() if c.is_inbox}

    # ---- 收集决定：item_id -> [目标分类名] ----
    decisions: dict[str, dict] = {}

    for a in verdicts.get("assignments") or []:
        entry = decisions.setdefault(a["item_id"], {"targets": [], "layer": a.get("layer"),
                                                    "score": a.get("score"), "reason": a.get("reason")})
        for t in a.get("targets") or []:
            if t not in entry["targets"]:
                entry["targets"].append(t)

    merged = 0
    for lab in review_labels or []:
        iid = lab.get("item_id") or lab.get("id")
        if not iid or iid not in by_id:
            continue
        if float(lab.get("confidence") or 0) < min_conf:
            continue
        targets = [t for t in targets_of(lab) if t and t != UNDECIDED]
        if not targets:
            continue
        # 复核结果是权威修正：**覆盖**而不是追加 L1 的判定。
        # 追加会让规则判错的目标和人工修正的目标同时生效，两边都进计划。
        decisions[iid] = {
            "targets": list(dict.fromkeys(targets)),
            "layer": "review",
            "score": lab.get("confidence"),
            "reason": lab.get("reason") or "",
        }
        merged += 1

    # ---- 目标分类 -> 已有收藏夹 id（不存在则标记待新建）----
    target_ids: dict[str, str | None] = {}
    for d in decisions.values():
        for name in d["targets"]:
            if name not in target_ids:
                hit = by_name.get(norm_name(name))
                target_ids[name] = hit.id if hit else None
    to_create = sorted(n for n, cid in target_ids.items() if cid is None)

    # ---- 生成逐条目操作 ----
    operations: list[dict] = []
    unresolved: list[dict] = []
    already = 0
    truncated = 0

    for iid, d in decisions.items():
        item = by_id.get(iid)
        if item is None:
            continue
        targets = d["targets"][:max_targets]
        if len(d["targets"]) > max_targets:
            truncated += 1

        current_names = [collections[c].name for c in item.containers if c in collections]
        add = [t for t in targets if t not in current_names]
        already += len(targets) - len(add)

        # 只有确实归进了新收藏夹才清收件箱，避免把没处理好的条目移走
        remove_ids = [c for c in item.containers if c in inbox_ids] if (empty_inbox and add) else []

        if not add and not remove_ids:
            continue

        operations.append({
            "item_id": item.id,
            "title": item.title,
            "creator_name": item.creator_name,
            "link": item.link,
            "add": add,
            "add_ids": [target_ids.get(t) for t in add],
            "remove": [collections[c].name for c in remove_ids if c in collections],
            "remove_ids": remove_ids,
            "current": current_names,
            "layer": d.get("layer"),
            "score": d.get("score"),
            "reason": d.get("reason") or "",
            "extra": item.extra,
        })

    per_target: dict[str, int] = defaultdict(int)
    for op in operations:
        for t in op["add"]:
            per_target[t] += 1

    plan = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "add",
        "empty_inbox": empty_inbox,
        "collections_to_create": to_create,
        "target_map": target_ids,
        "operations": operations,
        "per_target": dict(sorted(per_target.items(), key=lambda kv: -kv[1])),
        "unresolved": unresolved,
        "summary": {
            "total_items": len(items),
            "decided": len(decisions),
            "review_merged": merged,
            "items_to_touch": len(operations),
            "total_add_ops": sum(len(op["add"]) for op in operations),
            "items_leaving_inbox": sum(1 for op in operations if op["remove"]),
            "multi_target_items": sum(1 for op in operations if len(op["add"]) > 1),
            "already_in_place": len(verdicts.get("in_place") or []) + already,
            "unavailable": len(verdicts.get("unavailable") or []),
            "unresolved": len(unresolved),
            "truncated": truncated,
            "collections_to_create": len(to_create),
        },
    }
    return plan


def print_summary(plan: dict) -> None:
    s = plan["summary"]
    log.info("")
    log.info("  模式            追加收藏（不破坏原收藏夹）")
    log.info(f"  条目总数        {s['total_items']}")
    log.info(f"  需要处理        {s['items_to_touch']}  ({s['total_add_ops']} 次收藏操作)")
    log.info(f"  归入多个收藏夹  {s['multi_target_items']}")
    log.info(f"  移出收件箱      {s['items_leaving_inbox']}")
    log.info(f"  已在正确位置    {s['already_in_place']}")
    log.info(f"  已失效(不动)    {s['unavailable']}")
    log.info(f"  无法处理        {s['unresolved']}")
    if s.get("truncated"):
        log.info(f"  超出上限被截断  {s['truncated']}")
    if plan["collections_to_create"]:
        log.info(f"  需新建收藏夹 {s['collections_to_create']} 个：{plan['collections_to_create']}")
    log.info("")
    log.info("  各目标分类（收藏次数）：")
    for name, n in plan["per_target"].items():
        mark = "" if plan["target_map"].get(name) else " [需新建]"
        log.info(f"    {n:>5}  {name}{mark}")
