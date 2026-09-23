"""common/fs.py —— 落盘原语的唯一实现（原子写 / 追加账本 / tar 解包）。

这三件事在本仓都曾各写两份，且**每一份的注释都在解释同一件事**——正是那种
「口径只活在注释里」的重复：

| 原语 | 曾有两份 | 为什么必须唯一 |
|------|---------|---------------|
| 原子写 | `remote/artifacts.atomic_write_bytes` ↔ `remote/hub_server._write_bytes` | 「半截权重比没有权重更危险」「半截的记账文件会把续跑判据带偏」——两处都写了这句，而实现必须是 tmp + `os.replace` 这一种写法才成立 |
| 追加 JSONL | `rl/bc_ledger.append_ledger` ↔ `remote/hub_client._append_ledger` | 账本是**多进程追加**的：父目录创建、`ensure_ascii=False`、单行 JSON + 换行，任一处漏掉就让读侧解析出坏行 |
| tar 解包 | `remote/worker.unpack_opt_tar` ↔ `remote/hub_client._extract_tar` | 都要兼容 Py<3.12（`extractall` 的 `filter=` 参数），漏掉就 `TypeError` |

`extract_tar_bytes` 用 `filter="data"`（Py≥3.12）并回退裸 `extractall`：
**不要**为了「统一」把回退删掉——云端 Colab/Kaggle 的 3.10/3.11 走到那支会直接崩。
"""

from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path
from typing import Any

__all__ = ["append_jsonl", "atomic_write_bytes", "atomic_write_json", "extract_tar_bytes"]


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """tmp + `os.replace`：任何时刻读到的都是完整文件（会话被 kill 也不留半截）。

    `os.replace` 在同一文件系统内是原子的；`tmp` 与目标同目录（同 fs）是前提，
    所以这里用 `with_name` 而不是系统临时目录。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)


def atomic_write_json(path: Path | str, obj: Any, *, indent: int = 1) -> None:
    """`atomic_write_bytes` + JSON 编码（`ensure_ascii=False`：中文可读）。"""
    atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=indent).encode("utf-8"))


def append_jsonl(jsonl_path: str | Path, event: dict) -> None:
    """向账本追加一行 JSON（父目录按需创建）。

    账本文件随时可能被**另一个进程**追加/读取 ⇒ 只做「打开-追加-关闭」，
    绝不读改写；半写行由读侧负责跳过（见 `rl/bc_ledger.read_events`）。
    """
    p = Path(jsonl_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def extract_tar_bytes(tar_bytes: bytes, dest: Path | str) -> None:
    """内存里的 tar 字节 → `dest` 目录；兼容 Python 3.10（无 `filter=` 参数）。"""
    d = Path(dest)
    d.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        try:
            tf.extractall(d, filter="data")
        except TypeError:  # Python < 3.12
            tf.extractall(d)
