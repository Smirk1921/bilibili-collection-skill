"""流水线产物的读写。平台无关。

文件布局与 steam-collection-skill 一致：stepN/ 存第 N 步的产出，
step2/ 的分类总表是唯一基准（增量模式靠它做差异，不另设状态文件）。
"""

from __future__ import annotations

from .model import Collection, Item, Taxonomy
from .paths import DATA, STEP1, STEP2, STEP3, STEP4, STEP5, ensure_dirs
from .safety import read_json, read_jsonl, write_json

# ---- step1：抓取结果 ----
COLLECTIONS_PATH = STEP1 / "collections.json"
ITEMS_PATH = STEP1 / "items.json"

# ---- step2：分类体系与预分类 ----
TAXONOMY_PATH = STEP2 / "taxonomy.json"
CATEGORIES_MD_PATH = STEP2 / "categories.md"
PRECLASSIFICATION_PATH = STEP2 / "preclassification.json"
PENDING_PATH = STEP2 / "pending_confirmation.json"

# ---- step3：复核 ----
REVIEW_LABELS_PATH = STEP3 / "review_labels.json"
VERDICTS_PATH = STEP3 / "verdicts.json"

# ---- step4：计划与执行 ----
PLAN_PATH = STEP4 / "plan.json"

# ---- step5：增量 ----
INCREMENTAL_PATH = STEP5 / "diff.json"


def _ensure() -> None:
    ensure_dirs()


# ----------------------------------------------------------------------
# step1
# ----------------------------------------------------------------------
def save_collections(cols: list[Collection]) -> None:
    _ensure()
    write_json(COLLECTIONS_PATH, [c.to_json() for c in cols])


def load_collections() -> dict[str, Collection]:
    raw = read_json(COLLECTIONS_PATH, []) or []
    return {c.id: c for c in (Collection.from_json(d) for d in raw)}


def save_items(items: list[Item]) -> None:
    _ensure()
    write_json(ITEMS_PATH, [it.to_json() for it in items])


def load_items() -> list[Item]:
    raw = read_json(ITEMS_PATH, []) or []
    return [Item.from_json(d) for d in raw]


# ----------------------------------------------------------------------
# step2
# ----------------------------------------------------------------------
def save_taxonomy(tax: Taxonomy) -> None:
    _ensure()
    write_json(TAXONOMY_PATH, tax.to_json())


def load_taxonomy() -> Taxonomy | None:
    raw = read_json(TAXONOMY_PATH, None)
    return Taxonomy.from_json(raw) if raw else None


def save_preclassification(rows: list[dict]) -> None:
    _ensure()
    write_json(PRECLASSIFICATION_PATH, rows)


def load_preclassification() -> list[dict]:
    return read_json(PRECLASSIFICATION_PATH, []) or []


def save_pending(rows: list[dict]) -> None:
    _ensure()
    write_json(PENDING_PATH, rows)


def load_pending() -> list[dict]:
    return read_json(PENDING_PATH, []) or []


# ----------------------------------------------------------------------
# step3
# ----------------------------------------------------------------------
def save_verdicts(verdicts: dict) -> None:
    _ensure()
    write_json(VERDICTS_PATH, verdicts)


def load_verdicts() -> dict:
    return read_json(VERDICTS_PATH, {}) or {}


def save_review_labels(labels: list[dict]) -> None:
    _ensure()
    write_json(REVIEW_LABELS_PATH, {"count": len(labels), "labels": labels})


def load_review_labels() -> list[dict]:
    """复核结果。兼容三种形态：{labels:[...]} / 裸数组 / 不存在。"""
    raw = read_json(REVIEW_LABELS_PATH, None)
    if raw is None:
        return []
    if isinstance(raw, dict):
        return raw.get("labels") or []
    return raw if isinstance(raw, list) else []


# ----------------------------------------------------------------------
# step4
# ----------------------------------------------------------------------
def save_plan(plan: dict) -> None:
    _ensure()
    write_json(PLAN_PATH, plan)


def load_plan() -> dict:
    return read_json(PLAN_PATH, {}) or {}


def load_rollback() -> list[dict]:
    return read_jsonl(STEP4 / "rollback.jsonl")


# ----------------------------------------------------------------------
# 通用
# ----------------------------------------------------------------------
def load_rules(path) -> dict:
    return read_json(path, {}) or {}
