"""规则级联引擎：平台无关。

判定顺序（可靠性从高到低），命中即返回：
  1. strong          条目自己明确声明的形式（标题/简介里写了"手书""攻略""二创"）
  2. native_category 平台原生分类（B站分区 / Steam 标签 / YouTube 分类）
  3. text            标题/简介/标签里的关键词兜底

规则表本身是数据（JSON），改规则不用改代码——这是从「把体裁判定写死在
genre.py 里」的教训中改过来的。
"""

from __future__ import annotations

import re

from .model import Item


def match_rule(rule: dict, item: Item) -> bool:
    """判断单条 match 条件是否命中。空条件视为不命中。"""
    if not rule:
        return False
    hit = False

    if need := rule.get("tag_any"):
        if not (set(need) & set(item.tags or [])):
            return False
        hit = True

    if need_all := rule.get("tag_all"):
        if not set(need_all).issubset(set(item.tags or [])):
            return False
        hit = True

    if names := rule.get("creator_any"):
        if (item.creator_name or "") not in set(names):
            return False
        hit = True

    if (part := rule.get("native_category_any")) and item.category_name:
        if not any(k and k in item.category_name for k in part):
            return False
        hit = True

    if (rx := rule.get("text_regex")):
        try:
            if not re.search(rx, item.searchable(), re.IGNORECASE):
                return False
        except re.error:
            return False
        hit = True

    if (kw := rule.get("text_any")):
        try:
            if not re.search("|".join(re.escape(k) for k in kw), item.searchable(), re.IGNORECASE):
                return False
        except re.error:
            return False
        hit = True

    if (lo := rule.get("duration_gt")) is not None:
        if (item.duration or 0) <= int(lo):
            return False
        hit = True

    if (lt := rule.get("duration_lt")) is not None:
        if (item.duration or 0) >= int(lt):
            return False
        hit = True

    return hit


def _tier_hit(tier: list[dict], item: Item) -> tuple[str, str] | None:
    for entry in tier or []:
        target = (entry.get("to") or "").strip()
        if target and match_rule(entry.get("match") or {}, item):
            return target, entry.get("why") or entry.get("note") or ""
    return None


TIER_ORDER = ("strong", "native_category", "text", "native_category_weak")

TIER_LABEL = {
    "strong": "明确声明",
    "native_category": "原生分类",
    "text": "文本线索",
    "native_category_weak": "原生分类（弱）",
}


def apply_cascade(item: Item, rules: dict) -> tuple[str, str]:
    """按层级顺序判定，返回 (类目名, 依据)。类目名为空表示无法判定。

    层级说明：
      strong               条目自己明确声明的形式（"手书""攻略""二创"）
      native_category      平台原生分类，强信号（B站分区：动漫剪辑→MAD）
      text                 标题/简介/标签关键词兜底
      native_category_weak 平台原生分类，弱信号（游戏区只说明"是游戏"，
                           体裁还得看文本，所以排在这一层，被 text 覆盖）
    """
    for tier_name in TIER_ORDER:
        hit = _tier_hit(rules.get(tier_name) or [], item)
        if hit:
            target, why = hit
            label = TIER_LABEL[tier_name]
            if tier_name.startswith("native_category"):
                label = f"{label}「{item.category_name or '未知'}」"
            return target, f"{label}{('：' + why) if why else ''}"
    return "", ""


def apply_manual_rules(item: Item, rules: list[dict]) -> tuple[str, str] | None:
    """用户手写的硬规则，优先级最高，命中即直接归类。"""
    for rule in rules or []:
        target = (rule.get("to") or "").strip()
        if target and match_rule(rule.get("match") or {}, item):
            return target, f"命中手写规则 {rule.get('match')}"
    return None


def validate_rules(rules: dict) -> list[str]:
    """校验规则表结构，返回问题列表（空列表表示没问题）。

    支持两种形态：扁平的 {tier: [...]}，或按维度分组的 {form: {...}, subject: {...}}。
    """
    problems: list[str] = []
    if not isinstance(rules, dict):
        return ["规则表必须是对象"]

    if any(k in rules for k in TIER_ORDER):
        _validate_tiers(rules, "", problems)
    else:
        for dim, tiers in rules.items():
            if dim.startswith("_"):
                continue
            if not isinstance(tiers, dict):
                problems.append(f"维度 {dim!r} 必须是对象")
                continue
            _validate_tiers(tiers, f"{dim}.", problems)
    return problems


def _validate_tiers(tiers: dict, prefix: str, problems: list[str]) -> None:
    for tier, entries in tiers.items():
        if tier.startswith("_"):
            continue
        if tier not in TIER_ORDER:
            problems.append(f"未知层级 {prefix}{tier!r}（可用：{sorted(TIER_ORDER)}）")
            continue
        if not isinstance(entries, list):
            problems.append(f"层级 {prefix}{tier!r} 必须是数组")
            continue
        for i, entry in enumerate(entries):
            where = f"{prefix}{tier}[{i}]"
            if not isinstance(entry, dict):
                problems.append(f"{where} 必须是对象")
                continue
            if not (entry.get("to") or "").strip():
                problems.append(f"{where} 缺少 to")
            match = entry.get("match") or {}
            if not match:
                problems.append(f"{where} 缺少 match")
            if (rx := match.get("text_regex")):
                try:
                    re.compile(rx)
                except re.error as exc:
                    problems.append(f"{where} text_regex 无法编译：{exc}")
            known = {"tag_any", "tag_all", "creator_any", "native_category_any",
                     "text_regex", "text_any", "duration_gt", "duration_lt"}
            for key in match:
                if key not in known:
                    problems.append(f"{where} 未知匹配条件 {key!r}")


# 纯拉丁关键词不加词边界会误匹配到更长的英文单词里。
# 实测踩过两次：`drama` 命中光影包名 `Dramatic+Skys`；`MAD` 命中普通英文词。
_LATIN_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,}$")
_BOUNDED = re.compile(r"^\(\?<[=!]|\\b")


def lint_rules(rules: dict) -> list[str]:
    """检查规则表里容易误匹配的写法，返回提醒列表（不阻断执行）。"""
    warnings: list[str] = []

    def check(pattern: str, where: str) -> None:
        for part in pattern.split("|"):
            if not _LATIN_TOKEN.match(part):
                continue
            if any(c in "[]()\\" for c in part):
                continue
            warnings.append(
                f"{where}: 纯拉丁关键词 {part!r} 没有词边界，"
                f"会误匹配到更长的英文单词里（如 Dramatic 里的 drama）。"
                f"建议改成 (?<![A-Za-z0-9]){part}(?![A-Za-z0-9])"
            )

    def walk(tiers: dict, prefix: str) -> None:
        for tier, entries in (tiers or {}).items():
            if tier.startswith("_") or not isinstance(entries, list):
                continue
            for i, e in enumerate(entries):
                m = (e or {}).get("match") or {}
                if (rx := m.get("text_regex")):
                    check(rx, f"{prefix}{tier}[{i}]")

    if any(k in rules for k in TIER_ORDER):
        walk(rules, "")
    else:
        for dim, tiers in rules.items():
            if not dim.startswith("_") and isinstance(tiers, dict):
                walk(tiers, f"{dim}.")
    return warnings
