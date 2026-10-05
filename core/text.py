"""文本处理：分词、词频、相似度。平台无关，无第三方依赖。

中文不引 jieba，用字符二元组近似——对「标题/标签相似度」这类用途足够，
且避免给技能包增加安装负担。
"""

from __future__ import annotations

import math
import re
from collections import Counter

_ASCII_WORD = re.compile(r"[a-z0-9][a-z0-9+#._-]{1,}")
_CJK = re.compile(r"[\u4e00-\u9fff]+")

# 标题里高频但无区分度的词，参与相似度只会引入噪声
STOPWORDS = {
    "视频", "合集", "第", "期", "集", "上", "下", "中", "的", "了", "是", "在",
    "和", "与", "及", "我", "你", "他", "这", "那", "一个", "什么", "怎么",
    "为什么", "如何", "教程", "官方", "高清", "完整版", "中文", "字幕", "1080p",
    "4k", "full", "the", "and", "for", "with", "you", "how", "what", "ep",
    "part", "vol", "official", "video", "mv",
}


def tokenize(text: str) -> list[str]:
    """英文/数字按词切，中文用字符二元组近似。"""
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []

    for word in _ASCII_WORD.findall(text):
        if word not in STOPWORDS and not word.isdigit():
            tokens.append(word)

    for run in _CJK.findall(text):
        if len(run) == 1:
            if run not in STOPWORDS:
                tokens.append(run)
            continue
        for i in range(len(run) - 1):
            bigram = run[i : i + 2]
            if bigram not in STOPWORDS:
                tokens.append(bigram)

    return tokens


def tf_vector(tokens: list[str]) -> dict[str, float]:
    return dict(Counter(tokens))


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """稀疏向量余弦相似度，范围 [0, 1]。"""
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    if dot <= 0:
        return 0.0
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def fmt_duration(seconds) -> str:
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def norm_name(name: str) -> str:
    """分类名归一化，用于匹配已有收藏夹（忽略空格/全角空格/大小写）。"""
    if not name:
        return ""
    return name.strip().replace(" ", "").replace("　", "").lower()
