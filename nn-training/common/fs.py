"""common/fs.py —— 落盘原语的唯一实现（原子写 / 追加账本 / tar 解包）。

这三件事在本仓都曾各写两份，且**每一份的注释都在解释同一件事**——正是那种
「口径只活在注释里」的重复：

| 原语 | 曾有两份 | 为什么必须唯一 |
|------|---------|---------------|
| 原子写 | `remote/artifacts.atomic_write_bytes` ↔ `hub.server._write_bytes` | 「半截权重比没有权重更危险」「半截的记账文件会把续跑判据带偏」——两处都写了这句，而实现必须是 tmp + `os.replace` 这一种写法才成立 |
| 追加 JSONL | `biz/bc_ledger.append_ledger` ↔ `remote/hub_client._append_ledger` | 账本会被多线程并发追加（重叠的评估轮）：父目录创建、`ensure_ascii=False`、单行 JSON + 换行、**每路径一个写者**，任一处漏掉就让读侧解析出坏行 |
| tar 解包 | `remote/worker.unpack_opt_tar` ↔ `remote/hub_client._extract_tar` | 都要兼容 Py<3.12（`extractall` 的 `filter=` 参数），漏掉就 `TypeError` |

`extract_tar_bytes` 用 `filter="data"`（Py≥3.12）并回退裸 `extractall`：
**不要**为了「统一」把回退删掉——云端 Colab/Kaggle 的 3.10/3.11 走到那支会直接崩。
"""

from __future__ import annotations

import io
import json
import os
import queue
import tarfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any

__all__ = [
    "append_jsonl",
    "atomic_write_bytes",
    "atomic_write_json",
    "extract_tar_bytes",
    "same_bytes",
]

#: `os.replace` 撞「目标被别的句柄占着」（Windows）时的**有界重试**参数；见 `atomic_write_bytes`。
ATOMIC_REPLACE_ATTEMPTS = 6
ATOMIC_REPLACE_BACKOFF_SEC = 0.02

#: 流式比较 / 哈希的分块（字节）。
#:
#: 2026-10-17（plan/hub-server-memory-slim L2）：`path.read_bytes()` 这种「整份入内存只为比
#: 一下 / 算个哈希」是 hub 6.2GB RSS 的次要峰值源（权重文件几十 MB，补传时每次一发）。
#: 分块之后峰值 = 一块，与文件大小无关。
STREAM_CHUNK = 64 * 1024


