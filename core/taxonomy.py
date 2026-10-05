"""分类体系构建：从规则表 + 已有收藏夹生成 taxonomy.json。平台无关。

设计原则（沿用实测有效的做法）：
- 类目名带编码前缀（`A01 体裁-MAD`），B站收藏夹按名称排序时同维度自然聚在一起
- 兜底类编号用该维度最大号（`A99`），保证排序永远在最后
- 已有收藏夹本身就是用户给的分类先验，纳入 subject 维度作为候选类目
"""

from __future__ import annotations

from collections import Counter

from .model import Category, Collection, Dimension, Item, Taxonomy, default_taxonomy
from .safety import log
from .text import norm_name

FALLBACK_LABEL = "其他"
MAX_NAME_LEN = 40   # 收藏夹名过长在客户端显示会截断，这里做个护栏


def collect_rule_targets(rules: dict, dim_key: str) -> list[str]:
    """按出现顺序收集某个维度规则里用到的全部 to 值。"""
    seen: list[str] = []
    tiers = rules.get(dim_key) or {}
    if not isinstance(tiers, dict):
        return seen
    for tier in ("strong", "native_category", "text", "native_category_weak"):
        for entry in tiers.get(tier) or []:
            target = (entry.get("to") or "").strip()
            if target and target not in seen:
                seen.append(target)
    return seen


def build(
    collections: dict[str, Collection],
    items: list[Item],
    rules: dict,
    include_existing: bool = True,
    min_existing_size: int = 3,
) -> Taxonomy:
    """生成分类体系。

    form 维度：来自规则表的 to 值（体裁是客观的，不依赖用户已有的收藏夹）。
    subject 维度：规则表的 to 值 + 用户已有的收藏夹（用户自己的分组就是先验）。
    """
    tax = default_taxonomy()

    # ---- form 维度 ----
    form_dim = tax.dimension("form")
    form_labels = collect_rule_targets(rules, "form")
    for i, label in enumerate(form_labels, 1):
        tax.categories.append(Category(
            code=f"{form_dim.prefix}{i:02d}",
            name=_display_name(form_dim.prefix, i, form_dim.name, label),
            dimension="form", label=label, description="由规则表判定",
        ))
    _add_fallback(tax, form_dim)

    # ---- subject 维度 ----
    subject_dim = tax.dimension("subject")
    subject_labels = list(collect_rule_targets(rules, "subject"))

    if include_existing:
        sizes = Counter()
        for it in items:
            if it.unavailable:
                continue
            for cid in it.containers:
                col = collections.get(cid)
                if col and not col.is_inbox:
                    sizes[col.name] += 1
        for name, n in sizes.most_common():
            if n >= min_existing_size and name not in subject_labels:
                subject_labels.append(name)
        dropped = [n for n, c in sizes.items() if c < min_existing_size]
        if dropped:
            log.info(f"  条目过少、暂不立类的已有收藏夹（<{min_existing_size}）：{dropped}")

    for i, label in enumerate(subject_labels, 1):
        tax.categories.append(Category(
            code=f"{subject_dim.prefix}{i:02d}",
            name=_display_name(subject_dim.prefix, i, subject_dim.name, label),
            dimension="subject", label=label,
            description="规则命中 / 已有收藏夹",
        ))
    _add_fallback(tax, subject_dim)

    return tax


def _display_name(prefix: str, index: int, dim_name: str, label: str) -> str:
    name = f"{prefix}{index:02d} {dim_name}-{label}"
    if len(name) > MAX_NAME_LEN:
        name = name[:MAX_NAME_LEN]
    return name


def _add_fallback(tax: Taxonomy, dim: Dimension) -> None:
    """兜底类编号取该维度最大号，保证任何排序下都在最后。"""
    used = [c.code for c in tax.categories_of(dim.key)]
    if not used:
        return
    tax.categories.append(Category(
        code=f"{dim.prefix}99",
        name=f"{dim.prefix}99 {dim.name}-{FALLBACK_LABEL}",
        dimension=dim.key, label=FALLBACK_LABEL,
        description="未命中任何类目时归入", is_fallback=True,
    ))


def resolve(taxonomy: Taxonomy, dim_key: str, label: str) -> Category | None:
    """把规则里的 label 解析成具体类目。找不到返回 None（不硬塞进兜底类）。"""
    for c in taxonomy.categories_of(dim_key):
        if not c.is_fallback and c.matches(label):
            return c
    return None


def fallback_of(taxonomy: Taxonomy, dim_key: str) -> Category | None:
    for c in taxonomy.categories_of(dim_key):
        if c.is_fallback:
            return c
    return None


def validate(taxonomy: Taxonomy) -> list[str]:
    """结构校验，返回问题列表。"""
    problems: list[str] = []
    codes: set[str] = set()

    if not taxonomy.enabled_dimensions():
        problems.append("没有任何启用的维度")

    for c in taxonomy.categories:
        if c.code in codes:
            problems.append(f"类目编码重复：{c.code}")
        codes.add(c.code)
        if not c.dimension:
            problems.append(f"{c.code} 未指定维度")
        elif taxonomy.dimension(c.dimension) is None:
            problems.append(f"{c.code} 指向不存在的维度 {c.dimension!r}")
        if not c.name.strip():
            problems.append(f"{c.code} 名称为空")
        if len(c.name) > MAX_NAME_LEN:
            problems.append(f"{c.code} 名称过长（{len(c.name)} > {MAX_NAME_LEN}）")

    # 每个维度必须且只能有一个兜底类，且编号是该维度最大号
    for dim in taxonomy.dimensions:
        cats = taxonomy.categories_of(dim.key)
        if not cats:
            continue
        fallbacks = [c for c in cats if c.is_fallback]
        if len(fallbacks) > 1:
            problems.append(f"维度 {dim.name} 有多个兜底类")
        elif fallbacks:
            nums = [_code_num(c.code) for c in cats if not c.is_fallback]
            if nums and _code_num(fallbacks[0].code) <= max(nums):
                problems.append(
                    f"维度 {dim.name} 的兜底类 {fallbacks[0].code} 编号不是最大号"
                )

    # 名称唯一性（归一化后）
    seen: dict[str, str] = {}
    for c in taxonomy.categories:
        key = norm_name(c.name)
        if key in seen:
            problems.append(f"类目名重复：{c.name} 与 {seen[key]}")
        seen[key] = c.name

    return problems


def _code_num(code: str) -> int:
    try:
        return int("".join(ch for ch in code if ch.isdigit()) or 0)
    except ValueError:
        return 0


def to_markdown(taxonomy: Taxonomy, stats: dict | None = None) -> str:
    lines = ["# 分类类目定义", ""]
    lines.append("> 本文件是分类体系的唯一源头，由 `build_taxonomy.py` 生成、经用户确认后冻结。")
    lines.append("")
    for dim in taxonomy.dimensions:
        cats = taxonomy.categories_of(dim.key)
        if not cats:
            continue
        state = "" if dim.enabled else "（未启用）"
        rule = "互斥，每个条目只能属于一个" if dim.exclusive else "可多选"
        lines.append(f"## {dim.name} {state} —— {rule}")
        lines.append("")
        for c in cats:
            extra = "  *（兜底）*" if c.is_fallback else ""
            lines.append(f"- `{c.code}` **{c.label}**{extra}")
        lines.append("")
    if stats:
        lines.append("## 预分类统计")
        lines.append("")
        lines.append("| 类目 | 条目数 |")
        lines.append("|---|---:|")
        for name, n in sorted(stats.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {name} | {n} |")
        lines.append("")
    return "\n".join(lines)
