"""B站适配器：把 B站 收藏夹映射成统一的 Collection / Item。

这是整个技能包唯一知道 B站 细节的地方。它实现了 adapters.Adapter 协议，
core/ 的其余部分只认协议，不认 B站。

实测要点（踩过的坑，别改回去）：
- 收藏夹接口未登录时返回 code=0 且 data=null（不报错），必须显式校验 data
- 写接口成功后返回 data=null，不能当成登录失效
- 视频详情接口的 tname/tname_v2 恒为空字符串，分区名要靠 partitions 反查
- 分区是 UP主 自选的，偶尔会选错（实测有 AI 动画 MV 被投到「工程机械」）
"""

from __future__ import annotations

import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from ..model import Collection, Item
from ..paths import BROWSER_PROFILE, DATA, SCRIPTS
from ..safety import RateLimiter, log, read_json, write_json

API = "https://api.bilibili.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
COOKIE_PATH = SCRIPTS / "bilibili_cookies.json"
PARTITION_MAP_PATH = DATA / "partition_map.json"

REQUIRED_COOKIES = ("SESSDATA", "bili_jct")
# 需要退避重试的业务码：风控拦截 / 超出限制 / 请求过于频繁
RETRYABLE_CODES = {-412, -509, -799}
AUTH_CODES = {-101, -111}
# 稿件已失效：重试没有意义
PERMANENT_ERROR_CODES = {62002, -404, -403, -110}


class BiliError(RuntimeError):
    def __init__(self, code: int, message: str, url: str = "") -> None:
        super().__init__(f"code={code} message={message} url={url}")
        self.code = code
        self.message = message


class AuthError(BiliError):
    pass


