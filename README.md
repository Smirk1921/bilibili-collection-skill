# B站收藏夹整理分类 · 使用说明

把杂乱的 B站收藏夹自动整理成一套清晰的分类体系，并写回你的 B站账号。

本任务包遵循通用 Agent Skills 约定（一个文件夹 + `SKILL.md`），
Codex / Claude Code / dsh / ZCode 都能直接用。

## 快速开始

```bash
# 1) 扫码登录（只需一次，登录态存在本地）
python scripts/fetch_library.py --login

# 2) 抓取收藏夹与视频列表
python scripts/fetch_library.py

# 3) 生成分类体系 + 预分类
python scripts/build_taxonomy.py

# 4) 复核（可选但强烈推荐，实测能把准确率从 85% 提到 95%+）
python scripts/review_tools.py prepare --size 30
#    ... 把批次交给 AI 逐条判定，结果写入 step3/results/ ...
python scripts/review_tools.py merge
python scripts/review_tools.py apply --confirm

# 5) 生成计划，先看 dry-run 再执行
python scripts/build_plan.py
python scripts/apply_collections.py            # 预览
python scripts/apply_collections.py --confirm  # 真正执行

# 出问题了随时还原
python scripts/rollback.py --confirm
```

也可以不敲命令，直接把需求告诉 AI 助手：*"帮我整理一下 B站收藏夹"*，
它会读 `SKILL.md` 按流程走，每一步都会停下来等你确认。

## 你需要准备什么

| 项目 | 说明 |
|---|---|
| Python 3.9+ | `pip install -r requirements.txt` |
| Google Chrome | 扫码登录用。**不需要**下载 Playwright 浏览器内核，直接用系统 Chrome |
| B站账号 | 能扫码登录即可 |
| 时间 | 首次抓取约 3 分钟（逐条补标签），之后走缓存 |

## 安装到你的 AI 助手

把 `bilibili-collection-skill/` 复制到对应目录并**重命名为 `bilibili-collection`**
（必须与 SKILL.md 里声明的 name 一致）。

| 工具 | 目录 |
|---|---|
| OpenAI Codex | `~/.codex/skills/bilibili-collection` （项目级：`.codex/skills/`） |
| Claude Code | `~/.claude/skills/bilibili-collection` （装好后需重启） |
| dsh | `~/.dsh/skills/bilibili-collection` 或 `~/.agents/skills/bilibili-collection` |
| ZCode | `~/.zcode/skills/bilibili-collection` 或 `~/.agents/skills/bilibili-collection` |

Windows 路径示例：`C:\Users\<你的用户名>\.zcode\skills\bilibili-collection\`

> 小技巧：`~/.agents/skills` 是 dsh 和 ZCode 共同扫描的跨工具位置，两个都用的话装一份就够。

## 四个步骤（你会看到什么）

| 步骤 | 做什么 | 你要做什么 |
|---|---|---|
| 1 抓取 | 读收藏夹与视频列表，补标签/分区/简介 | 首次扫码；确认列表无误 |
| 2 分类体系 | 生成类目（体裁 + 主题双轴）并预分类 | 确认类目是否合理 |
| 3 复核 | AI 逐条看标题+分区+简介，修正判错的 | 看变更报告 |
| 4 写入 | 建收藏夹 + 追加收藏 | **写前明确同意** |

**写入是「追加收藏」不是「移动」**：视频被加入目标收藏夹，原有收藏夹保持不变。
唯一例外是默认收藏夹（收件箱）——归类后会从中移出，否则它永远是乱的。
**不会删除任何视频、不会取消任何收藏。**

## 分类是怎么做的

三级流水线，规则优先、AI 兜底：

1. **手写规则**（`local_config.json` 的 `rules`）——命中即归类，优先级最高
2. **体裁维度**——按 B站分区 + 简介关键词判定"这是什么形式的视频"
   （MAD / 实况 / 攻略 / 解析 / 音乐 / 广播剧…）
3. **主题维度**——先看明确信号，再看已有收藏夹的画像打分
   （游戏 / 动画 / AI / 哲学 / 百合 / 乐队…）
4. **AI 复核**——前几层判不准的，交给 AI 带上完整上下文逐条判定

**为什么规则不够**：实测纯规则准确率上限约 85%。分区会被 UP主 选错
（有个 AI 动画 MV 被投到了「工程机械」分区）、标签会误导
（`galgame` 是载体不是题材）、中文弱词会产生假阳性
（「对话**技巧**」把角色分析判成了攻略）。所以必须有复核环节。

一个视频可以同时归入多个收藏夹，比如 `world.execute(me)` → `MAD` + `AI · 算法`。

## 日常收藏了新视频？（增量模式）

```bash
python scripts/incremental.py detect
python scripts/incremental.py prepare --size 30
#  ... 复核 ...
python scripts/incremental.py merge
python scripts/incremental.py apply --confirm
```

只处理新增部分。分类总表本身就是"已分类基准"，不另设状态文件，重复运行幂等。

## 常见问题

**会动我原来的收藏夹吗？**
不会。已有收藏夹的名称和内容都不会被改名或清空，只会往里面**增加**视频。
唯一会移出的是默认收藏夹（收件箱），这可以在 `local_config.json` 里关掉。

**判错了怎么办？**
所有写入都记在 `step4/rollback.jsonl`，`python scripts/rollback.py --confirm` 一键还原。
改分类也很简单：改 `core/adapters/bilibili_genre_rules.json` 里的规则，
或者用 `local_config.json` 的手写规则覆盖，然后重跑步骤 2 起。

**有些视频是灰色的/打不开**
那是被 UP主 删除或设为私密的失效稿件。工具会单独识别出来，不做任何处理。

**会被 B站 风控吗？**
工具内置了限速（读 0.35s/次、写 2s/次）、指数退避、连续失败自动中止。
按默认参数跑是安全的，**不要调小间隔**。

**收藏夹数量有上限吗？**
B站 未公开这个限制。工具会在写入前报告将新建多少个收藏夹，你确认后再执行。

## 给技术用户的备注

- `core/` 是平台无关层，`core/adapters/bilibili.py` 是唯一知道 B站 细节的地方。
  换平台只需实现 `Adapter` 协议，见 `references/porting-guide.md`
- 所有脚本都能独立运行，`python scripts/xxx.py --help` 看用法
- 每步产出放独立子目录（`step1/` `step2/` …），方便检查与回溯
- 离线自测：`python tests/smoke_test.py`（合成数据，不碰网络）
- 配置在 `scripts/local_config.json`（从 `local_config.template.json` 复制），
  所有字段都可用环境变量覆盖且环境变量优先
- `scripts/` 下的脚本可以单独拷走用，但需要连带 `core/`

## 免责声明

- 本工具使用 B站 的**非公开接口**，接口随时可能变更
- B站 API 的非官方文档仓库已于 2026-01 因律师函永久关停，
  平台对自动化访问的态度在收紧。请自行评估使用风险，控制使用频率
- 凭据（Cookie）只存在本地 `scripts/` 目录，已在 `.gitignore` 中排除，
  **绝不要提交或分享**
- 本工具与哔哩哔哩无任何关联。使用者需自行承担账号风险
- MIT License
