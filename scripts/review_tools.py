# -*- coding: utf-8 -*-
"""步骤3 工具: 复核批次的切分 / 汇总 / 回写。

AI 复核协议见 references/methods-review.md。核心是：把待确认条目切成小批，
交给子代理逐条判定，再把结果汇总回 step3/review_labels.json。

用法:
  python scripts/review_tools.py prepare [--size 30]
      -> step3/batches/batch_NNN.txt + manifest.json
  python scripts/review_tools.py merge
      -> step3/review_labels.json + step3/change_report.txt
         （自动修复子代理常见的格式问题：竖线/逗号混用、漏列、加粗标记）
  python scripts/review_tools.py apply [--dry-run]
      -> 把复核结果合并进 step2/preclassification.json（先备份）

结果文件格式（子代理写 step3/results/batch_NNN.txt，每行一条）:
  <item_id> | <目标分类，多个用 + 连接> | <置信度 0-1> | <判断依据>
"""

from __future__ import annotations

import re

from _common import arg_val, has_flag, load_cfg, require_step1, usage

from core import store
from core.safety import backup, log, read_csv, write_csv

USAGE = __doc__
BATCH_DIR = store.STEP3 / "batches"
RESULT_DIR = store.STEP3 / "results"
MANIFEST = BATCH_DIR / "manifest.json"
UNDECIDED = "待定"


# ----------------------------------------------------------------------
def cmd_prepare() -> int:
    size = max(5, int(arg_val("--size", 30)))
    collections, items = require_step1()
    by_id = {it.id: it for it in items}

    pending = store.load_pending()
    if not pending:
        log.warn("没有待确认条目。可以跳过步骤3，直接进入步骤4： python scripts/build_plan.py")
        return 0

    # 已有目标分类的也一并复核（用户点名的、上次待定的优先放前面）
    review_targets = [p for p in pending]
    for r in store.load_preclassification():
        if r.get("layer") in ("profile", "rules") and r["item_id"] not in {p["item_id"] for p in review_targets}:
            review_targets.append(r)

    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR).mkdir(parents=True, exist_ok=True)

    taxonomy = store.load_taxonomy()
    cat_names: list[str] = []
    if taxonomy:
        cat_names = [c.name for c in taxonomy.categories if not c.is_fallback]

    manifest, n = [], 0
    for start in range(0, len(review_targets), size):
        n += 1
        chunk = review_targets[start : start + size]
        path = BATCH_DIR / f"batch_{n:03d}.txt"
        path.write_text(_render_batch(n, chunk, by_id, cat_names), encoding="utf-8")
        manifest.append({"batch": n, "file": str(path.name), "count": len(chunk),
                         "item_ids": [c["item_id"] for c in chunk]})

    store.write_json(MANIFEST, {"batch_size": size, "batches": manifest,
                                "total": len(review_targets)})
    log.ok(f"已切分 {n} 个批次，共 {len(review_targets)} 条 -> {BATCH_DIR}")
    log.info(f"  候选类目：{'、'.join(cat_names) if cat_names else '（未找到类目定义）'}")
    log.info("")
    log.info("**汇报**：批次数量与大小、候选类目。**等待确认**后开始逐批复核。")
    return 0


