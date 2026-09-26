"""course_compare —— 两条腿按**绝对 it 点**对齐的对照表（plan/course-archive.plan.md §3.5）。

用途：把「与 x20-noexplore 同 it 点对照」这类评审里手工拼的表变成随时可出的东西。

输入**既可以是活体目录也可以是封存目录**——只读 `eval_log.jsonl` / `training_log.jsonl`
（或它们的 `.gz` / `.xz`，按 magic 判别，照 `remote/protocol.py` 先例），不扫盘、不递归。

**缺键 ≠ 0**（`reports/x20-dodge.review.md` P0 的教训）：某列在任一侧**一行都没有**时显示
`未知`，而不是把它算成 0 拉低分母。合法的 0 值（零击杀的局）照常计入。

命令：

    python tools/course_compare.py --a tmp/x20-dodge-l1 --b archive/courses/x20-noexplore \\
        [--iters 0,5,10] [--format md|csv] [--out tmp/cmp.md]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import lzma
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
UNKNOWN = "未知"

#: 逐局评估账本的候选文件名（活体是 `.jsonl`；封存是压缩件）
_EVAL_NAMES = ("eval_log.jsonl", "eval_log.jsonl.gz", "eval_log.jsonl.xz")
_TRAIN_NAMES = ("training_log.jsonl", "training_log.jsonl.gz", "training_log.jsonl.xz")

#: 报表指标：(键, 标题, 取值函数)。取不到 ⇒ `未知`。
#: 「机制读数」四列来自 metrics v8 / Phase 0（旧报告缺键是常态，所以必须能显示未知）。
METRICS: list[tuple[str, str, Callable[[list[dict]], float | None]]] = [
    ("n", "局数", lambda rs: float(len(rs)) or None),
    ("mean", "kills 均值", lambda rs: _mean(rs, "kills")),
    ("p10", "kills p10", lambda rs: _pct(rs, "kills", 10)),
    (
        "low_pct",
        "低杀率 %(kills≤3)",
        lambda rs: _share(rs, "kills", lambda r: (_num(r, "kills") or 0) <= 3),
    ),
    ("zero_kill_pct", "零击杀 %", lambda rs: _share(rs, "kills", lambda r: _num(r, "kills") == 0)),
    ("win_pct", "胜率 %", lambda rs: _share(rs, "win", lambda r: _num(r, "win") == 1)),
    (
        "cleared_pct",
        "全歼率 %",
        lambda rs: _share(rs, "cleared", lambda r: _num(r, "cleared") == 1),
    ),
    (
        "timeout_pct",
        "超时 %",
        lambda rs: _share(rs, "outcome", lambda r: r.get("outcome") == "timeout"),
    ),
    ("ticks_mean", "ticks 均值", lambda rs: _mean(rs, "ticks")),
    ("dmg_mean", "承伤均值", lambda rs: _mean(rs, "playerDamageTaken")),
    ("hp_ratio_mean", "残血比均值(v8)", lambda rs: _mean(rs, "playerHpRatio")),
    ("danger_mean", "危险 tick 均值(v8)", lambda rs: _mean(rs, "dangerTicks")),
    ("stuck_mean", "卡住 tick 均值", lambda rs: _mean(rs, "stuckTicks")),
]


def _num(row: dict, key: str) -> float | None:
    """取数值；**缺键 / 非数值 / null 一律 None**（不是 0）。"""
    v = row.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _mean(rows: list[dict], key: str) -> float | None:
    vals = [v for v in (_num(r, key) for r in rows) if v is not None]
    return sum(vals) / len(vals) if vals else None


def _pct(rows: list[dict], key: str, p: float) -> float | None:
    """最近秩分位数（无插值）：小样本下比线性插值更稳，且整数值可直接读。"""
    vals = sorted(v for v in (_num(r, key) for r in rows) if v is not None)
    if not vals:
        return None
    idx = max(0, min(len(vals) - 1, round((p / 100) * (len(vals) - 1))))
    return vals[idx]


def _present(row: dict, key: str) -> bool:
    """该行有没有这一列的值（`None`/缺键都不算——故 `kills` 与 `outcome` 同一套判据）。"""
    v = row.get(key)
    return v is not None and isinstance(v, (int, float, str)) and not isinstance(v, bool)


def _share(rows: list[dict], key: str, pred: Callable[[dict], bool]) -> float | None:
    """占比 %；**分母只算该列有值的行**（缺键行既不进分子也不进分母）。

    分母按键分别算——拿 `kills` 当所有列的分母会把「有 win 没 kills」的旧报告行静默算进
    分母（那正是「缺键 ≠ 0」要防的事）。
    """
    known = [r for r in rows if _present(r, key)]
    if not known:
        return None
    return 100.0 * sum(1 for r in known if pred(r)) / len(known)


# ────────────────────────── 读面（gz/xz 透明） ──────────────────────────


def _read_maybe_compressed(path: Path) -> str:
    """按 magic 解压（gzip / xz / 裸文本）。"""
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw).decode("utf-8", "replace")
    if raw[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(raw).decode("utf-8", "replace")
    return raw.decode("utf-8", "replace")


def resolve_log(dirpath: Path, names: Iterable[str]) -> Path | None:
    """在目录里找日志（活体名优先，封存压缩名兜底）。"""
    for name in names:
        p = dirpath / name
        if p.is_file():
            return p
    return None


def read_jsonl(path: Path) -> list[dict]:
    """读 JSONL（坏行跳过——一个坏行不该让整表空掉）。"""
    out: list[dict] = []
    for line in _read_maybe_compressed(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out


def load_eval_rows(dirpath: Path) -> list[dict]:
    p = resolve_log(dirpath, _EVAL_NAMES)
    if p is None:
        raise FileNotFoundError(f"{dirpath} 下找不到逐局评估账本（试过 {', '.join(_EVAL_NAMES)}）")
    return [r for r in read_jsonl(p) if r.get("event") == "eval"]


def load_train_rows(dirpath: Path) -> list[dict]:
    p = resolve_log(dirpath, _TRAIN_NAMES)
    return read_jsonl(p) if p else []


# ────────────────────────── 对齐与汇总 ──────────────────────────


def group_by_iter(rows: Iterable[dict]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for r in rows:
        it = r.get("iter")
        if isinstance(it, bool) or not isinstance(it, int):
            continue
        out.setdefault(it, []).append(r)
    return out


def summarize(rows: list[dict]) -> dict[str, float | None]:
    return {key: fn(rows) for key, _title, fn in METRICS}


def _fmt(v: float | None) -> str:
    if v is None:
        return UNKNOWN
    return f"{v:.2f}" if abs(v) < 1000 else f"{v:.0f}"


def _delta(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return UNKNOWN
    return f"{b - a:+.2f}"


def compare_iters(a_dir: Path, b_dir: Path, iters: Sequence[int] | None = None) -> dict[str, Any]:
    """两条腿按 it 对齐的汇总。`iters=None` ⇒ 取两侧**都有数据**的 it 交集。"""
    a = group_by_iter(load_eval_rows(a_dir))
    b = group_by_iter(load_eval_rows(b_dir))
    picked = sorted(a.keys() & b.keys()) if iters is None else list(iters)
    rows: list[dict[str, str]] = []
    for it in picked:
        sa, sb = summarize(a.get(it, [])), summarize(b.get(it, []))
        for key, title, _fn in METRICS:
            rows.append(
                {
                    "it": str(it),
                    "metric": title,
                    "key": key,
                    "a": _fmt(sa[key]),
                    "b": _fmt(sb[key]),
                    "delta": _delta(sa[key], sb[key]),
                }
            )
    return {"a": str(a_dir), "b": str(b_dir), "iters": picked, "rows": rows}


def render_markdown(result: dict[str, Any]) -> str:
    """md 表。**路径只出现在表头** —— 这是「活体 vs 活体」与「活体 vs 封存」同输出的前提。"""
    out = io.StringIO()
    out.write("# 课程对照（按绝对 it 点对齐）\n\n")
    out.write(f"- A：`{result['a']}`\n- B：`{result['b']}`\n- it 点：{result['iters']}\n\n")
    out.write("| it | 指标 | A | B | Δ |\n|---:|---|---:|---:|---:|\n")
    for r in result["rows"]:
        out.write(f"| {r['it']} | {r['metric']} | {r['a']} | {r['b']} | {r['delta']} |\n")
    return out.getvalue()


def render_csv(result: dict[str, Any]) -> str:
    """CSV。同样**不含路径列**（只在注释行给出）。"""
    out = io.StringIO()
    out.write(f"# A={result['a']}\n# B={result['b']}\n")
    w = csv.writer(out)
    w.writerow(["it", "key", "metric", "a", "b", "delta"])
    for r in result["rows"]:
        w.writerow([r["it"], r["key"], r["metric"], r["a"], r["b"], r["delta"]])
    return out.getvalue()


def _parse_iters(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    return [int(x) for x in raw.replace(" ", "").split(",") if x]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python tools/course_compare.py",
        description="两条腿按绝对 it 点对齐的对照表（活体或封存目录皆可）",
    )
    ap.add_argument(
        "--a", required=True, help="A 腿目录（活体 tmp/<课> 或封存 archive/courses/<课>）"
    )
    ap.add_argument("--b", required=True, help="B 腿目录")
    ap.add_argument("--iters", default=None, help="逗号分隔的 it 列表；缺省 = 两侧共有的 it 交集")
    ap.add_argument("--format", default="md", choices=["md", "csv"])
    ap.add_argument("--out", default=None, help="输出文件；缺省打到 stdout")
    args = ap.parse_args(argv)

    try:
        result = compare_iters(Path(args.a), Path(args.b), _parse_iters(args.iters))
    except FileNotFoundError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2
    text = render_markdown(result) if args.format == "md" else render_csv(result)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写 {args.out}（{len(result['iters'])} 个 it 点）")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
