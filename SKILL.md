---
name: bilibili-collection
description: 全自动把用户的 B站收藏夹整理分类并写回。四步流程：①抓取收藏夹与视频列表（浏览器扫码登录）②设计分类体系并预分类（体裁+主题双轴，可从已有收藏夹学习）③逐条复核修正 ④生成收藏计划并写入（写前强制确认）；已完成分类后新收藏的视频走增量模式（只处理新增部分）。当用户要求"整理B站收藏夹/给收藏夹分类/收藏夹太乱/帮我归类收藏的视频"，或说"又收藏了新视频/增量更新收藏夹"时使用。
---

# B站收藏夹整理分类

把用户的 B站收藏夹整理成一套清晰的分类体系。整个流程分四步，
**每一步完成后必须停下向用户汇报并等确认，用户同意后才进入下一步**。

分类体系默认以「**体裁 + 主题**」两维为核心：体裁回答"这是什么形式的视频"
（MAD / 实况 / 攻略 / 解析 / 音乐 / 广播剧…），主题回答"讲的是什么"
（游戏 / 动画 / AI / 哲学 / 百合 / 乐队…）。一个视频可以同时归入多个收藏夹。
维度是可配置的——用户想加「系列」「UP主」「观看状态」都可以，
AI 也可以提议新类目，但**必须经用户确认才写入类目定义**。

## 总览

```
步骤1  抓取收藏夹与视频列表   -> step1/collections.json + step1/items.json
         ↓ 用户确认列表无误
步骤2  分类体系 + 预分类      -> step2/taxonomy.json + categories.md + preclassification.json
         ↓ 用户确认类目与预分类
步骤3  AI 逐条复核修正        -> step3/review_labels.json + change_report.txt
         ↓ 用户确认修正报告
步骤4  生成收藏计划并写入      -> step4/plan.json -> 建夹 + 追加收藏 (写前必须再次确认)
         ↓
增量模式  收藏夹里新收藏了视频  -> 只处理新增部分 (step5/)
```

**写入语义是「追加收藏」而不是「移动」**：视频被加入目标收藏夹，
原有收藏夹保持不变。唯一例外是收件箱（B站默认收藏夹）——
归类后会从中移出，否则它永远是乱的。**不会删除任何视频、不会取消任何收藏。**

## 开工前：收集信息与已有材料

先确认这几件事，缺什么问什么：

| 需要 | 怎么拿到 | 缺失时 |
|---|---|---|
| 登录态 | `python scripts/fetch_library.py --login` 扫码 | 必填，否则收藏夹接口返回 `data=null` |
| 分类偏好 | 直接问用户 | 默认「体裁 + 主题」，可从已有收藏夹学习 |
| 不想动的收藏夹 | 问用户 | 填进 `execute.skip_collections` |
| 是否清空默认收藏夹 | 问用户 | 默认清空（`execute.empty_inbox`） |

**已有收藏夹本身就是用户给的分类先验**——步骤2 会把条目数 ≥3 的已有收藏夹
自动纳入候选类目，不要忽略它们另起炉灶。

## 步骤 1：抓取收藏夹与视频列表

```
python scripts/fetch_library.py                # 抓取（已有缓存则复用）
python scripts/fetch_library.py --login        # Cookie 缺失/过期时，浏览器扫码登录一次
python scripts/fetch_library.py --refresh      # 忽略缓存，全量重抓
python scripts/fetch_library.py --dry-run      # 只报告将要做什么，不写文件
```

要点：
- 登录用 Playwright 驱动**系统已安装的 Chrome**，不需要下载浏览器内核。
  打开窗口让用户扫码（约 30 秒），Cookie 存 `scripts/bilibili_cookies.json`。
- 抓取分两段：先拿收藏夹列表和条目（去重后按 id 归并所属收藏夹），
  再逐条富化标签/分区/简介。**富化是唯一逐条发请求的环节，已内置限速与并发控制。**
- 富化结果增量落盘，中断后重跑会自动续传。
- **失效条目**（被删除/设为私密）会被单独识别出来，标记为不可用，
  后续所有步骤都不会碰它们。

**汇报内容**：收藏夹数量、条目总数、失效条目数。**等待确认**。

## 步骤 2：分类体系设计 + 预分类

```
python scripts/build_taxonomy.py                 # 生成类目 + 预分类
python scripts/build_taxonomy.py --no-existing   # 不把已有收藏夹纳入候选类目
python scripts/build_taxonomy.py --no-report     # 跳过 Excel/Markdown 报告
```

产出 `step2/taxonomy.json`（类目定义，**唯一源头**）、`step2/categories.md`、
`step2/preclassification.json`（分类总表）与 `step2/pending_confirmation.json`（待确认清单）。
方法论与编号规范见 `references/categories-guide.md`。

要点：
- **体裁维度**由规则表判定（B站分区 + 简介关键词），客观、可复现。
- **主题维度**先看明确信号（规则表），再看已有收藏夹的画像打分。
- 类目名带编码前缀（`A01 体裁-MAD`）——B站收藏夹按名称排序时同维度自然聚在一起。
- **已有同名收藏夹会被复用原名，不会重命名用户的收藏夹。**
- 判不准的条目进待确认清单，**不要留空、不要瞎猜**。

**汇报**：类目数量与代表性成员、已归类数、待确认数。**等待确认**。