# ======================================================================
# HTTP 客户端：限速 + 重试 + 风控识别
# ======================================================================
class BiliClient:
    def __init__(self, cfg: dict, need_login: bool = True) -> None:
        rl = cfg.get("rate_limit", {})
        self.max_retries = int(rl.get("max_retries", 4))
        self.backoff_base = float(rl.get("backoff_base", 1.8))
        self._read = RateLimiter(float(rl.get("read_min_interval", 0.35)))
        self._write = RateLimiter(float(rl.get("write_min_interval", 2.0)))

        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._lock = threading.Lock()
        self.mid: int | None = None
        self.uname = ""

        cookies = self._load_cookies(cfg)
        for name in REQUIRED_COOKIES:
            if cookies.get(name):
                self.session.cookies.set(name, cookies[name], domain=".bilibili.com")

        if need_login:
            missing = [n for n in REQUIRED_COOKIES if not cookies.get(n)]
            if missing:
                raise AuthError(-101, f"缺少登录凭据 {missing}，请先运行: python scripts/fetch_library.py --login")

    @staticmethod
    def _load_cookies(cfg: dict) -> dict:
        """Cookie 来源优先级：环境变量 > 配置文件 > 浏览器登录缓存。"""
        bili = cfg.get("bilibili", {})
        out = {}
        if bili.get("sessdata"):
            out["SESSDATA"] = bili["sessdata"]
        if bili.get("bili_jct"):
            out["bili_jct"] = bili["bili_jct"]
        if bili.get("dedeuserid"):
            out["DedeUserID"] = bili["dedeuserid"]
        if not (out.get("SESSDATA") and out.get("bili_jct")):
            cached = read_json(COOKIE_PATH, {}) or {}
            for k, v in cached.items():
                out.setdefault(k, v)
        return out

    @property
    def csrf(self) -> str:
        return self.session.cookies.get("bili_jct", "") or ""

    def _request(self, method: str, path: str, *, params=None, data=None,
                 write: bool = False, allow_null: bool = False):
        url = path if path.startswith("http") else API + path
        limiter = self._write if write else self._read
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            limiter.wait()
            try:
                resp = self.session.request(method, url, params=params, data=data, timeout=20)
            except requests.RequestException as exc:
                last_exc = exc
                self._backoff(attempt, f"网络错误 {exc.__class__.__name__}")
                continue

            if resp.status_code == 412:
                self._backoff(attempt, "HTTP 412 风控拦截")
                continue
            if resp.status_code >= 500:
                self._backoff(attempt, f"HTTP {resp.status_code}")
                continue
            if resp.status_code != 200:
                raise BiliError(resp.status_code, f"HTTP {resp.status_code}", url)

            try:
                payload = resp.json()
            except ValueError:
                raise BiliError(-1, "返回不是 JSON（可能被风控或接口已变更）", url)

            code = payload.get("code", 0)
            if code == 0:
                body = payload.get("data")
                if body is None and not allow_null:
                    raise AuthError(0, "接口返回 data=null（登录态失效或无权限）", url)
                return body
            if code in AUTH_CODES:
                raise AuthError(code, payload.get("message", ""), url)
            if code in RETRYABLE_CODES and attempt < self.max_retries:
                self._backoff(attempt, f"业务码 {code} {payload.get('message', '')}")
                continue
            raise BiliError(code, payload.get("message", ""), url)

        raise BiliError(-1, f"重试 {self.max_retries} 次后仍失败：{last_exc}", url)

    def _backoff(self, attempt: int, reason: str) -> None:
        wait = (self.backoff_base ** attempt) + random.uniform(0, 0.6)
        log.warn(f"{reason}，{wait:.1f}s 后重试（第 {attempt}/{self.max_retries} 次）")
        time.sleep(wait)

    def get(self, path: str, **params):
        return self._request("GET", path, params=params or None)

    def post(self, path: str, **data):
        payload = dict(data)
        payload["csrf"] = self.csrf
        # 写接口成功后通常返回 data:null，不能当成登录失效
        return self._request("POST", path, data=payload, write=True, allow_null=True)

    # ---- 业务接口 ----
    def nav(self) -> dict:
        data = self._request("GET", "/x/web-interface/nav", allow_null=True) or {}
        if not data.get("isLogin"):
            raise AuthError(-101, "登录态已失效，请重新运行: python scripts/fetch_library.py --login")
        self.mid = int(data.get("mid") or 0)
        self.uname = data.get("uname") or ""
        return data

    def list_folders(self) -> list[dict]:
        if self.mid is None:
            self.nav()
        data = self.get("/x/v3/fav/folder/created/list-all",
                        up_mid=self.mid, web_location="333.1387")
        folders = (data or {}).get("list") or []
        if not folders:
            folders, pn = [], 1
            while True:
                page = self.get("/x/v3/fav/folder/created/list",
                                up_mid=self.mid, pn=pn, ps=20, web_location="333.1387") or {}
                items = page.get("list") or []
                folders.extend(items)
                if not page.get("has_more") or not items:
                    break
                pn += 1
        return folders

    def folder_resources(self, media_id, page_size: int = 20) -> list[dict]:
        items, pn = [], 1
        while True:
            data = self.get("/x/v3/fav/resource/list", media_id=media_id, pn=pn,
                            ps=page_size, keyword="", order="mtime", type=0, tid=0,
                            platform="web")
            medias = (data or {}).get("medias") or []
            items.extend(medias)
            if not (data or {}).get("has_more") or not medias:
                break
            pn += 1
        return items

    def video_detail(self, bvid: str) -> dict:
        """一次拿到 标签 + 分区 + cid + 简介 + 时长。"""
        data = self.get("/x/web-interface/view/detail", bvid=bvid) or {}
        view = data.get("View") or {}
        raw_tags = data.get("Tags") or []
        tags = [
            t.get("tag_name", "")
            for t in raw_tags
            if t.get("tag_name") and not str(t.get("tag_name")).startswith("发现《")
        ]
        return {
            "cid": view.get("cid"),
            "aid": view.get("aid"),
            "tid": view.get("tid"),
            "tid_v2": view.get("tid_v2"),
            "description": (view.get("desc") or "").strip(),
            "duration": view.get("duration"),
            "published_at": view.get("pubdate"),
            "tags": tags,
        }

    def create_folder(self, title: str, intro: str = "", privacy: int = 0) -> str:
        data = self.post("/x/v3/fav/folder/add", title=title, intro=intro,
                         privacy=privacy, web_location="333.1387")
        media_id = (data or {}).get("id")
        if not media_id:
            raise BiliError(-1, f"创建收藏夹失败，返回：{data}")
        return str(media_id)

    def deal_resource(self, aid, add_media_ids=None, del_media_ids=None) -> None:
        """把稿件加入/移出若干收藏夹（不删除稿件本身）。"""
        self.post("/x/v3/fav/resource/deal", rid=aid, type=2,
                  add_media_ids=",".join(str(x) for x in (add_media_ids or [])),
                  del_media_ids=",".join(str(x) for x in (del_media_ids or [])),
                  platform="web", web_location="333.1387")


