"""统一数据模型：平台无关。

所有平台差异都收敛到 adapters/ 里，core/ 其余部分只认这里的字段。
这是「预留通用化」的核心——换平台时只需要写一个新适配器，
把该平台的数据映射成 Item / Collection 即可。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Collection:
    """一个容器（B站收藏夹 / Steam 收藏集 / 书签文件夹 …）。"""

    id: str
    name: str
    count: int = 0
    is_inbox: bool = False          # 收件箱：归类后会被清空（B站默认收藏夹）
    meta: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Collection":
        return Collection(
            id=str(d.get("id")),
            name=d.get("name") or "",
            count=int(d.get("count") or 0),
            is_inbox=bool(d.get("is_inbox")),
            meta=d.get("meta") or {},
        )


@dataclass
class Item:
    """一个条目（B站视频 / Steam 游戏 / 书签 / 文件 …）。"""

    id: str
    title: str
    description: str = ""
    duration: int | None = None
    creator_id: str | None = None
    creator_name: str = ""
    tags: list[str] = field(default_factory=list)
    category_id: str | None = None      # 平台原生分区 id
    category_name: str = ""             # 分区名（可能为空，取决于平台）
    link: str = ""
    containers: list[str] = field(default_factory=list)   # 所属容器 id
    added_at: int | None = None
    published_at: int | None = None
    unavailable: bool = False           # 已删除 / 私密 / 无法访问
    unavailable_reason: str = ""
    extra: dict = field(default_factory=dict)   # 平台特有字段（aid/cid/…）

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Item":
        known = {f for f in Item.__dataclass_fields__}
        kwargs = {k: v for k, v in d.items() if k in known}
        kwargs.setdefault("id", str(d.get("id") or ""))
        kwargs.setdefault("title", d.get("title") or "")
        return Item(**kwargs)

    def searchable(self) -> str:
        """用于规则匹配与相似度的合并文本。"""
        parts = [self.title or "", self.description or "", " ".join(self.tags or [])]
        if self.category_name:
            parts.append(self.category_name)
        return " ".join(parts)


# ----------------------------------------------------------------------
# 分类体系
# ----------------------------------------------------------------------
@dataclass
class Dimension:
    """一个分类维度。默认启用「体裁 + 主题」，其余可选。"""

    key: str
    name: str
    prefix: str                     # 类目编码前缀，如 A / B
    exclusive: bool = False         # 互斥维度：每个条目只能属于一个类目
    min_members: int = 1            # 立类所需最少条目数
    source: str = "ai"              # rules | profile+ai | ai | stats | user
    enabled: bool = True

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Dimension":
        return Dimension(
            key=d["key"],
            name=d.get("name") or d["key"],
            prefix=d.get("prefix") or "X",
            exclusive=bool(d.get("exclusive")),
            min_members=int(d.get("min_members") or 1),
            source=d.get("source") or "ai",
            enabled=bool(d.get("enabled", True)),
        )


@dataclass
class Category:
    """一个类目，对应一个目标收藏夹。"""

    code: str                       # 如 A01
    name: str                       # 如 "A01 体裁-MAD"（写入收藏夹的名字）
    dimension: str                  # 所属维度 key
    label: str = ""                 # 短标签，如 "MAD"；规则表用它来指向类目
    description: str = ""
    rules: dict = field(default_factory=dict)
    is_fallback: bool = False       # 兜底类（编号用该维度最大号）

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Category":
        return Category(
            code=d["code"],
            name=d.get("name") or d["code"],
            dimension=d.get("dimension") or "",
            label=d.get("label") or "",
            description=d.get("description") or "",
            rules=d.get("rules") or {},
            is_fallback=bool(d.get("is_fallback")),
        )

    def matches(self, text: str) -> bool:
        """规则里的 to 值能否指向本类目。"""
        from .text import norm_name

        t = norm_name(text)
        return bool(t) and t in {norm_name(self.label), norm_name(self.name), norm_name(self.code)}


@dataclass
class Taxonomy:
    """类目定义，唯一源头（step2/taxonomy.json）。"""

    version: int = 1
    dimensions: list[Dimension] = field(default_factory=list)
    categories: list[Category] = field(default_factory=list)

    def enabled_dimensions(self) -> list[Dimension]:
        return [d for d in self.dimensions if d.enabled]

    def dimension(self, key: str) -> Dimension | None:
        for d in self.dimensions:
            if d.key == key:
                return d
        return None

    def categories_of(self, dim_key: str) -> list[Category]:
        return [c for c in self.categories if c.dimension == dim_key]

    def by_name(self) -> dict[str, Category]:
        from .text import norm_name

        return {norm_name(c.name): c for c in self.categories}

    def to_json(self) -> dict:
        return {
            "version": self.version,
            "dimensions": [d.to_json() for d in self.dimensions],
            "categories": [c.to_json() for c in self.categories],
        }

    @staticmethod
    def from_json(d: dict) -> "Taxonomy":
        return Taxonomy(
            version=int(d.get("version") or 1),
            dimensions=[Dimension.from_json(x) for x in d.get("dimensions") or []],
            categories=[Category.from_json(x) for x in d.get("categories") or []],
        )


# 默认维度骨架：以「体裁 + 主题」为核心，其余默认关闭，用户可自行开启或增删
DEFAULT_DIMENSIONS = [
    Dimension(key="form", name="体裁", prefix="A", exclusive=False, min_members=1,
              source="rules", enabled=True),
    Dimension(key="subject", name="主题", prefix="B", exclusive=False, min_members=1,
              source="profile+ai", enabled=True),
    Dimension(key="series", name="系列", prefix="C", exclusive=False, min_members=2,
              source="ai", enabled=False),
    Dimension(key="creator", name="UP主", prefix="D", exclusive=False, min_members=3,
              source="stats", enabled=False),
    Dimension(key="status", name="状态", prefix="E", exclusive=True, min_members=1,
              source="user", enabled=False),
]


def default_taxonomy() -> Taxonomy:
    return Taxonomy(version=1, dimensions=[Dimension(**d.to_json()) for d in DEFAULT_DIMENSIONS], categories=[])
