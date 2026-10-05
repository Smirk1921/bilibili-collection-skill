# -*- coding: utf-8 -*-
"""离线冒烟测试：用合成数据跑通 步骤2→步骤4a 全链路，不需要登录、不碰网络。

用法: python tests/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import classify as classify_mod  # noqa: E402
from core import config, plan as plan_mod, store, taxonomy as tax_mod  # noqa: E402
from core.model import Collection, Item  # noqa: E402
from core.safety import log  # noqa: E402

RULES = {
    "form": {
        "strong": [
            {"match": {"text_regex": "原创动画|二创|二次创作|手书|AMV|(?-i:\\bMAD\\b)|live2d|MMD|立绘"},
             "to": "MAD"},
            {"match": {"text_regex": "广播剧|音声"}, "to": "广播剧"},
        ],
        "native_category": [
            {"match": {"native_category_any": ["鬼畜", "音MAD"]}, "to": "鬼畜"},
            {"match": {"native_category_any": ["动漫剪辑", "同人动画"]}, "to": "MAD"},
            {"match": {"native_category_any": ["动漫评论", "影视杂谈"]}, "to": "解析 · 杂谈"},
            {"match": {"native_category_any": ["MV", "翻唱", "演奏"]}, "to": "音乐"},
        ],
        "text": [
            {"match": {"text_regex": "攻略|教学|教程|指南"}, "to": "攻略 · 教学"},
            {"match": {"text_regex": "实况|流程|通关|全语音"}, "to": "实况"},
            {"match": {"text_regex": "解析|杂谈|拆解|分析"}, "to": "解析 · 杂谈"},
        ],
        "native_category_weak": [
            {"match": {"native_category_any": ["沙盒类", "其他游戏"]}, "to": "实况"},
        ],
    },
    "subject": {
        "strong": [
            {"match": {"text_regex": "百合|(?<![A-Za-z])GL(?![A-Za-z])|利兹与青鸟"}, "to": "百合 · GL"},
        ],
        "text": [
            {"match": {"text_regex": "缺氧"}, "to": "缺氧"},
            {"match": {"text_regex": "原神"}, "to": "原神"},
            {"match": {"text_regex": "MyGO|BanG Dream|GBC"}, "to": "乐队动画"},
            {"match": {"text_regex": "人工智能|大模型|Claude|GPT|AGI|语音合成|TTS"}, "to": "AI · 算法"},
        ],
    },
}

COLLECTIONS = [
    Collection(id="100", name="默认收藏夹", count=3, is_inbox=True),
    Collection(id="201", name="缺氧", count=3),
    Collection(id="202", name="百合 · GL", count=3),
    Collection(id="203", name="乐队动画", count=3),
]

# (id, 标题, 作者, 时长, 标签, 原生分类, 简介, 所在收藏夹)
RAW = [
    # 已有收藏夹（作为分类先验，每类 3 条以便立类）
    ("BV1", "【缺氧】壁虎养殖模块", "EWZP", 375, ["缺氧", "攻略", "模块"], "模拟经营游戏",
     "游戏：缺氧(Oxygen Not Included) 平台：Steam", ["201"]),
    ("BV2", "缺氧实用模块——自动收获乔木树", "TKtuo", 277, ["缺氧", "单机", "新人"], "模拟经营游戏",
     "缺氧萌新一枚", ["201"]),
    ("BV3", "【缺氧硬核模块】石油裂解的完美设计方案", "白夜鬼剑士", 569, ["缺氧", "攻略"], "模拟经营游戏",
     "", ["201"]),

    ("BV4", "【百合磕学】利兹与青鸟逐帧解析", "Gloxxa", 2051, ["利兹与青鸟", "京阿尼", "解析"], "动漫评论",
     "作品名《利兹与青鸟》", ["202"]),
    ("BV5", "【恋尽】白河豚漫画深度解析", "牛腩爆炒BGE", 1161, ["与你相恋到生命尽头", "百合"], "动漫评论",
     "本期视频是《与你相恋到生命尽头》的深度解析", ["202"]),
    ("BV6", "上伊那牡丹必须死于酒精中毒", "蓝瞳十七", 176, ["上伊那牡丹", "动画杂谈"], "动漫评论",
     "", ["202"]),

    ("BV7", "【MyGO/素睦】暗恋是一个人的事情", "纪祈集", 125, ["AMV", "BanG Dream!MyGO", "MAD"], "动漫剪辑",
     "原曲改编 AMV", ["203"]),
    ("BV8", "去死吧，为了CRYCHIC", "喔唷哦哟哟", 213, ["AMV", "MyGO", "MAD"], "动漫剪辑", "", ["203"]),
    ("BV9", "「BanG Dream! It's MyGO!!!!!」#1", "MyGO_AveMujica", 1431, ["BanG Dream!"], "MV",
     "感谢大家对MyGO!!!!!的支持", ["203"]),

    # 收件箱里待分发的条目
    ("BV10", "Claude用代码搓了13015帧4K视频", "穆阿蒂布", 217, ["Claude", "原创动画", "MV"], "工程机械",
     "本视频是非营利的个人二次创作，音乐：Mili", ["100"]),
    ("BV11", "今天吃了碗面", "生活UP", 300, ["美食", "生活"], "随拍·综合", "", ["100"]),
    ("BV12", "【已删除】某个视频", "某人", 100, [], "其他游戏", "", ["100"]),
]


def seed() -> tuple[dict, list]:
    cols = {c.id: c for c in COLLECTIONS}
    items = []
    for iid, title, creator, dur, tags, cat, desc, containers in RAW:
        it = Item(id=iid, title=title, creator_name=creator, duration=dur, tags=tags,
                  category_name=cat, description=desc, containers=containers,
                  link=f"https://example.invalid/{iid}", extra={"aid": 1000 + len(items)})
        if iid == "BV12":
            it.unavailable = True
            it.unavailable_reason = "code=62002 稿件不可见"
        items.append(it)
    store.save_collections(list(cols.values()))
    store.save_items(items)
    return cols, items


def main() -> int:
    log.step("冒烟测试：合成数据")
    cols, items = seed()
    cfg = config.load()

    # ---- 步骤2：分类体系 + L1 预分类 ----
    tax = tax_mod.build(cols, items, RULES, include_existing=True, min_existing_size=3)
    problems = tax_mod.validate(tax)
    assert not problems, f"分类体系校验失败：{problems}"
    log.ok(f"类目 {len(tax.categories)} 个：{[c.name for c in tax.categories]}")

    verdicts, pending = classify_mod.classify(items, cols, tax, RULES, cfg)
    classify_mod.print_summary(verdicts)

    # 断言：体裁判定正确
    by_id = {a["item_id"]: a for a in verdicts["assignments"]}
    assert "BV10" in by_id, "BV10 应被归类"
    assert "MAD" in " ".join(by_id["BV10"]["targets"]), \
        f"BV10 体裁应为 MAD，实际 {by_id['BV10']['targets']}"
    assert any("AI" in t for t in by_id["BV10"]["targets"]), \
        f"BV10 主题应含 AI · 算法，实际 {by_id['BV10']['targets']}"

    # 断言：百合信号只在真有百合信号时命中（BV10 简介含 Mili/WebGL 之类不应误判）
    assert not any("百合" in t for t in by_id.get("BV10", {}).get("targets", [])), \
        "BV10 不应被判为百合"

    # 断言：失效条目被单独识别
    assert len(verdicts["unavailable"]) == 1, \
        f"应有 1 条失效，实际 {len(verdicts['unavailable'])}"

    # 断言：完全无关的条目进入待复核
    assert any(p["item_id"] == "BV11" for p in pending), "BV11 应进入待复核"

    # ---- 步骤4a：计划 ----
    plan = plan_mod.build_plan(verdicts, None, items, cols, tax, cfg)
    plan_mod.print_summary(plan)
    store.save_plan(plan)
    store.save_verdicts(verdicts)
    assert plan["mode"] == "add", "必须是追加收藏语义"
    assert all(op["add"] for op in plan["operations"]), "操作必须有目标"

    # 断言：目标名要么复用已有收藏夹原名，要么用带编码的类目名（会新建）
    for op in plan["operations"]:
        for t in op["add"]:
            assert t in {c.name for c in cols.values()} or t[:1] in "ABCDE", \
                f"目标名不合法：{t}"

    log.info("")
    log.info("  操作明细：")
    for op in plan["operations"]:
        rm = f"  (移出 {'/'.join(op['remove'])})" if op["remove"] else ""
        log.info(f"    {op['item_id']:6} {op['title'][:30]:32} -> {' + '.join(op['add'])}{rm}")

    log.info("")
    log.ok("冒烟测试全部通过")
    log.info("  已验证：体裁级联、主题信号、百合误判防护、失效识别、待复核、计划生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
