"""L1 分类：手写规则 + 体裁级联 + 收藏夹画像匹配。平台无关。

判定顺序：
  1. 手写硬规则（用户自定义，命中即归类，优先级最高）
  2. 体裁维度（走 rules.apply_cascade：明确声明 -> 原生分类 -> 文本线索）
  3. 主题维度（画像打分，够高直接归类，否则进待复核清单）

产出 data/verdicts.json 与 data/pending_for_review.json。
"""

from __future__ import annotations

import time
from collections import Counter

from .model import Category, Collection, Item, Taxonomy
from .rules import apply_cascade, apply_manual_rules
from .scoring import build_idf, build_profiles, rank_profiles
from .safety import log
from . import taxonomy as tax_mod
from .text import norm_name

UNAVAILABLE_TARGET = "⚠ 已失效"


def classify(
    items: list[Item],
    collections: dict[str, Collection],
    taxonomy: Taxonomy,
    rules: dict,
    cfg: dict,
) -> tuple[dict, list[dict]]:
    """返回 (verdicts, pending)。调用方负责落盘。"""
    cls = cfg.get("classify", {})
    weights = cls.get("weights") or {}
    high = float(cls.get("high_confidence", 0.62))
    margin = float(cls.get("margin", 0.08))
    reorg_margin = float(cls.get("reorg_margin", 0.10))
    secondary_min = float(cls.get("secondary_confidence", 0.45))
    min_size = int(cls.get("min_collection_size", 3))
    skip_names = set(cfg.get("execute", {}).get("skip_collections") or [])

    form_dim = _dim(taxonomy, "form")
    subject_dim = _dim(taxonomy, "subject")

    log.step("构建收藏夹画像…")
    profiles = build_profiles(
        items, collections,
        skip_names=skip_names,
        treat_inbox_as_excluded=True,
        min_size=min_size,
    )
    log.info(f"  可用作分类目标的收藏夹：{len(profiles)} 个")

    log.step("计算 IDF 权重…")
    tag_idf, tok_idf = build_idf(items)

    assignments: list[dict] = []
    in_place: list[dict] = []
    unavailable: list[dict] = []
    pending: list[dict] = []

    for item in items:
        current = [collections[c].name for c in item.containers if c in collections]
        if item.unavailable:
            unavailable.append({
                "item_id": item.id,
                "title": item.title,
                "creator_name": item.creator_name,
                "link": item.link,
                "current_collections": current,
                "reason": item.unavailable_reason or "条目不可访问",
            })
            continue

        # --- 1) 手写硬规则 ---
        manual = apply_manual_rules(item, cfg.get("rules") or [])
        if manual:
            target, why = manual
            assignments.append(_verdict(item, current, [target], 1.0, None, "manual", why))
            continue

        targets: list[str] = []
        reasons: list[str] = []

        # --- 2) 体裁维度：规则级联 ---
        # 规则表里写的是短标签（MAD），落盘时换成带编码的类目名（A01 体裁-MAD），
        # 这样 B站 收藏夹按名称排序时同维度自然聚在一起。
        if form_dim:
            form_target, form_why = apply_cascade(item, rules.get("form") or {})
            if form_target:
                cat = tax_mod.resolve(taxonomy, form_dim.key, form_target)
                if cat:
                    targets.append(_target_name(cat, collections))
                    reasons.append(f"体裁：{form_why}")

        # --- 3) 主题维度：先看明确信号，再看画像打分 ---
        subject_target = None
        subject_from_rules = False
        if subject_dim:
            sub_target, sub_why = apply_cascade(item, rules.get("subject") or {})
            if sub_target:
                cat = tax_mod.resolve(taxonomy, subject_dim.key, sub_target)
                if cat:
                    subject_target = _target_name(cat, collections)
                    subject_from_rules = True
                    reasons.append(f"主题：{sub_why}")

        scored = rank_profiles(item, profiles, tag_idf, tok_idf, weights) if profiles else []
        # 规则已经给出明确的主题信号时，不再用画像打分覆盖它——规则更可靠
        if scored and subject_target is None:
            best = scored[0]
            second = scored[1]["score"] if len(scored) > 1 else 0.0
            gap = best["score"] - second
            best_name = best["profile"].name
            current_scores = [r["score"] for r in scored if r["profile"].collection_id in item.containers]
            cur_best = max(current_scores) if current_scores else 0.0

            if best["score"] >= high and (gap >= margin or best_name in current):
                if best_name in current:
                    subject_target = best_name
                    reasons.append(f"主题：已在「{best_name}」内（标签覆盖 {best['tag']:.0%}）")
                elif _allow_leave(current, collections, best["score"], cur_best, reorg_margin):
                    subject_target = best_name
                    reasons.append(
                        f"主题：标签覆盖 {best['tag']:.0%} · 标题 {best['title']:.0%}"
                        f" · UP主 {'命中' if best['creator'] else '未命中'}"
                    )
                # 分数够高但不足以从已整理好的收藏夹挪走 -> 维持现状
                elif current:
                    in_place.append(_verdict(item, current, current, best["score"], gap, "in_place",
                                             "当前收藏夹已足够匹配，无需变动"))
                    continue

            # 次分类：分数达到次级阈值且不在当前收藏夹里的，一并归入
            if subject_target:
                for r in scored[1:]:
                    if len(targets) >= 2:
                        break
                    if (r["score"] >= secondary_min and r["profile"].name not in current
                            and r["profile"].name != subject_target):
                        targets.append(r["profile"].name)

        if subject_target:
            targets.append(subject_target)

        # 去重保序
        targets = list(dict.fromkeys(t for t in targets if t))

        if targets:
            if all(t in current for t in targets):
                in_place.append(_verdict(item, current, targets, scored[0]["score"] if scored else None,
                                         None, "in_place", "已在目标收藏夹内"))
            else:
                assignments.append(_verdict(
                    item, current, targets,
                    scored[0]["score"] if scored else None,
                    (scored[0]["score"] - scored[1]["score"]) if len(scored) > 1 else None,
                    "rules" if subject_from_rules else "profile", "；".join(reasons),
                ))
            continue

        # --- 4) 待复核 ---
        pending.append({
            "item_id": item.id,
            "title": item.title,
            "creator_name": item.creator_name,
            "duration": item.duration,
            "tags": sorted(item.tags or []),
            "category_name": item.category_name,
            "description": (item.description or "")[:200],
            "current_collections": current,
            "link": item.link,
            "top_score": round(scored[0]["score"], 3) if scored else 0.0,
            "candidates": [
                {"collection": r["profile"].name, "score": round(r["score"], 3)}
                for r in scored[:5]
            ],
        })

    verdicts = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": {"high": high, "margin": margin, "reorg_margin": reorg_margin,
                       "secondary": secondary_min},
        "assignments": assignments,
        "in_place": in_place,
        "unavailable": unavailable,
        "pending_count": len(pending),
        "stats": _stats(assignments, in_place, unavailable, pending, items, collections),
    }
    log.ok(
        f"L1 归类 {len(assignments)} · 已在正确位置 {len(in_place)} · "
        f"失效 {len(unavailable)} · 待复核 {len(pending)}"
    )
    return verdicts, pending


