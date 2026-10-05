# -*- coding: utf-8 -*-
"""脚本公共部分：路径、参数解析、适配器构造。

约定（与 steam-collection-skill 一致）：
- 每个脚本都能独立运行，`python scripts/xxx.py --help` 看用法
- 参数用 sys.argv 手写解析，不引 argparse，保持脚本轻量可读
- 所有写操作都支持 --dry-run / --confirm
"""

from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent          # scripts/
ROOT = BASE.parent                              # 技能根目录
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import config, store  # noqa: E402
from core.safety import log  # noqa: E402

RULES_PATH = ROOT / "core" / "adapters" / "bilibili_genre_rules.json"


def has_flag(*flags: str) -> bool:
    return any(f in sys.argv[1:] for f in flags)


def arg_val(flag: str, default=None):
    """取 `--flag value` 形式的值；未提供返回 default。"""
    args = sys.argv[1:]
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            return args[i + 1]
    return default


def positional() -> list[str]:
    """取所有非选项参数（跳过 --flag 及其值）。"""
    out, args, skip = [], sys.argv[1:], False
    for a in args:
        if skip:
            skip = False
            continue
        if a.startswith("--"):
            skip = True
            continue
        out.append(a)
    return out


def usage(text: str, code: int = 0) -> None:
    print(text.strip())
    sys.exit(code)


def load_cfg() -> dict:
    return config.load()


def make_adapter(cfg: dict, need_login: bool = True):
    """构造平台适配器。目前只有 B站；换平台在这里改一行即可。"""
    from core.adapters.bilibili import BilibiliAdapter

    return BilibiliAdapter(cfg, need_login=need_login)


def load_rules() -> dict:
    rules = store.load_rules(RULES_PATH)
    from core.rules import lint_rules, validate_rules

    problems = validate_rules(rules)
    if problems:
        log.err("规则表有问题：")
        for p in problems:
            log.info(f"  - {p}")
        raise SystemExit("请先修正 " + str(RULES_PATH))

    for w in lint_rules(rules):
        log.warn(f"规则表提醒 —— {w}")
    return rules


def require_step1() -> tuple[dict, list]:
    collections = store.load_collections()
    items = store.load_items()
    if not collections or not items:
        raise SystemExit(
            "缺少抓取结果，请先运行： python scripts/fetch_library.py"
        )
    return collections, items
