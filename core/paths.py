"""路径约定。

与 steam-collection-skill 保持一致：每一步的产出放独立子目录，
便于检查与回溯；`step2/` 是分类总表所在，也是增量模式的唯一基准。
"""

from __future__ import annotations

import os
from pathlib import Path

# core/paths.py -> core/ -> 技能根目录
ROOT = Path(__file__).resolve().parent.parent

STEP1 = ROOT / "step1"   # 抓取：收藏夹与条目列表
STEP2 = ROOT / "step2"   # 分类体系 + 预分类总表（唯一基准）
STEP3 = ROOT / "step3"   # AI 复核
STEP4 = ROOT / "step4"   # 写入计划与回滚
STEP5 = ROOT / "step5"   # 增量模式
DATA = ROOT / "data"     # 缓存（分区映射表、富化结果等）
OUT = ROOT / "out"       # 人类可读报告

SCRIPTS = ROOT / "scripts"
CONFIG_PATH = SCRIPTS / "local_config.json"
CONFIG_TEMPLATE = SCRIPTS / "local_config.template.json"
BROWSER_PROFILE = SCRIPTS / ".browser_profile"

ALL_DIRS = (STEP1, STEP2, STEP3, STEP4, STEP5, DATA, OUT)


def ensure_dirs() -> None:
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def rel(path) -> str:
    """相对技能根目录的展示路径，用于日志。"""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def env(*names: str) -> str | None:
    """按顺序返回第一个非空环境变量。"""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None
