"""Shared atomic JSON-file replacement for cache and history snapshots."""

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, raw: bytes) -> None:
    """Replace only the owned file; preserve its previous content on failure."""
    # 在目标目录先写完整临时文件再 replace，避免中途失败留下半份 JSON。
    # 这不提供跨进程写协调；缓存和历史仍各由一个插件实例拥有文件。
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