def atomic_write_bytes(path: Path | str, data: bytes) -> None:
    """tmp + `os.replace`：任何时刻读到的都是完整文件（会话被 kill 也不留半截）。

    `os.replace` 在同一文件系统内是原子的；`tmp` 与目标同目录（同 fs）是前提，
    所以这里用 `with_name` 而不是系统临时目录。

    ★ **Windows：撞读者句柄时要有界重试（2026-10-14 满载 flake）**——`os.replace`
    （`MoveFileEx` 的 REPLACE_EXISTING）要求目标**没有别的打开句柄**，而 Python 的 `open()`
    不共享删除权 ⇒ **正在读**这个文件的一方会让它抛 `PermissionError(13)`。本仓是常态：
    补传账本 `delivered.json` 由子进程写、父侧/测试**轮询读**（状态面同理）。读侧只持有
    几微秒，但满载下窗口会被撞上（实测：写侧 `_save_ledger` 因此失败 ⇒ `deliver_result`
    把异常吞成「没送达」⇒ `result_done` 永远落不了盘）。退避重试几次即可，读侧一松手就成。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    for attempt in range(ATOMIC_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:  # Windows：目标被别的句柄占着（读侧还没松手）
            if attempt == ATOMIC_REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(ATOMIC_REPLACE_BACKOFF_SEC * (attempt + 1))


def atomic_write_json(path: Path | str, obj: Any, *, indent: int = 1) -> None:
    """`atomic_write_bytes` + JSON 编码（`ensure_ascii=False`：中文可读）。"""
    atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=indent).encode("utf-8"))


#: 写者空闲多久自行退场（写者不持有句柄，退场只回收线程；下次 append 会重建）。
#: 每条 tmp 路径都会建一个写者——没有空闲退场就是一条线程泄漏（测试面尤其明显）。
_JSONL_WRITER_IDLE_EXIT_SEC = 30.0


class _JsonlWriter:
    """单条路径的追加执行者：队列收**已编码的整行**，逐行 open/append/close 并回 ack。

    行在调用方线程里先 `json.dumps`（序列化语义与旧实现逐字节一致，也避免入队后
    对象被改写的竞态）；写者只做 IO，因此「同一路径任何时刻至多一个追加句柄」恒成立。
    """

    def __init__(self, key: str, path: str) -> None:
        self.key = key
        self.path = path
        self.closed = False
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._thread = threading.Thread(
            target=self._run, name=f"jsonl-writer:{Path(path).name}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                item = self._q.get(timeout=_JSONL_WRITER_IDLE_EXIT_SEC)
            except queue.Empty:
                # 退场与入队都在 `_JSONL_WRITERS_LOCK` 下判定 ⇒ 不会漏掉「刚好在这时
                # 投递」的行（投递方看到 closed 就新建写者，队列先进先出不丢序）。
                with _JSONL_WRITERS_LOCK:
                    if not self._q.empty():
                        continue
                    if _JSONL_WRITERS.get(self.key) is self:
                        _JSONL_WRITERS.pop(self.key, None)
                    self.closed = True
                return
            text, fut = item
            try:
                p = Path(self.path)
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(text)
            except Exception as e:  # 写失败回给调用方（`_record_with_retry` 靠它重试）
                fut.set_exception(e)
            else:
                fut.set_result(None)


#: 按**规范化路径**索引的单写者（见 `append_jsonl`）。
_JSONL_WRITERS: dict[str, _JsonlWriter] = {}
_JSONL_WRITERS_LOCK = threading.Lock()


def append_jsonl(jsonl_path: str | Path, event: dict) -> None:
    """向账本追加一行 JSON（父目录按需创建）；**返回即已落盘**（写者 ack 后才返回）。

    为什么不是「就地 open」：两轮评估可以重叠（`trainer/loop_eval._sweep_eval_tail`
    刻意不 join it55 的尾巴，it60 已开评），而各轮的 `jsonl_lock` 只管自己那一轮。
    Windows 上两个追加句柄**不是**原子的 O_APPEND：后写者按自己句柄的偏移落笔，
    覆盖前写者刚写的行首——2026-11-02 z4-polish.it60 因此留下 119 字节断头行，
    summary 重扫少一局（`_record_agent_meta` 的 meta 行同遭）。锁住「轮内」挡不住
    「轮与轮」，所以写入口收敛到**每路径一个写者线程**：调用方只投递 + 等 ack。

    只串行化**同进程**的追加（本仓的 eval/rollout 都住在 trainer 进程内）；跨进程同时
    写同一账本不在本原语的保证内（各课程各有自己的 eval_log）。

    账本文件随时可能被**另一个进程**读取 ⇒ 只做「打开-追加-关闭」，绝不读改写；
    半写行由读侧负责跳过（见 `biz/bc_ledger.read_events`）。
    """
    p = Path(jsonl_path)
    text = json.dumps(event, ensure_ascii=False) + "\n"
    # 绝对路径入键：相对路径随 CWD 变，key 漂移会造出第二个写者写同一文件。
    abs_path = os.path.abspath(str(p))
    key = os.path.normcase(abs_path)
    with _JSONL_WRITERS_LOCK:
        writer = _JSONL_WRITERS.get(key)
        if writer is None or writer.closed:
            writer = _JsonlWriter(key, abs_path)
            _JSONL_WRITERS[key] = writer
        fut: Future = Future()
        writer._q.put((text, fut))
    fut.result()


def extract_tar_bytes(tar_bytes: bytes, dest: Path | str) -> None:
    """内存里的 tar 字节 → `dest` 目录；兼容 Python 3.10（无 `filter=` 参数）。"""
    d = Path(dest)
    d.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        try:
            tf.extractall(d, filter="data")
        except TypeError:  # Python < 3.12
            tf.extractall(d)


def same_bytes(path: Path | str, data: bytes) -> bool:
    """文件与字节是否逐字节相同；**先比 size 再分块比**（峰值 = 一块，不是整份）。

    2026-10-17（plan/hub-server-memory-slim L2）：替掉 `path.read_bytes() == blob` 那一族。
    幂等重传是常态（补传权重每次都走这道判据），「先比 size」那一半照旧保留——它让
    绝大多数命中**一个字节都不读**。文件不可读（被并发删掉）⇒ `False`（与旧实现同：
    旧实现 `OSError` 也吞掉）。
    """
    p = Path(path)
    try:
        if p.stat().st_size != len(data):
            return False
    except OSError:
        return False
    view = memoryview(data)
    try:
        with open(p, "rb", buffering=0) as f:
            off = 0
            while off < len(view):
                chunk = f.read(min(STREAM_CHUNK, len(view) - off))
                if not chunk:
                    return False  # 文件在两次 stat 之间被截短
                if view[off : off + len(chunk)] != chunk:
                    return False
                off += len(chunk)
    except OSError:
        return False
    return True