## 步骤 3：逐条复核（可选，但强烈推荐）

实测结论：**纯规则的准确率上限约 85%**——分区会选错、简介会缺失、
中文弱词会产生假阳性。所以规则给基线，再由 AI 带上完整上下文逐条复核。

```
python scripts/review_tools.py prepare --size 30   # 切分批次 -> step3/batches/
# 把批次交给子代理逐条判定，结果写入 step3/results/
python scripts/review_tools.py merge               # 汇总 -> step3/review_labels.json
python scripts/review_tools.py apply --confirm     # 回写分类总表
```

协议见 `references/methods-review.md`。结果行格式：

```
<item_id> | <目标分类，多个用 + 连接> | <置信度 0-1> | <判断依据>
```

`merge` 会自动修复子代理常见的格式问题（竖线/逗号混用、漏列、加粗标记、
行首序号、百分数置信度），修不了的行记进报告而不是静默丢弃。

**复核时必须让子代理看到「标题 + 作者 + 原生分类 + 标签 + 简介 + 当前收藏夹」**——
只给标题会重蹈 85% 的覆辙。

**汇报**：复核覆盖数、修正数、变化样例。**等待确认**。

## 步骤 4：生成收藏计划并写入

```
python scripts/build_plan.py              # 生成计划并预览（不碰线上数据）
python scripts/apply_collections.py       # 预览（dry-run）
python scripts/apply_collections.py --confirm   # 真正执行
python scripts/rollback.py --confirm      # 出问题时一键还原
```

**写前确认（强制）**：向用户展示 —— 将新建多少个收藏夹（列出名字）、
要处理多少个条目、多少次收藏操作、移出收件箱多少个、有没有条目被跳过；
并说明可随时回滚。**用户明确同意后才执行写入**。

安全机制（都是实测有效的，不要绕过）：
- 默认 dry-run，必须显式 `--confirm`
- 每成功一条立刻追加 `step4/rollback.jsonl`，中断可续、失败可还原
- 连续失败 3 次自动中止，避免在风控状态下硬撞
- 重复运行自动跳过已处理过的条目（断点续跑）
- 写入前可先做一次往返验证，确认接口参数格式没变

## 增量模式：已分类过，收藏夹里加了新视频

```
python scripts/incremental.py detect          # 对比现状与已分类基准
python scripts/incremental.py prepare --size 30
python scripts/incremental.py merge
python scripts/incremental.py apply --confirm
```

设计要点：**`step2/preclassification.json` 是「已分类基准」的唯一来源，
不另设 state 文件**，因此重复运行天然幂等。详见 `references/incremental-guide.md`。

## 通用守则

- 每步产出物放独立子目录（`step1/` `step2/` …），保持工作区整洁
- 凭据（Cookie / csrf token）只存本地配置文件，绝不写进任何产出物或汇报文本
- 一切写入类操作之前必须先备份并向用户确认
- 网络抓取带重试与退避；失败时给出可操作的下一步提示而不是静默失败
- **不改动用户的收藏夹名称**：已有同名收藏夹一律复用原名
- 遇到本 skill 未覆盖的情况，先查 `references/pitfalls.md` 的已知问题清单

## 文件结构

```
bilibili-collection-skill/
  SKILL.md                     本文件 (agent 执行入口)
  README.md                    人类用户使用说明 (技术向速览)
  使用说明-新手版.txt           人类用户使用说明 (零基础手把手, 纯文本)
  CHANGELOG.md                 版本与设计决策记录
  references/
    methods-fetch.md           步骤1 抓取方法与风控要点
    categories-guide.md        步骤2 分类体系方法论与编号规范
    methods-review.md          步骤3 复核批次协议与结果格式
    write-guide.md             步骤4 写入协议、安全机制与回滚
    incremental-guide.md       增量模式
    pitfalls.md                实战踩坑清单（遇到异常先查这里）
    porting-guide.md           如何接入新平台（通用化说明）
  scripts/
    local_config.template.json 配置模板（复制为 local_config.json 后填写）
    fetch_library.py           步骤1 抓取（含 --login 浏览器登录）
    build_taxonomy.py          步骤2 类目生成 + 预分类
    review_tools.py            步骤3 批次切分/汇总/回写
    build_plan.py              步骤4a 生成收藏计划
    apply_collections.py       步骤4b 执行写入
    rollback.py                步骤4c 回滚
    incremental.py             增量模式 detect/prepare/merge/apply
  core/                        平台无关核心层（换平台只需新增适配器）
    model.py                   Item / Collection / Taxonomy 数据模型
    text.py  safety.py         文本处理 / 日志限速备份
    scoring.py rules.py        打分引擎 / 规则级联引擎
    taxonomy.py                分类体系构建与校验
    classify.py plan.py        分类与计划
    apply.py report.py         写入回滚与报告
    store.py paths.py config.py
    adapters/
      __init__.py              Adapter 协议（通用化的接口）
      bilibili.py              B站适配器（唯一知道 B站细节的地方）
      bilibili_genre_rules.json B站体裁判定规则表（数据，非代码）
  step1/ … step5/              各步产出（gitignore）
  data/ out/                   缓存与报告（gitignore）
  tests/smoke_test.py          离线冒烟测试（合成数据，不碰网络）
```
