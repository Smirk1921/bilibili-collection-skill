# 增量模式：已分类过，收藏夹里新加了视频

## 核心设计

**`step2/preclassification.json` 是「已分类基准」的唯一来源，不另设状态文件。**
重复运行天然幂等——这是从 steam-collection-skill 学来的结论，
避免了"总表和状态文件不一致"这一类经典问题。

## 流程

```
python scripts/incremental.py detect           # 对比现状与基准
python scripts/incremental.py prepare --size 30
# 把 step5/batches/ 交给子代理，结果写入 step5/results/
python scripts/incremental.py merge
python scripts/incremental.py apply --confirm
```

```
detect   -> step5/diff.json + step5/diff_report.txt
            新增条目合并进 step1/items.json，已消失的条目只报告不动
prepare  -> step5/batches/batch_NNN.txt（协议同步骤3）
merge    -> step5/review_labels.json + step5/change_report.txt
apply    -> 备份后合并进 step2/preclassification.json
```

## 各步要点

### detect

- 重新抓取当前收藏夹，与基准取差集
- **已消失的条目默认保留在总表里**（`apply --prune` 才移除）——
  它们只是被移出了收藏夹，留在总表里无害，而删掉会丢失分类记录
- 支持位置参数强制重分类指定条目：`incremental.py detect BV1xx BV2yy`
  （上次待定的、用户点名的），会带上现有分类值

### 收件箱

增量模式下，新收藏的视频通常都在**默认收藏夹**（收件箱）里。
`empty_inbox` 生效时，归类后会从中移出。

### 分类与批次

`prepare` 复用步骤3 的批次渲染逻辑（`review_tools._render_batch`），
协议完全一致，所以子代理的提示词可以直接沿用。

### merge / apply

`merge` 与 `apply` 复用 `review_tools` 的实现，只是把
`store.REVIEW_LABELS_PATH` 指向 `step5/review_labels.json`。
`apply` 前会备份 `step2/preclassification.json` 到 `step2/backup/`。

## 汇报模板

```
增量检测完成：
- 收藏夹现有 N 条，已分类基准 M 条
- 新增 X 条，已消失 Y 条（默认保留，仅报告）
- 待复核 X 条，分 K 个批次
**等待确认**后进入复核。
```
