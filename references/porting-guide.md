# 接入新平台：通用化说明

本技能包分两层：

```
core/                 平台无关：数据模型、打分算法、分类流水线、安全机制
core/adapters/        平台相关：唯一知道具体平台细节的地方
```

**换平台只需要写一个新适配器**，`core/` 其余部分完全不用改。

## Adapter 协议

实现 `core/adapters/__init__.py: Adapter` 里的 8 个方法即可：

```python
class Adapter(Protocol):
    name: str

    def authenticate(self) -> None: ...
    def list_collections(self) -> list[Collection]: ...
    def list_item_ids(self, collection_id: str) -> list[str]: ...
    def fetch_items(self, item_ids: list[str]) -> list[Item]: ...
    def enrich(self, item: Item) -> Item: ...
    def create_collection(self, name: str, **kwargs) -> str: ...
    def assign(self, item_id, add_ids, remove_ids, extra=None) -> None: ...
    def resolve_category(self, raw_id) -> str: ...   # 可选
```

约定：

- `authenticate()` 失败时抛异常，异常信息要能指导用户下一步做什么
- `fetch_items()` 必须填对 `Item.containers`——分类引擎靠它判断条目当前在哪些收藏夹
- `enrich()` 是唯一逐条发请求的方法，**实现方自己负责限速与重试**
- `assign()` 必须是**追加**语义，不得删除条目本身
- `resolve_category()` 拿不到就返回空串，不要抛异常

## 统一数据模型

| 概念 | B站 | Steam 收藏集 | 浏览器书签 | 本地文件 |
|---|---|---|---|---|
| `Collection` | 收藏夹 | Collection | 文件夹 | 目录 |
| `Collection.is_inbox` | 默认收藏夹 | — | 未整理 | 下载目录 |
| `Item` | 视频 | 游戏 | 书签 | 文件 |
| `Item.creator_name` | UP主 | 开发商 | — | — |
| `Item.category_name` | 分区 | Steam 标签 | — | 扩展名 |
| `Item.containers` | 所属收藏夹 id | 所属 Collection | 所属文件夹 | 所属目录 |
| `Item.tags` | 视频标签 | store_tags | 书签标签 | — |

## 已可直接复用的部分

- `core/text.py` —— 分词、余弦、时长格式化（100% 通用）
- `core/safety.py` —— 日志、原子写、时间戳备份、限速器、dry-run 守卫
- `core/scoring.py` —— 画像构建、IDF 加权覆盖率、打分排序
- `core/rules.py` —— 规则级联引擎（规则表是数据，改规则不用改代码）
- `core/taxonomy.py` —— 分类体系构建与校验
- `core/classify.py` / `plan.py` —— 分类与计划（只认统一模型）
- `core/apply.py` —— 写入与回滚（只调协议方法）
- `core/report.py` —— Excel/Markdown 渲染（只需换列名）
- `core/store.py` / `paths.py` / `config.py` —— 产物读写与配置

## 需要新写的部分

1. **适配器**（实现上面 8 个方法）
2. **规则表** —— 该平台的"体裁"判定规则。B站 的见
   `core/adapters/bilibili_genre_rules.json`，是纯数据，可参照格式另写一份
3. **`scripts/_common.py` 里的 `make_adapter()`** —— 改一行指向新适配器

## 各平台要点提示

- **Steam**：`IPlayerService/GetOwnedGames` 拿库存；收藏集是本地云存储文件；
  store_tags 可作主题信号。参考 `Smirk1921/steam-collection-skill`
- **浏览器书签**：Chrome 的 `Bookmarks` JSON 或 HTML 导出；
  文件夹即 Collection；无登录；域名与标题可作分类信号
- **YouTube 播放列表**：Data API v3；播放列表即 Collection；
  `snippet.categoryId` 即原生分类；写入用 `playlistItems.insert/delete`
- **本地文件**：目录即 Collection；`os.rename` 即 assign，
  但**写入适配器必须做成事务性的**（先复制再删，或至少先备份）

## 通用化时不要做的事

- 不要给协议加投机性的方法——只加已经被真实需求验证过的
- 不要把平台字段名泄漏进 `core/`（`bvid` / `media_id` 这类只能出现在适配器里）
- 不要把 B站 的规则表内容搬进 `core/rules.py`——规则是数据，按平台分开存放