def _dim(taxonomy: Taxonomy, key: str):
    d = taxonomy.dimension(key)
    return d if (d and d.enabled) else None


def _target_name(cat: Category, collections: dict[str, Collection]) -> str:
    """类目落到哪个收藏夹上。

    已有同名收藏夹就复用它原来的名字（不重命名用户的收藏夹），
    否则用带编码的类目名——那会新建一个收藏夹。
    """
    keys = {norm_name(cat.name), norm_name(cat.label)}
    for col in collections.values():
        if norm_name(col.name) in keys:
            return col.name
    return cat.name


def _category_exists(taxonomy: Taxonomy, dim_key: str, name: str) -> bool:
    return any(c.matches(name) for c in taxonomy.categories_of(dim_key))


def _allow_leave(current_names, collections: dict[str, Collection],
                 best_score: float, cur_best: float, reorg_margin: float) -> bool:
    """从「已整理好的收藏夹」里挪走条目，需要目标明显更优。"""
    by_name = {c.name: c for c in collections.values()}
    from_non_inbox = any(
        (col := by_name.get(n)) and not col.is_inbox for n in current_names
    )
    if not from_non_inbox:
        return True
    return (best_score - cur_best) >= reorg_margin


def _verdict(item, current, targets, score, gap, layer, reason) -> dict:
    return {
        "item_id": item.id,
        "title": item.title,
        "creator_name": item.creator_name,
        "duration": item.duration,
        "tags": item.tags or [],
        "category_name": item.category_name,
        "link": item.link,
        "targets": targets,
        "current_collections": current,
        "score": round(score, 3) if isinstance(score, (int, float)) else None,
        "gap": round(gap, 3) if isinstance(gap, (int, float)) else None,
        "layer": layer,
        "reason": reason,
    }


def _stats(assignments, in_place, unavailable, pending, items, collections) -> dict:
    by_target: Counter = Counter()
    for a in assignments:
        for t in a["targets"]:
            by_target[t] += 1
    return {
        "total_items": len(items),
        "total_collections": len(collections),
        "assigned": len(assignments),
        "in_place": len(in_place),
        "unavailable": len(unavailable),
        "pending": len(pending),
        "by_target": dict(by_target.most_common()),
    }


def print_summary(verdicts: dict) -> None:
    s = verdicts["stats"]
    log.info("")
    log.info(f"  条目总数 {s['total_items']} / 收藏夹 {s['total_collections']}")
    log.info(f"  L1 归类 {s['assigned']} · 已在正确位置 {s['in_place']} · "
             f"失效 {s['unavailable']} · 待复核 {s['pending']}")
    if s["by_target"]:
        log.info("  各目标分类（前 20）：")
        for name, n in list(s["by_target"].items())[:20]:
            log.info(f"    {n:>5}  {name}")
