"""配置加载：scripts/local_config.json + 环境变量覆盖（环境变量优先）。

所有字段都可以留空——对应功能降级而不是报错。
"""

from __future__ import annotations

from .paths import CONFIG_PATH, CONFIG_TEMPLATE, env
from .safety import log, read_json

DEFAULTS: dict = {
    "bilibili": {
        "sessdata": "",
        "bili_jct": "",
        "dedeuserid": "",
    },
    "rate_limit": {
        "read_min_interval": 0.35,
        "write_min_interval": 2.0,
        "read_workers": 3,
        "max_retries": 4,
        "backoff_base": 1.8,
    },
    "classify": {
        "high_confidence": 0.62,
        "margin": 0.08,
        "reorg_margin": 0.10,
        "secondary_confidence": 0.45,
        "ai_min_confidence": 0.5,
        "min_collection_size": 3,
        "weights": {"tag": 1.00, "title": 0.35, "creator": 0.45, "category": 0.15},
    },
    "execute": {
        "max_targets_per_item": 3,
        "empty_inbox": True,
        "skip_collections": [],
        "keep_created_collections": True,
    },
}

# 环境变量 -> (配置段, 键)，环境变量优先于文件
ENV_MAP = {
    "BILI_SESSDATA": ("bilibili", "sessdata"),
    "BILI_JCT": ("bilibili", "bili_jct"),
    "BILI_DEDEUSERID": ("bilibili", "dedeuserid"),
    "BILI_READ_INTERVAL": ("rate_limit", "read_min_interval"),
    "BILI_WRITE_INTERVAL": ("rate_limit", "write_min_interval"),
    "BILI_READ_WORKERS": ("rate_limit", "read_workers"),
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def load() -> dict:
    """读配置；文件缺失时回落到模板并提示，而不是直接崩。"""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}

    if CONFIG_PATH.exists():
        cfg = _deep_merge(cfg, read_json(CONFIG_PATH, {}) or {})
    elif CONFIG_TEMPLATE.exists():
        log.warn(f"未找到 {CONFIG_PATH.name}，暂用模板默认值。")
        log.info(f"  请复制 {CONFIG_TEMPLATE.name} 为 {CONFIG_PATH.name} 后填写凭据。")

    for var, (section, key) in ENV_MAP.items():
        val = env(var)
        if val:
            target = cfg.setdefault(section, {})
            default = DEFAULTS.get(section, {}).get(key)
            if isinstance(default, float):
                try:
                    val = float(val)
                except ValueError:
                    continue
            elif isinstance(default, int):
                try:
                    val = int(val)
                except ValueError:
                    continue
            target[key] = val

    return cfg


def save(cfg: dict) -> None:
    from .safety import write_json

    write_json(CONFIG_PATH, cfg)
