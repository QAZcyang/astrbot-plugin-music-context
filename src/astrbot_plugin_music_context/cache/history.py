"""Non-expiring observation snapshots in the existing plugin data directory."""

import json
from pathlib import Path

from astrbot.api import logger

from .files import atomic_write

class HistoryStore:
    """One file keeps Context/reply associations atomic; one plugin instance owns it."""

    # 历史用于 WebUI 展示与追踪，不能由歌曲缓存还原；无 TTL 的独立快照让重启后
    # 仍保留请求/回复关联。记录投影与数量限制归 ManagementState，不归音乐缓存。
    def __init__(self, directory: str | Path):
        self.path = Path(directory) / "history.json"
        self.logger = logger
        self.available = False
        self.read_failed = False

    def load(self) -> dict:
        empty = {"version": 1, "sequence": 0, "contexts": [], "replies": []}
        try:
            with self.path.open("rb") as stream:
                # Count limits live in ManagementState. A byte cap would reject
                # complete long replies and discard valid snapshots on restart.
                raw = stream.read()
            data = json.loads(raw)
            if (
                not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1
                or type(data.get("sequence")) is not int or not 0 <= data["sequence"] < 10**12
                or not isinstance(data.get("contexts"), list)
                or not isinstance(data.get("replies"), list)
            ):
                raise ValueError("Invalid history envelope")
            self.available = True
            return data
        except FileNotFoundError:
            self.save(empty)
        except (OSError, ValueError, RecursionError) as exc:
            self.log("read", exc)
            # An unreadable file may become available again; do not overwrite it.
            # 格式损坏可恢复为空；OS 读取失败可能只是暂时不可访问，暂停写入避免覆盖未读历史。
            if not isinstance(exc, OSError):
                self.save(empty)
            else:
                self.read_failed = True
        return empty

    def save(self, data: dict) -> bool:
        try:
            if self.read_failed:
                raise OSError("History was not safely loaded; retry on restart")
            raw = json.dumps(
                data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
            atomic_write(self.path, raw)
            self.available = True
            return True
        except (OSError, ValueError, RecursionError) as exc:
            self.log("write", exc)
            self.available = False
            return False

    def log(self, operation: str, exc: Exception) -> None:
        self.logger.warning("Music history %s failed (%s)", operation, type(exc).__name__)