def _render_batch(n: int, chunk: list[dict], by_id: dict, cat_names: list[str]) -> str:
    lines = [
        f"# 复核批次 {n:03d}  共 {len(chunk)} 条",
        "# 请为每条给出目标分类（可多个，用 + 连接），判不准写「待定」。",
        "# 结果写入 step3/results/batch_%03d.txt，每行一条：" % n,
        "#   <item_id> | <目标分类> | <置信度> | <判断依据>",
        "",
        "## 候选类目",
        "  " + ("、".join(cat_names) if cat_names else "（未找到类目定义）"),
        "",
    ]
    for i, row in enumerate(chunk, 1):
        it = by_id.get(row["item_id"])
        lines.append(f"[{i}] item_id={row['item_id']}")
        lines.append(f"    标题: {row.get('title') or (it.title if it else '')}")
        if it:
            lines.append(f"    作者: {it.creator_name}")
            lines.append(f"    原生分类: {it.category_name or '（无）'}")
            lines.append(f"    标签: {', '.join(it.tags or []) or '（无）'}")
            if it.description:
                lines.append(f"    简介: {it.description[:180]}")
        cur = row.get("current_collections") or []
        if cur:
            lines.append(f"    当前收藏夹: {' / '.join(cur)}")
        if row.get("targets"):
            lines.append(f"    L1 判定: {' + '.join(row['targets'])}"
                         f"（{row.get('layer')}，{row.get('reason') or ''}）")
        if row.get("top_score") is not None:
            lines.append(f"    L1 得分: {row['top_score']}")
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------
def cmd_merge() -> int:
    if not RESULT_DIR.exists():
        raise SystemExit(f"未找到 {RESULT_DIR}，请先让子代理产出结果文件")

    results = sorted(RESULT_DIR.glob("*.txt")) + sorted(RESULT_DIR.glob("*.csv"))
    if not results:
        raise SystemExit(f"{RESULT_DIR} 里没有结果文件")

    labels: list[dict] = []
    problems: list[str] = []
    for path in results:
        for lineno, row in enumerate(_norm_rows(path.read_text(encoding="utf-8")), 1):
            if row.get("error"):
                problems.append(f"{path.name}:{lineno} {row['error']}")
                continue
            labels.append(row)

    if not labels:
        raise SystemExit("没有解析出任何有效结果，请检查格式")

    # 防误覆盖：结果明显少于已有标签时停下确认。
    # 实测踩过——拿一个测试批次跑 merge，把 142 条人工复核结果冲掉了。
    existing = store.load_review_labels()
    if len(existing) > len(labels) * 2 and len(existing) - len(labels) > 10 \
            and not has_flag("--force"):
        log.err(f"已有 {len(existing)} 条复核标签，本次只解析出 {len(labels)} 条。")
        log.info(f"  如果确实要用新结果覆盖，加 --force；")
        log.info(f"  如果只是想补测一批，先把结果文件挪出 {RESULT_DIR.name}/。")
        return 1

    before = {r["item_id"]: r for r in store.load_preclassification()}
    changed = []
    for lab in labels:
        old = before.get(lab["item_id"])
        old_targets = (old or {}).get("targets") or []
        if set(old_targets) != set(lab["targets"]):
            changed.append({"item_id": lab["item_id"],
                            "title": (old or {}).get("title", ""),
                            "before": old_targets, "after": lab["targets"],
                            "reason": lab.get("reason", "")})

    store.save_review_labels(labels)
    log.ok(f"已写入 {store.REVIEW_LABELS_PATH}（{len(labels)} 条）")

    lines = [f"# 复核变更报告", "",
             f"- 复核条数：{len(labels)}",
             f"- 与 L1 结果不同：{len(changed)}",
             f"- 解析失败：{len(problems)}", ""]
    if changed:
        lines += ["## 变更明细", "", "| 标题 | 原判定 | 复核后 | 依据 |", "|---|---|---|---|"]
        for c in changed[:300]:
            lines.append(f"| {c['title'][:40]} | {' + '.join(c['before']) or '（无）'} "
                         f"| {' + '.join(c['after'])} | {c['reason'][:40]} |")
        lines.append("")
    if problems:
        lines += ["## 解析失败的行", ""] + [f"- {p}" for p in problems[:50]] + [""]

    report_path = store.STEP3 / "change_report.txt"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    log.ok(f"已写入 {report_path}")

    if problems:
        log.warn(f"{len(problems)} 行解析失败（已记入报告，可手动修正后重跑 merge）")
    log.info("")
    log.info(f"**汇报**：复核覆盖 {len(labels)} 条、修正 {len(changed)} 条。**等待确认**。")
    return 0


def _norm_rows(text: str) -> list[dict]:
    """解析子代理结果，尽量修复常见格式问题而不是直接丢弃。"""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # 去掉 markdown 加粗与行首序号
        line = re.sub(r"\*\*", "", line)
        line = re.sub(r"^\s*\d+[.、)]\s*", "", line)

        parts = [p.strip() for p in re.split(r"[|｜]", line)]
        if len(parts) < 2:
            parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            out.append({"error": f"无法解析：{line[:60]}"})
            continue

        item_id = parts[0].strip()
        if not re.match(r"^[A-Za-z0-9]+$", item_id):
            out.append({"error": f"item_id 不合法：{item_id[:30]}"})
            continue

        targets = [t.strip() for t in re.split(r"[+＋,，/、]", parts[1]) if t.strip()]
        targets = [t for t in targets if t != UNDECIDED]

        conf = 0.0
        if len(parts) > 2:
            m = re.search(r"(\d+(?:\.\d+)?)", parts[2])
            if m:
                conf = float(m.group(1))
                if conf > 1:
                    conf = conf / 100.0

        out.append({
            "item_id": item_id,
            "targets": targets,
            "confidence": conf or (0.9 if targets else 0.0),
            "reason": (parts[3] if len(parts) > 3 else "").strip(),
        })
    return out


# ----------------------------------------------------------------------
def cmd_apply() -> int:
    labels = store.load_review_labels()
    if not labels:
        raise SystemExit("没有复核结果，请先运行 merge")

    by_id = {r["item_id"]: dict(r) for r in store.load_preclassification()}
    changed = 0
    for lab in labels:
        row = by_id.get(lab["item_id"])
        if row is None:
            continue
        if set(row.get("targets") or []) != set(lab["targets"]):
            changed += 1
        row["targets"] = lab["targets"]
        row["layer"] = "review"
        row["score"] = lab.get("confidence")
        row["reason"] = lab.get("reason") or row.get("reason")

    log.step(f"将回写 {len(by_id)} 条，其中 {changed} 条与 L1 不同")
    if has_flag("--dry-run") or not has_flag("--confirm"):
        log.warn("当前为 DRY-RUN，未回写。确认后加 --confirm 重新执行。")
        return 0

    bak = backup(store.PRECLASSIFICATION_PATH)
    if bak:
        log.info(f"已备份 -> {bak.name}")
    store.save_preclassification(list(by_id.values()))
    log.ok(f"已回写 {store.PRECLASSIFICATION_PATH}")
    log.info("")
    log.info("**汇报**：回写条数。**等待确认**后进入步骤4： python scripts/build_plan.py")
    return 0


# ----------------------------------------------------------------------
def main() -> int:
    if has_flag("--help", "-h") or not _cmd():
        usage(USAGE)
    return {"prepare": cmd_prepare, "merge": cmd_merge, "apply": cmd_apply}[_cmd()]()


def _cmd() -> str:
    from _common import positional

    pos = positional()
    return pos[0] if pos and pos[0] in ("prepare", "merge", "apply") else ""


if __name__ == "__main__":
    raise SystemExit(main())
