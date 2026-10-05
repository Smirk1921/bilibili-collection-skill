# -*- coding: utf-8 -*-
"""步骤2: 生成分类体系 + L1 预分类 -> step2/

产出:
  step2/taxonomy.json            类目定义（唯一源头，经用户确认后冻结）
  step2/categories.md            人类可读的类目说明
  step2/preclassification.json   分类总表（机器可读，增量模式的基准）
  step2/preclassification.csv    分类总表（人类可复核）
  step2/pending_confirmation.json 待确认清单
  step3/verdicts.json            L1 判定明细
  out/分类清单.xlsx + 分类方案.md  人类可读报告

分类体系默认以「体裁 + 主题」两维为核心:
  体裁 由规则表判定（B站分区 + 简介关键词），客观、可复现
  主题 先看明确信号，再看已有收藏夹画像打分，判不准的进待确认清单

用法:
  python scripts/build_taxonomy.py                 # 生成并预分类
  python scripts/build_taxonomy.py --no-existing   # 不把已有收藏夹纳入候选类目
  python scripts/build_taxonomy.py --no-report     # 跳过 Excel/Markdown 报告
"""

from _common import (RULES_PATH, has_flag, load_cfg, load_rules,
                     require_step1, usage)

from core import classify as classify_mod
from core import report as report_mod
from core import store, taxonomy as tax_mod
from core.safety import log, write_csv

USAGE = __doc__


def main() -> int:
    if has_flag("--help", "-h"):
        usage(USAGE)

    cfg = load_cfg()
    rules = load_rules()
    collections, items = require_step1()

    log.step("步骤2 生成分类体系")
    tax = tax_mod.build(
        collections, items, rules,
        include_existing=not has_flag("--no-existing"),
    )
    problems = tax_mod.validate(tax)
    if problems:
        log.err("分类体系校验未通过：")
        for p in problems:
            log.info(f"  - {p}")
        return 1

    log.ok(f"共 {len(tax.categories)} 个类目，"
           f"{len(tax.enabled_dimensions())} 个启用维度")
    for dim in tax.enabled_dimensions():
        cats = tax.categories_of(dim.key)
        log.info(f"  {dim.name}（{len(cats)}）："
                 f"{'、'.join(c.label for c in cats if not c.is_fallback)}")

    log.step("L1 预分类…")
    verdicts, pending = classify_mod.classify(items, collections, tax, rules, cfg)
    classify_mod.print_summary(verdicts)

    if has_flag("--dry-run"):
        log.warn("DRY-RUN：未写入任何文件。")
        return 0

    # ---- 落盘 ----
    store.save_taxonomy(tax)
    store.CATEGORIES_MD_PATH.write_text(
        tax_mod.to_markdown(tax, verdicts["stats"].get("by_target")), encoding="utf-8"
    )
    store.save_verdicts(verdicts)
    store.save_pending(pending)

    rows = _to_rows(verdicts, pending, items, collections)
    store.save_preclassification(rows)
    _write_review_csv(rows)

    log.ok(f"已写入 {store.TAXONOMY_PATH}")
    log.ok(f"已写入 {store.CATEGORIES_MD_PATH}")
    log.ok(f"已写入 {store.PRECLASSIFICATION_PATH}")
    log.ok(f"已写入 {store.PENDING_PATH}")

    if not has_flag("--no-report"):
        report_mod.report(verdicts, {"summary": {}}, None, items, collections)

    log.info("")
    log.info(f"**汇报**：{len(tax.categories)} 个类目、"
             f"已归类 {verdicts['stats']['assigned']} 条、"
             f"待确认 {len(pending)} 条。**等待确认**。")
    log.info("确认类目后进入步骤3： python scripts/review_tools.py prepare")
    return 0


def _to_rows(verdicts: dict, pending: list, items: list, collections: dict) -> list[dict]:
    """把判定结果摊平成一张表，作为增量模式的唯一基准。"""
    by_id = {it.id: it for it in items}
    out: dict[str, dict] = {}

    def put(item_id, targets, layer, score, reason):
        it = by_id.get(item_id)
        if it is None:
            return
        out[item_id] = {
            "item_id": item_id,
            "title": it.title,
            "creator_name": it.creator_name,
            "duration": it.duration,
            "category_name": it.category_name,
            "tags": it.tags or [],
            "current_collections": [collections[c].name for c in it.containers if c in collections],
            "targets": targets,
            "layer": layer,
            "score": score,
            "reason": reason,
            "link": it.link,
        }

    for a in verdicts.get("assignments") or []:
        put(a["item_id"], a.get("targets") or [], a.get("layer"), a.get("score"), a.get("reason"))
    for a in verdicts.get("in_place") or []:
        put(a["item_id"], a.get("targets") or [], "in_place", a.get("score"), a.get("reason"))
    # 失效条目也要进总表（targets 为空），否则增量模式每次都把它们当成"新增"
    for a in verdicts.get("unavailable") or []:
        put(a["item_id"], [], "unavailable", None, a.get("reason"))
    # 待确认条目同样要进总表，否则也会被当成新增
    for p in pending:
        put(p["item_id"], [], "pending", p.get("top_score"), "待复核")
    return list(out.values())


def _write_review_csv(rows: list[dict]) -> None:
    columns = ["item_id", "title", "creator_name", "category_name", "targets",
               "current_collections", "layer", "score", "reason", "link"]
    flat = []
    for r in rows:
        flat.append({
            **r,
            "tags": ", ".join(r.get("tags") or []),
            "targets": " + ".join(r.get("targets") or []),
            "current_collections": " / ".join(r.get("current_collections") or []),
        })
    write_csv(store.STEP2 / "preclassification.csv", flat, columns)
    log.ok(f"已写入 {store.STEP2 / 'preclassification.csv'}")


if __name__ == "__main__":
    raise SystemExit(main())
