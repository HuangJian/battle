"""test_hub_ledger_incremental.py — 账本 H6 **真增量**（2026-10-17，plan/hub-server-memory-slim L3）。

**治什么**：`_read_ledger` 只有「文件 size 没变 ⇒ 复用缓存」这一半；size 一变就**全文重解析**
（`for line in f` + 每行程 `json.loads`）。一轮训练往账本追加常数行，而派发热路径（worker 每
几秒一轮询）每拍都调它 ⇒ 长跑时每拍 O(账本)。hub 连跑 35h 累计 **74713s CPU**，这一条占了一大半。

修法是补上缺的那一半：记住**已解析到的字节位**（`_ledger_off`），下次只从那里读新增字节。

本文件钉的是**机制**（只读新增），不是"结果对"：

1. 追加一行后，再从盘上读的字节数 == **新增那行的字节数**（不是整个文件）；
2. 结果与全量解析**逐字段相同**（增量不许重解、不许漏解）；
3. **半截行**（写者正在追加）不解析、也不吃掉后面的行；
4. 文件被**截短/重写**（size 变小）⇒ 自动退回全量，结果与新内容一致。
"""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub import store_ledger as sl
from hub.store import _JobStore

KEEP = ("job_pending", "job_completed", "job_cancelled")


class _ReadMeter:
    """替换 `store_ledger.open`，统计**二进制读**的字节数（判据 ① 的唯一量具）。"""

    def __init__(self) -> None:
        self.bytes = 0
        self.opens = 0

    def install(self, monkeypatch: Any) -> _ReadMeter:
        real = builtins.open
        me = self

        def fake(path: Any, *a: Any, **kw: Any) -> Any:
            f = real(path, *a, **kw)
            mode = a[0] if a else kw.get("mode", "r")
            if "b" in str(mode):
                me.opens += 1
                orig = f.read

                def read(n: int = -1) -> Any:
                    b = orig(n)
                    me.bytes += len(b)
                    return b

                f.read = read  # type: ignore[method-assign]
            return f

        monkeypatch.setattr(sl, "open", fake, raising=False)
        return me


def _store(tmp_path: Path) -> _JobStore:
    return _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl")


def _rows(st: _JobStore) -> list[dict]:
    return st._read_ledger()


def _expect(path: Path) -> list[dict]:
    """独立重实现：全量解析一遍（对拍用，不复用被测代码）。"""
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        if e.get("event") in KEEP:
            out.append(e)
    return out


def _append(st: _JobStore, n: int, *, event: str = "job_pending") -> int:
    """追加 n 行，返回新增的字节数。"""
    before = st.jsonl_path.stat().st_size if st.jsonl_path.exists() else 0
    for i in range(n):
        st._append_ledger({"event": event, "job_id": f"j{i:04d}", "ts": float(i)})
    return st.jsonl_path.stat().st_size - before


def test_first_read_parses_the_whole_file(tmp_path: Path) -> None:
    """首读：逐字段等于全量解析（增量实现不许改结果）。"""
    st = _store(tmp_path)
    _append(st, 5)
    assert _rows(st) == _expect(st.jsonl_path)
    assert st._ledger_off == st.jsonl_path.stat().st_size, "整份都解析完了 ⇒ offset 到头"


def test_appending_one_row_reads_only_the_new_bytes(tmp_path: Path, monkeypatch: Any) -> None:
    """★ 判据 ①：追加一行后再读，**只读新增字节**（旧实现读整个文件）。"""
    st = _store(tmp_path)
    _append(st, 20)
    _rows(st)  # 首读（全量）
    added = _append(st, 1)
    meter = _ReadMeter().install(monkeypatch)
    _rows(st)
    assert meter.bytes == added, f"读了 {meter.bytes} 字节，新增只有 {added}"
    # 顺带：结果仍然对（不重解 ⇒ 不会多出一条；不漏解 ⇒ 不会少一条）
    assert _rows(st) == _expect(st.jsonl_path)


def test_incremental_matches_full_parse_field_by_field(tmp_path: Path) -> None:
    """★ 判据 ②：分 5 批追加、每批后读一次，最终结果与全量解析**逐字段**相同。"""
    st = _store(tmp_path)
    for batch in range(5):
        _append(st, 7)
        rows = _rows(st)
        assert len(rows) == (batch + 1) * 7, rows[-1]
    assert _rows(st) == _expect(st.jsonl_path)
    # 非这三种事件的行读了就丢（与旧口径一致：它们不参与折叠）
    st._append_ledger({"event": "job_rejected", "job_id": "zzz", "ts": 1.0})
    assert all(e["event"] in KEEP for e in _rows(st))


def test_a_half_written_line_is_not_parsed_and_does_not_eat_the_next(tmp_path: Path) -> None:
    """★ 判据 ③：末尾半截行（`{"event": "job_pendi`）既不解析、也不吃掉后面的完整行。"""
    st = _store(tmp_path)
    _append(st, 2)
    assert len(_rows(st)) == 2
    with open(st.jsonl_path, "a", encoding="utf-8") as f:
        f.write('{"event": "job_pendi')  # 写者正在追加
    assert len(_rows(st)) == 2, "半截行不该被解析"
    partial = st.jsonl_path.stat().st_size - st._ledger_off
    assert partial > 0, "半截行必须留在 offset 之外（下次接着读）"
    # 补全这一行 ⇒ 立刻能解析到，且不重解前面两行
    with open(st.jsonl_path, "a", encoding="utf-8") as f:
        f.write('ng", "job_id": "half", "ts": 9.0}\n')
    rows = _rows(st)
    assert [e["job_id"] for e in rows] == ["j0000", "j0001", "half"], rows
    assert rows == _expect(st.jsonl_path)


def test_truncated_file_falls_back_to_a_full_reparse(tmp_path: Path) -> None:
    """★ 判据 ④：文件被截短/重写（size < 上次 offset）⇒ 退回全量，结果与新内容一致。

    真机形状：tmp 被清、账本被重建（`tools/tmp-clean.py` 那次事故就清了整个 tmp/）。
    增量实现在这条路上最容易错成「拿旧缓存 + 读一点点」⇒ 结果里混着已不存在的 job。
    """
    st = _store(tmp_path)
    _append(st, 10)
    assert len(_rows(st)) == 10
    st.jsonl_path.write_text("", encoding="utf-8")
    _append(st, 3)
    rows = _rows(st)
    assert len(rows) == 3, f"截短后应只剩新写的 3 行：{rows}"
    assert rows == _expect(st.jsonl_path)