# ======================================================================
# 分区映射表：tname 恒为空，只能从排行榜/相关视频反查
# ======================================================================
class PartitionMap:
    TOP_LEVEL_IDS = [1001, 1003, 1004, 1005, 1006, 1007, 1008, 1010, 1011,
                     1012, 1017, 1020, 1021, 1022, 1024, 1029, 1032]
    TYPES = ("all", "rookie", "origin")

    def __init__(self) -> None:
        self.data = read_json(PARTITION_MAP_PATH, {}) or {}

    @property
    def by_id(self) -> dict:
        return self.data.get("tname_v2") or {}

    def name_of(self, tid_v2) -> str:
        return self.by_id.get(str(tid_v2), "")

    def parent_of(self, tid_v2) -> str:
        return (self.data.get("tidv2_to_pname") or {}).get(str(tid_v2), "")

    def is_empty(self) -> bool:
        return not self.by_id

    def build(self, client: BiliClient, refresh: bool = False) -> dict:
        tname_v2: dict[str, str] = {}
        pname_v2: dict[str, str] = {}
        to_parent: dict[str, str] = {}
        tname_v1: dict[str, str] = {}

        # 合并式更新：某次采集被限流也不该丢掉已有映射
        if PARTITION_MAP_PATH.exists() and not refresh:
            old = read_json(PARTITION_MAP_PATH, {}) or {}
            tname_v2.update(old.get("tname_v2") or {})
            pname_v2.update(old.get("pname_v2") or {})
            to_parent.update(old.get("tidv2_to_pname") or {})
            tname_v1.update(old.get("tname_v1") or {})

        def absorb(items) -> int:
            n = 0
            for it in items or []:
                if it.get("pid_v2") and it.get("pid_name_v2"):
                    pname_v2[str(it["pid_v2"])] = it["pid_name_v2"]
                if it.get("tidv2") and it.get("tnamev2"):
                    tname_v2[str(it["tidv2"])] = it["tnamev2"]
                    if it.get("pid_name_v2"):
                        to_parent[str(it["tidv2"])] = it["pid_name_v2"]
                if it.get("tid") and it.get("tname"):
                    tname_v1[str(it["tid"])] = it["tname"]
                n += 1
            return n

        def harvest(path: str, **params) -> int:
            try:
                data = client.get(path, **params) or {}
            except Exception:  # noqa: BLE001 - 单个来源失败不影响整体
                return 0
            return absorb(data.get("list"))

        log.step("采集分区映射（排行榜 + 热门榜 + 入站必刷）…")
        total = 0
        for rid in self.TOP_LEVEL_IDS:
            for typ in self.TYPES:
                total += harvest("/x/web-interface/ranking/v2", rid=rid, type=typ)
        for typ in self.TYPES:
            total += harvest("/x/web-interface/ranking/v2", rid=0, type=typ)
        for pn in range(1, 7):
            total += harvest("/x/web-interface/popular", pn=pn, ps=50)
        for pn in (1, 2):
            total += harvest("/x/web-interface/popular/precious", pn=pn, ps=100)

        total += self._fill_gaps(client, tname_v2)

        self.data = {
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "ranking/v2 + popular + precious + archive/related",
            "sampled_items": total,
            "pname_v2": pname_v2,
            "tname_v2": tname_v2,
            "tidv2_to_pname": to_parent,
            "tname_v1": tname_v1,
        }
        write_json(PARTITION_MAP_PATH, self.data)
        log.ok(f"分区映射表：顶层 {len(pname_v2)} 类 / 子分区 {len(tname_v2)} 个")
        return self.data

    def _fill_gaps(self, client: BiliClient, tname_v2: dict) -> int:
        """用「相关视频」接口补齐排行榜没覆盖到的分区（相关视频与本视频同分区）。"""
        enriched = read_json(DATA / "enriched.json", {}) or {}
        if not enriched:
            return 0
        sample: dict[str, str] = {}
        for iid, info in enriched.items():
            tid = str(info.get("tid_v2") or "")
            if tid and tid not in tname_v2 and tid not in sample:
                sample[tid] = iid
        if not sample:
            return 0

        log.info(f"  用相关视频接口补 {len(sample)} 个未覆盖分区…")
        n = 0
        for tid, bvid in sample.items():
            try:
                related = client.get("/x/web-interface/archive/related", bvid=bvid) or []
            except Exception:  # noqa: BLE001
                continue
            for it in related:
                if str(it.get("tidv2") or "") == tid and it.get("tnamev2"):
                    tname_v2[tid] = it["tnamev2"]
                    n += 1
                    break
        return n


