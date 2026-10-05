# 步骤4：收藏计划与写入协议

## 写入语义：追加收藏，不是移动

用 `POST /x/v3/fav/resource/deal` 的 `add_media_ids` / `del_media_ids`：

- **加入目标收藏夹，原有收藏夹保持不变** —— 这是非破坏性的
- 唯一会移除的地方是**收件箱**（B站默认收藏夹）：归类后从中移出，
  否则它永远是乱的。`execute.empty_inbox` 可关掉
- **绝不删除视频、绝不取消收藏**，只做收藏夹归属的增删

为什么不用 `move`：移动会把视频从原收藏夹搬走，无法表达"一个视频同属多个收藏夹"。

## 收藏计划

```
python scripts/build_plan.py        # -> step4/plan.json（不碰线上数据）
```

计划结构：

```json
{
  "mode": "add",
  "collections_to_create": ["A01 体裁-MAD", "B05 主题-AI · 算法"],
  "target_map": {"A01 体裁-MAD": null, "缺氧": "<已有收藏夹id>"},
  "operations": [
    {"item_id": "BVxxxxxxxxxx", "title": "...", "add": ["A01 体裁-MAD", "B05 主题-AI · 算法"],
     "add_ids": [null, null], "remove": ["默认收藏夹"], "remove_ids": ["<收件箱id>"]}
  ]
}
```

`target_map` 里值为 `null` 表示该收藏夹还不存在，执行时会新建。

## 写前确认清单（向用户展示，必须得到肯定答复）

1. 将新建多少个收藏夹（**列出名字**）
2. 要处理多少个条目、共多少次收藏操作
3. 移出收件箱多少个条目
4. 有多少条目被跳过（已在正确位置 / 已失效 / 无法处理）
5. 说明可随时用 `python scripts/rollback.py --confirm` 还原

**用户明确同意后才执行写入。**

## 安全机制（都是实测有效的，不要绕过）

| 机制 | 实现 |
|---|---|
| 默认 dry-run | `apply_collections.py` 不带 `--confirm` 时只打印预览 |
| 逐条回滚日志 | 每成功一条立刻追加 `step4/rollback.jsonl` |
| 断点续跑 | 重跑时跳过已处理过的条目（按 rollback 日志判断） |
| 熔断 | 连续失败 3 次自动中止，避免在风控状态下硬撞 |
| 限速 | 写接口全局最小间隔 2.0s（`rate_limit.write_min_interval`） |
| 原子写 | 所有 JSON 先写 `.tmp` 再 replace |
| 往返验证 | 可先拿一个条目做「加入→回读确认→移除→回读确认」，验证接口格式没变 |

## 回滚

```
python scripts/rollback.py                 # 预览
python scripts/rollback.py --confirm       # 执行
python scripts/rollback.py --run-id <id>   # 只还原某一次运行
```

回滚日志每条记录 `added_ids` / `removed_ids`，回滚就是反向操作：
**把加进去的移除、移出来的加回去**。

本工具创建的收藏夹**不会被自动删除**——回滚时只列出名字，由用户决定。
B站 收藏夹一旦建错，手动删比程序删更安全。

## 常见失败

| 现象 | 原因 | 处理 |
|---|---|---|
| `code=-101` / `data=null` | 登录态过期 | 重新 `--login` |
| `code=-412` | 触发风控 | 停下来等一段时间，调大 `write_min_interval` |
| `code=10003` 收藏夹已存在 | 同名收藏夹被并发创建 | 检查 `target_map`，重跑会自动复用 |
| 连续失败中止 | 可能被限流 | 用 `--confirm` 重跑，已处理的会自动跳过 |
