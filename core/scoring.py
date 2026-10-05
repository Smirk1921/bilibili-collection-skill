"""打分引擎：收藏夹画像 + IDF 加权覆盖率。平台无关。

为什么不用余弦相似度：单个条目只有几个标签，跟整个收藏夹的标签集合做余弦
会被严重稀释（实测得分普遍落在 0.05~0.3，无法区分好坏）。改用 IDF 加权覆盖率——
越罕见的标签越有区分度，若条目的特征标签几乎都能在某个收藏夹里找到，
覆盖率就接近 1，天然可解释、可校准。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .model import Collection, Item
from .text import tokenize


@dataclass
class Profile:
    """一个已有收藏夹的画像，用作分类先验。"""

    collection_id: str
    name: str
    size: int = 0
    tag_counter: Counter = field(default_factory=Counter)
    tag_set: set[str] = field(default_factory=set)
    title_tokens: set[str] = field(default_factory=set)
    creator_counter: Counter = field(default_factory=Counter)
    category_counter: Counter = field(default_factory=Counter)
    sample_titles: list[str] = field(default_factory=list)

    def top_tags(self, n: int = 12) -> list[str]:
        return [t for t, _ in self.tag_counter.most_common(n)]

    def top_creators(self, n: int = 10) -> list[str]:
        return [c for c, _ in self.creator_counter.most_common(n)]

    def top_categories(self, n: int = 5) -> list[str]:
        return [c for c, _ in self.category_counter.most_common(n)]

    def to_json(self) -> dict:
        return {
            "collection_id": self.collection_id,
            "name": self.name,
            "size": self.size,
            "top_tags": self.top_tags(),
            "top_creators": self.top_creators(),
            "top_categories": self.top_categories(),
            "sample_titles": self.sample_titles[:8],
        }


def build_profiles(
    items: list[Item],
    collections: dict[str, Collection],
    skip_names: set[str] | None = None,
    treat_inbox_as_excluded: bool = True,
    min_size: int = 3,
    sample_limit: int = 8,
) -> dict[str, Profile]:
    """按「已有收藏夹」构建画像。

    收件箱（B站默认收藏夹）默认排除——它是待整理的堆积地，不是分类目标。
    """
    skip_names = skip_names or set()
    profiles: dict[str, Profile] = {}

    for item in items:
        if item.unavailable:
            continue
        for cid in item.containers:
            col = collections.get(cid)
            if col is None or col.name in skip_names:
                continue
            if treat_inbox_as_excluded and col.is_inbox:
                continue

            prof = profiles.get(cid)
            if prof is None:
                prof = Profile(collection_id=cid, name=col.name)
                profiles[cid] = prof

            prof.size += 1
            prof.tag_counter.update(item.tags or [])
            prof.tag_set.update(item.tags or [])
            prof.title_tokens.update(tokenize(item.title or ""))
            if item.creator_name:
                prof.creator_counter[item.creator_name] += 1
            if item.category_name:
                prof.category_counter[item.category_name] += 1
            if len(prof.sample_titles) < sample_limit:
                prof.sample_titles.append(item.title or "")

    return {cid: p for cid, p in profiles.items() if p.size >= min_size}


def build_idf(items: list[Item]) -> tuple[dict[str, float], dict[str, float]]:
    """按条目计算标签与标题词的 IDF。"""
    n = max(1, len(items))
    tag_df: Counter = Counter()
    tok_df: Counter = Counter()
    for item in items:
        for t in set(item.tags or []):
            tag_df[t] += 1
        for t in set(tokenize(item.title or "")):
            tok_df[t] += 1
    tag_idf = {t: math.log(n / (1 + df)) + 1.0 for t, df in tag_df.items()}
    tok_idf = {t: math.log(n / (1 + df)) + 1.0 for t, df in tok_df.items()}
    return tag_idf, tok_idf


def weighted_coverage(needles: set[str], haystack: set[str], idf: dict[str, float]) -> float:
    """needles 里有多少比例（按 IDF 加权）能在 haystack 中找到。"""
    if not needles:
        return 0.0
    total = sum(idf.get(t, 1.0) for t in needles)
    if total <= 0:
        return 0.0
    hit = sum(idf.get(t, 1.0) for t in needles if t in haystack)
    return hit / total


DEFAULT_WEIGHTS = {"tag": 1.00, "title": 0.35, "creator": 0.45, "category": 0.15}


def score_item(
    item: Item,
    profile: Profile,
    tag_idf: dict[str, float],
    tok_idf: dict[str, float],
    weights: dict[str, float] | None = None,
) -> dict:
    """条目与单个画像的匹配分，返回分项明细便于解释。"""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    w_sum = sum(w.values()) or 1.0

    tags = set(item.tags or [])
    toks = set(tokenize(item.title or ""))

    tag_s = weighted_coverage(tags, profile.tag_set, tag_idf)
    title_s = weighted_coverage(toks, profile.title_tokens, tok_idf)
    creator_s = 1.0 if item.creator_name and item.creator_name in profile.creator_counter else 0.0
    cat_s = 1.0 if item.category_name and item.category_name in profile.category_counter else 0.0

    score = (
        w["tag"] * tag_s + w["title"] * title_s
        + w["creator"] * creator_s + w["category"] * cat_s
    ) / w_sum

    return {
        "score": score,
        "tag": tag_s,
        "title": title_s,
        "creator": creator_s,
        "category": cat_s,
        "profile": profile,
    }


def rank_profiles(
    item: Item,
    profiles: dict[str, Profile],
    tag_idf: dict[str, float],
    tok_idf: dict[str, float],
    weights: dict[str, float] | None = None,
) -> list[dict]:
    """按分数从高到低返回全部画像的打分结果。"""
    scored = [score_item(item, p, tag_idf, tok_idf, weights) for p in profiles.values()]
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored
