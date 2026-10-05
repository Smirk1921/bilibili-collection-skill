"""安全机制：日志、原子写、时间戳备份、限速、回滚日志、dry-run 守卫。

这些是平台无关的——换成任何数据源，写入保护逻辑都不用改。
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from pathlib import Path

# ----------------------------------------------------------------------
# 日志
# ----------------------------------------------------------------------
class Log:
    """极简日志，Windows 下强制 UTF-8，避免中文乱码。"""

    _lock = threading.Lock()

    def __init__(self) -> None:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    def _emit(self, tag: str, msg: str) -> None:
        with self._lock:
            print(f"[{time.strftime('%H:%M:%S')}] {tag} {msg}", flush=True)

    def info(self, msg: str) -> None:
        self._emit("   ", msg)

    def ok(self, msg: str) -> None:
        self._emit(" OK", msg)

    def warn(self, msg: str) -> None:
        self._emit("WARN", msg)

    def err(self, msg: str) -> None:
        self._emit("FAIL", msg)

    def step(self, msg: str) -> None:
        self._emit("==>", msg)


log = Log()


# ----------------------------------------------------------------------
# JSON / JSONL（原子写）
# ----------------------------------------------------------------------
def read_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.warn(f"读取 {p.name} 失败（{exc}），按空处理")
        return default


def write_json(path, obj) -> None:
    """原子写：先写 .tmp 再 replace，崩溃不会留下半截文件。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    tmp.replace(p)


def append_jsonl(path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


# ----------------------------------------------------------------------
# CSV（人类可复核的中间产物，统一 UTF-8-BOM，Excel 双击不乱码）
# ----------------------------------------------------------------------
def read_csv(path) -> list[dict]:
    import csv

    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows: list[dict], columns: list[str]) -> None:
    import csv

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in columns})
    tmp.replace(p)


# ----------------------------------------------------------------------
# 备份
# ----------------------------------------------------------------------
def backup(path, backup_dir=None) -> Path | None:
    """把现有文件复制一份带时间戳的备份，返回备份路径。"""
    p = Path(path)
    if not p.exists():
        return None
    d = Path(backup_dir) if backup_dir else p.parent / "backup"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"{p.stem}_{time.strftime('%Y%m%d_%H%M%S')}{p.suffix}"
    shutil.copy2(p, dst)
    return dst


# ----------------------------------------------------------------------
# 限速
# ----------------------------------------------------------------------
class RateLimiter:
    """全局最小间隔限速器，线程安全。写操作靠它避免触发风控。"""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = max(0.0, min_interval)
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self.min_interval
        if sleep_for > 0:
            time.sleep(sleep_for)


# ----------------------------------------------------------------------
# dry-run 守卫
# ----------------------------------------------------------------------
def guard_write(confirm: bool, what: str, preview=None) -> bool:
    """写操作统一守卫。未确认则打印预览并返回 False，调用方直接 return。"""
    if confirm:
        return True
    log.warn(f"当前为 DRY-RUN，不会{what}。")
    if preview is not None:
        preview()
    log.info("确认无误后加 --confirm 重新执行。")
    return False