# ======================================================================
# 浏览器登录
# ======================================================================
def browser_login(cfg: dict, timeout: int = 300) -> dict:
    """用 Playwright 驱动系统 Chrome 扫码登录，抓取 Cookie 存本地。"""
    try:
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("未安装 playwright。请先运行：pip install playwright")

    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)
    log.step("启动 Chrome 进行登录…")
    log.info("如果浏览器里已经是登录状态，会自动跳过扫码。")

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_PROFILE), channel="chrome", headless=False,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-first-run", "--no-default-browser-check"],
                viewport={"width": 1280, "height": 860},
            )
        except PWError as exc:
            raise SystemExit(
                f"启动 Chrome 失败：{exc}\n请确认本机已安装 Google Chrome，"
                "且没有其他程序占用该配置目录。"
            )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto("https://www.bilibili.com/", wait_until="domcontentloaded")
            cookies = _wait_for_login(ctx, timeout)
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass

    write_json(COOKIE_PATH, cookies)
    log.ok(f"登录态已保存到 {COOKIE_PATH.name}")
    client = BiliClient(cfg, need_login=True)
    info = client.nav()
    log.ok(f"验证通过：已登录为 {info.get('uname')} (mid={info.get('mid')})")
    return cookies


def _wait_for_login(ctx, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    last_notice = 0.0
    prompted = False

    while time.monotonic() < deadline:
        jar = {}
        for c in ctx.cookies():
            if "bilibili.com" in (c.get("domain") or ""):
                jar[c["name"]] = c["value"]
        if jar.get("SESSDATA") and jar.get("bili_jct") and jar.get("DedeUserID"):
            log.ok("检测到登录成功")
            return jar

        now = time.monotonic()
        if not prompted:
            log.info(">>> 请在打开的 Chrome 窗口里扫码登录 B站（右上角「登录」）")
            prompted = True
            last_notice = now
        elif now - last_notice > 30:
            log.info(f"仍在等待登录…剩余 {int(deadline - now)}s")
            last_notice = now
        time.sleep(2.0)

    raise SystemExit(f"等待登录超时（{timeout}s）。请重新运行。")


# ======================================================================
# 适配器实现
# ======================================================================
class BilibiliAdapter:
    """B站收藏夹适配器。"""

    name = "bilibili"

    def __init__(self, cfg: dict, need_login: bool = True) -> None:
        self.cfg = cfg
        self.client = BiliClient(cfg, need_login=need_login)
        self.partitions = PartitionMap()
        self._raw_items: dict[str, dict] = {}   # bvid -> 原始条目（含 aid/cid）

    # ---- 认证 ----
    def authenticate(self) -> None:
        self.client.nav()

    # ---- 读 ----
    def list_collections(self) -> list[Collection]:
        raw = self.client.list_folders()
        out = []
        for f in raw:
            attr = int(f.get("attr") or 0)
            title = (f.get("title") or "").strip()
            out.append(Collection(
                id=str(f.get("id")),
                name=title,
                count=int(f.get("media_count") or 0),
                # B站默认收藏夹 = 收件箱
                is_inbox=bool(attr & 1) or title == "默认收藏夹",
                meta={"attr": attr},
            ))
        out.sort(key=lambda c: (not c.is_inbox, c.name))
        return out

    def list_item_ids(self, collection_id: str) -> list[str]:
        return [it["bvid"] for it in self.client.folder_resources(collection_id)
                if it.get("bvid")]

    def fetch_items(self, item_ids: list[str]) -> list[Item]:
        """从收藏夹列表接口取条目；去重后每个 bvid 只保留一条，containers 记全。"""
        by_id: dict[str, Item] = {}
        for col in self.list_collections():
            for raw in self.client.folder_resources(col.id):
                bvid = raw.get("bvid")
                if not bvid:
                    continue
                self._raw_items[bvid] = raw
                item = by_id.get(bvid)
                if item is None:
                    upper = raw.get("upper") or {}
                    cnt = raw.get("cnt_info") or {}
                    item = Item(
                        id=bvid,
                        title=(raw.get("title") or "").strip(),
                        description=(raw.get("intro") or "").strip(),
                        duration=raw.get("duration"),
                        creator_id=str(upper.get("mid") or "") or None,
                        creator_name=(upper.get("name") or "").strip(),
                        link=raw.get("link") or f"https://www.bilibili.com/video/{bvid}",
                        added_at=raw.get("fav_time"),
                        published_at=raw.get("pubtime"),
                        containers=[],
                        extra={"aid": raw.get("id"), "play": cnt.get("play")},
                    )
                    by_id[bvid] = item
                if col.id not in item.containers:
                    item.containers.append(col.id)
        return list(by_id.values())

    def enrich(self, item: Item, workers: int | None = None) -> Item:
        rl = self.cfg.get("rate_limit", {})
        workers = workers or int(rl.get("read_workers", 3))

        if self.partitions.is_empty():
            self.partitions.build(self.client, refresh=True)

        try:
            detail = self.client.video_detail(item.id)
        except BiliError as exc:
            permanent = exc.code in PERMANENT_ERROR_CODES
            item.unavailable = permanent
            item.unavailable_reason = str(exc)[:200]
            item.extra["error_code"] = exc.code
            item.extra["permanent"] = permanent
            return item

        item.description = item.description or detail.get("description") or ""
        item.duration = item.duration or detail.get("duration")
        item.tags = detail.get("tags") or []
        item.category_id = str(detail.get("tid_v2") or detail.get("tid") or "") or None
        item.category_name = self.partitions.name_of(detail.get("tid_v2")) or \
            self.partitions.name_of(detail.get("tid"))
        item.extra.update({
            "aid": detail.get("aid") or item.extra.get("aid"),
            "cid": detail.get("cid"),
            "tid": detail.get("tid"),
            "tid_v2": detail.get("tid_v2"),
            "parent_category": self.partitions.parent_of(detail.get("tid_v2")),
        })
        return item

    def enrich_many(self, items: list[Item], cfg: dict, refresh: bool = False) -> list[Item]:
        """并发富化，增量落盘（中断可续）。"""
        rl = cfg.get("rate_limit", {})
        workers = int(rl.get("read_workers", 3))
        cache_path = DATA / "enriched.json"
        cache = {} if refresh else (read_json(cache_path, {}) or {})

        todo = []
        for it in items:
            cached = cache.get(it.id)
            if cached and not (cached.get("error") and not cached.get("permanent")):
                _apply_cache(it, cached)
                continue
            todo.append(it)

        if not todo:
            log.ok(f"富化数据已完整（{len(items)} 条），跳过")
            return items

        log.step(f"富化 {len(todo)} 个条目（并发 {workers}，已缓存 {len(items) - len(todo)}）…")
        done, gone, failed = 0, 0, 0
        started = time.monotonic()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.enrich, it): it for it in todo}
            for fut in as_completed(futures):
                item = futures[fut]
                done += 1
                try:
                    fut.result()
                except Exception as exc:  # noqa: BLE001 - 单条失败不中断整体
                    failed += 1
                    item.unavailable = False
                    item.extra["error"] = str(exc)[:200]
                    item.extra["permanent"] = False
                if item.unavailable:
                    gone += 1
                cache[item.id] = _dump_item(item)
                if done % 50 == 0 or done == len(todo):
                    elapsed = time.monotonic() - started
                    rate = done / elapsed if elapsed else 0
                    eta = (len(todo) - done) / rate if rate else 0
                    log.info(f"  富化 {done}/{len(todo)}  ({rate:.1f}/s, 剩余约 {eta/60:.1f} 分钟)")
                    write_json(cache_path, cache)   # 增量落盘

        write_json(cache_path, cache)
        if gone:
            log.warn(f"{len(gone)} 个条目已失效（被删除/设为私密），无法分类，将原样保留")
        if failed:
            log.warn(f"{len(failed)} 个条目富化失败（可重跑补全）")
        log.ok(f"富化完成：可用 {len(todo) - gone - failed} 条，失效 {gone} 条")
        return items

    # ---- 写 ----
    def create_collection(self, name: str, **kwargs) -> str:
        return self.client.create_folder(name, intro=kwargs.get("intro", "由自动分类脚本创建"))

    def assign(self, item_id: str, add_ids: list[str], remove_ids: list[str],
               extra: dict | None = None) -> None:
        aid = (extra or {}).get("aid") or (self._raw_items.get(item_id) or {}).get("id")
        if not aid:
            raise BiliError(-1, f"缺少 aid，无法收藏 {item_id}")
        self.client.deal_resource(aid, add_media_ids=add_ids, del_media_ids=remove_ids)

    # ---- 可选 ----
    def resolve_category(self, raw_id) -> str:
        return self.partitions.name_of(raw_id)

    def in_collection(self, collection_id: str, item_id: str) -> bool:
        """真实回读确认，用于往返验证。"""
        return any(it.get("bvid") == item_id
                   for it in self.client.folder_resources(collection_id))


def _dump_item(item: Item) -> dict:
    d = {
        "tags": item.tags or [],
        "category_id": item.category_id,
        "category_name": item.category_name,
        "description": (item.description or "")[:500],
        "duration": item.duration,
        "unavailable": item.unavailable,
        "unavailable_reason": item.unavailable_reason,
    }
    d.update({k: v for k, v in item.extra.items() if k in ("aid", "cid", "tid", "tid_v2", "error", "error_code", "permanent")})
    return d


def _apply_cache(item: Item, cached: dict) -> None:
    item.tags = cached.get("tags") or []
    item.category_id = cached.get("category_id")
    item.category_name = cached.get("category_name") or ""
    if not item.description:
        item.description = cached.get("description") or ""
    item.unavailable = bool(cached.get("unavailable"))
    item.unavailable_reason = cached.get("unavailable_reason") or ""
    for k in ("aid", "cid", "tid", "tid_v2"):
        if cached.get(k) is not None:
            item.extra[k] = cached[k]
