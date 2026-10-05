# 步骤1：抓取方法与风控要点

## 数据源分层

| 来源 | 凭据 | 拿到什么 | 说明 |
|---|---|---|---|
| 收藏夹列表接口 | 需登录 | 全部收藏夹（含默认收藏夹） | 未登录时返回 `code=0` 且 `data=null`，**不报错** |
| 收藏夹内容接口 | 需登录 | 条目列表（标题/作者/时长/收藏时间） | 每页 20 条，自动翻页 |
| 视频详情接口 | **免登录** | 标签 + 分区 id + cid + 简介 + 时长 | 富化的主力，一次拿全 |
| 分区映射表 | 免登录 | `tid_v2` → 分区名 | 需另行采集，见下 |

## 登录：浏览器扫码（推荐）

```
python scripts/fetch_library.py --login
```

用 Playwright 驱动**系统已安装的 Chrome**（`channel="chrome"`），
不需要下载浏览器内核。打开窗口让用户扫码，Cookie 自动存入
`scripts/bilibili_cookies.json`。

- 使用持久化用户目录 `scripts/.browser_profile/`，登录一次后下次免扫码
- 不读取用户日常浏览器的配置，避免碰到其他网站的登录信息
- 拿到的 Cookie 里 `SESSDATA` + `bili_jct` 是必需的；
  `bili_jct` 同时用作写操作的 csrf token

也可以手动填 `scripts/local_config.json` 的 `bilibili.sessdata` / `bili_jct`，
或用环境变量 `BILI_SESSDATA` / `BILI_JCT`（环境变量优先）。

## 必须显式校验 `data=null`

B站收藏夹类接口在**未登录或无权限时返回 `code=0` + `message="OK"` + `data=null`**，
而不是返回错误码。所以判断成功的条件不是 `code==0`，而是 `code==0 且 data 非空`。
`core/adapters/bilibili.py: BiliClient._request` 已经处理了这一点，
新写接口调用时不要绕过它。

同理，**写接口成功后返回的也是 `data=null`**，不能当成登录失效。
写请求统一走 `allow_null=True`。

## 分区名要自己反查

视频详情接口的 `tname` / `tname_v2` 实测**恒为空字符串**，只有数字 `tid` / `tid_v2`。
而分区名是判断体裁的重要线索，所以 `core/adapters/bilibili.py: PartitionMap`
从这些接口反查：

| 接口 | 覆盖 |
|---|---|
| `/x/web-interface/ranking/v2?rid={pid_v2}&type={all,rookie,origin}` | 顶层 17 个分区的排行榜 |
| `/x/web-interface/popular` | 热门榜，覆盖不同子分区 |
| `/x/web-interface/popular/precious` | 入站必刷，覆盖冷门子分区 |
| `/x/web-interface/archive/related` | **按视频逐个补齐**——相关视频与本视频同分区 |

映射表缓存到 `data/partition_map.json`，采用**合并式更新**：
某次采集被限流也不会丢掉已有映射。

## 富化

逐条调用视频详情接口补标签/分区/简介。这是唯一逐条发请求的环节。

- 并发由 `rate_limit.read_workers` 控制，默认 3；全局最小间隔 `read_min_interval`，默认 0.35s
- 结果增量落盘（每 50 条），中断后重跑自动续传
- **失效条目**（`62002 稿件不可见` / `-404 啥都木有` / `-403`）标记为永久不可用，
  后续不再重试、不参与分类、不会被移动
- 其他错误（网络抖动）下次重跑会补

## 风控要点

B站 API 的非官方文档仓库（bilibili-API-collect）已于 2026-01 因律师函**永久关停**，
平台对自动化访问的态度在收紧。所以：

- 使用真实 UA 与 Referer
- 复用真实登录态，不要用匿名请求硬刷
- 读接口 ≥0.35s/次，写接口 ≥2s/次（`scripts/local_config.json` 可调，**不建议调小**）
- 遇到 `-412`（风控拦截）/ `-509` / `-799` 自动指数退避重试
- 写操作连续失败 3 次即中止，不要硬撞

## 产出与汇报

产出 `step1/collections.json` 与 `step1/items.json`。

**汇报内容**：收藏夹数量、条目总数、失效条目数。**等待确认**。
