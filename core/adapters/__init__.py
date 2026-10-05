"""适配器协议：core/ 与具体平台之间的唯一接口。

换平台时只需要实现这里的 8 个方法，把该平台的数据映射成 Item / Collection，
core/ 的其余部分（打分、计划、安全机制、报告）完全不用改。
详见 references/porting-guide.md。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..model import Collection, Item


@runtime_checkable
class Adapter(Protocol):
    """平台适配器。"""

    name: str

    # ---- 认证 ----
    def authenticate(self) -> None:
        """校验/建立登录态。失败时抛异常，异常信息要能指导用户下一步怎么做。"""
        ...

    # ---- 读 ----
    def list_collections(self) -> list[Collection]:
        """列出全部容器。收件箱（待整理的堆积地）要标 is_inbox=True。"""
        ...

    def list_item_ids(self, collection_id: str) -> list[str]:
        """列出某个容器内的条目 id。"""
        ...

    def fetch_items(self, item_ids: list[str]) -> list[Item]:
        """批量取条目的基础信息（标题/作者/时长/所属容器）。

        `containers` 必须填对——分类引擎靠它判断条目当前在哪些收藏夹里。
        """
        ...

    def enrich(self, item: Item) -> Item:
        """补充用于分类的信号：标签、原生分类、简介。

        这是唯一会逐条发起网络请求的方法，实现方必须自己做限速与重试。
        """
        ...

    # ---- 写 ----
    def create_collection(self, name: str, **kwargs) -> str:
        """新建容器，返回其 id。"""
        ...

    def assign(self, item_id: str, add_ids: list[str], remove_ids: list[str],
               extra: dict | None = None) -> None:
        """把条目加入 add_ids、从 remove_ids 移出。

        必须是「追加收藏」语义——不得删除条目本身。
        """
        ...

    # ---- 可选 ----
    def resolve_category(self, raw_id) -> str:
        """平台原生分类 id -> 名称。拿不到就返回空串，不要抛异常。"""
        return ""
