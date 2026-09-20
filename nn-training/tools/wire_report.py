"""tools/wire_report.py —— 把传输账（`wire:` 行）聚成 **p50/p90** 与坏签比例。

为什么需要（plan/minimize-payload.plan.md §4.0 / §7.2）：验收口径是「**报 p50/p90，不报均值**；
每组 ≥10 job」，而 `wire:` 是**逐 job 一行**原文——不聚合就只能靠人眼扫日志，而
「同机同 hub 的 44× 双峰」（22.7 KB/s ↔ 990 KB/s）恰恰是均值最容易骗过人的分布。
§8 开放问题 4（`WIRE_MIN_RATE` / `WIRE_REROLL_BUDGET` / `WIRE_REROLL_MAX` 的取值用实机
`wire:` 数据校准）也靠这张表。

合成三类行（一个 seg 名可能来自不同「源」，用 `源:seg` 区分）：

  * worker 每 job 摘要 —— `job <jid>: wire payload=3.32MB/321.8s(10KB/s) code=cache-hit …`
  * 引导期每段摘要 —— `wire: code.zip 1.37MB/106.0s(13KB/s) attempts=1 rerolls=0`
  * hub 发送完成行 —— `[hub-server] 响应发送完成 /jobs/x/payload 3486200 bytes in 321.8s`
    （与 worker 侧同段的秒数对账，即可**本地化慢腿**：hub→cloudflared 还是 cloudflared→worker）

坏签比例 = `wire: re-roll` 行数 ÷ 该段传输次数（改前恒为 0——那时根本没有重抽）。

用法（只读，不碰网络）：

    python tools/wire_report.py tmp/*.log
    python tools/wire_report.py <日志目录>            # 目录下所有 *.log
    python tools/wire_report.py tmp/x.log --json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: 段名（`payload=` / `code.zip ` / `合计=` …）：字母开头，允许 `_ : . - @`（如 `blob:opt`、`task-9.zip`）。
SEG_RE = re.compile(r"([A-Za-z][\w:.@-]*)=([\d.]+)MB/([\d.]+)s\((\d+)KB/s\)")
#: 零字节命中（`code=cache-hit`）；不计入速率样本，但要在表里看得见。
HIT_RE = re.compile(r"([A-Za-z][\w:.@-]*)=([\w-]+)-hit")
#: worker 每 job 摘要行（`job <jid>: wire …`）。
JOB_RE = re.compile(r"job (\S+): wire (.*)$")
#: 引导期摘要行（`wire: code.zip 1.37MB/106.0s(13KB/s) attempts=2 rerolls=1`）。
#: ⚠ 行尾的 `rerolls=` **不**参与统计（重抽一律数 `wire: re-roll` 行：那里才有速率/阈值/
#: 作废字节），否则摘要里的计数与逐次行会算两次。
BOOT_RE = re.compile(
    r"wire: (\S+) ([\d.]+)MB/([\d.]+)s\((\d+)KB/s\) attempts=\d+ rerolls=\d+"
)
#: 重抽行（worker 与引导侧同一形状）：`wire: re-roll #1/3 payload: 实测 6 KB/s < 阈值 80 KB/s…`
REROLL_RE = re.compile(
    r"wire: re-roll #(\d+)/(\d+) (\S+): 实测 (\d+) KB/s < 阈值 (\d+) KB/s"
    r".*已收 (\d+) bytes 作废"
)
#: hub 侧发送完成行：`响应发送完成 /jobs/x/payload 3486200 bytes in 321.8s`
HUB_RE = re.compile(r"响应发送完成 (\S+) (\d+) bytes in ([\d.]+)s")
#: worker 摘要尾部那个「合计」（是各段之和，不是一段）——不进分段表。
TOTAL_SEG = "合计"
#: 判据阈值（与 `remote/worker.py::WIRE_MIN_RATE` 同值）：低于它算一次**坏签**。
BAD_DRAW_KBPS = 80.0


@dataclass
class SegStats:
    """一个 `源:段` 的样本：每 job 一条 `(bytes, sec)`（命中与重抽另计）。"""

    n: int = 0
    bytes_total: int = 0
    secs: list[float] = field(default_factory=list)
    rates: list[float] = field(default_factory=list)
    rerolls: int = 0
    wasted_bytes: int = 0
    hits: int = 0

    def add(self, nbytes: int, sec: float) -> None:
        self.n += 1
        self.bytes_total += int(nbytes)
        self.secs.append(float(sec))
        if sec > 0:
            self.rates.append(nbytes / sec / 1024.0)


def percentile(values: list[float], q: float) -> float:
    """最近秩（nearest-rank）分位数：样本少时也不撒谎（不插值、不外推）。

    `q=0.5` → 中位数；`q=0.9` → p90。空样本返回 `nan`（调用方据此显示 `—`）。
    """
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[idx]


def parse_lines(lines: list[str]) -> dict[str, SegStats]:
    """解析日志行 → `{源:段: SegStats}`。纯函数（无 IO），便于单测直接钉。"""
    out: dict[str, SegStats] = {}

    def bucket(key: str) -> SegStats:
        return out.setdefault(key, SegStats())

    for line in lines:
        m = REROLL_RE.search(line)
        if m:
            seg = m.group(3)
            st = bucket(f"reroll:{seg}")
            st.rerolls += 1
            st.wasted_bytes += int(m.group(6))
            continue
        m = HUB_RE.search(line)
        if m:
            bucket(f"hub:{m.group(1)}").add(int(m.group(2)), float(m.group(3)))
            continue
        m = BOOT_RE.search(line)
        if m:
            bucket(f"boot:{m.group(1)}").add(int(float(m.group(2)) * 1048576), float(m.group(3)))
            continue
        m = JOB_RE.search(line)
        if not m:
            continue
        for name, mb, sec, _kbps in SEG_RE.findall(m.group(2)):
            if name == TOTAL_SEG:
                continue
            bucket(f"job:{name}").add(int(float(mb) * 1048576), float(sec))
        for name, _why in HIT_RE.findall(m.group(2)):
            # 零字节命中（code/ts_code/blob 内容寻址缓存、push 的 preloaded）——同样是「本段
            # 没走网络」，与速率样本分开计（它们没有秒数，混进去会把 p50 拉成 0）。
            bucket(f"job:{name}").hits += 1
    return out


def _merge_rerolls(stats: dict[str, SegStats]) -> None:
    """把 `reroll:<seg>` 并回对应的 `job:<seg>` / `boot:<seg>`（段名是同一套）。

    重抽只认逐次行（`wire: re-roll #n/m <seg>: …已收 N bytes 作废`）——摘要行里的
    `reroll=N` 是同一事实的另一种写法，两边都算就是重复计数。
    """
    for key in [k for k in stats if k.startswith("reroll:")]:
        seg = key.split(":", 1)[1]
        for prefix in ("job", "boot"):
            target = stats.get(f"{prefix}:{seg}")
            if target is not None:
                target.rerolls += stats[key].rerolls
                target.wasted_bytes += stats[key].wasted_bytes


def summarize(stats: dict[str, SegStats]) -> list[dict]:
    """每个 `源:段` 一行读数（p50/p90 秒与速率、坏签比例、命中数）。"""
    rows: list[dict] = []
    for key, st in sorted(stats.items()):
        # `reroll:<seg>` 已并进所属段；只命中没传输的段（st.n == 0）也上表——
        # 「本会话 code 全是命中」正是省下字节的证据。
        if key.startswith("reroll:") or (st.n == 0 and st.hits == 0):
            continue
        def _num(v: float, digits: int) -> float | None:
            """`nan`（无样本）→ `None`（渲染成「—」而不是 0——两者含义截然不同）。"""
            return None if math.isnan(v) else round(v, digits)

        bad = sum(1 for r in st.rates if r < BAD_DRAW_KBPS)
        rows.append(
            {
                "seg": key,
                "jobs": st.n,
                "mb": round(st.bytes_total / 1048576.0, 2),
                "p50_sec": _num(percentile(st.secs, 0.5), 1),
                "p90_sec": _num(percentile(st.secs, 0.9), 1),
                # 速率只报 **p50 + 最差**：坏签是**低尾**（13 KB/s 落在排序最前），
                # 报「速率 p90」量到的是好签那一端——那会让人把坏签读成「一切正常」。
                # 秒数那边相反：坏签就是高尾，所以 p90 只对 `*_sec` 有意义。
                "p50_kbps": _num(percentile(st.rates, 0.5), 0),
                "worst_kbps": _num(min(st.rates), 0) if st.rates else None,
                "bad_draws": bad,
                "bad_pct": round(100.0 * bad / st.n, 1) if st.n else 0.0,
                "rerolls": st.rerolls,
                "wasted_mb": round(st.wasted_bytes / 1048576.0, 2),
                "hits": st.hits,
            }
        )
    return rows


def render(rows: list[dict]) -> str:
    """人读表：一屏看完「哪段在吃时间 / 坏签多不多 / 命中率」——均值刻意不出现。"""
    head = (
        f"{'seg':<22}{'n':>4}{'MB':>9}{'p50s':>8}{'p90s':>8}"
        f"{'p50KB/s':>9}{'worst':>7}{'bad%':>7}{'reroll':>7}{'wasteMB':>8}{'hit':>5}"
    )
    lines = [head, "-" * len(head)]
    for r in rows:
        def _f(v: object, width: int) -> str:
            return f"{'—' if v is None else v:>{width}}"

        lines.append(
            f"{r['seg']:<22}{r['jobs']:>4}{r['mb']:>9}"
            f"{_f(r['p50_sec'], 8)}{_f(r['p90_sec'], 8)}"
            f"{_f(r['p50_kbps'], 9)}{_f(r['worst_kbps'], 7)}"
            f"{r['bad_pct']:>7}{r['rerolls']:>7}{r['wasted_mb']:>8}{r['hits']:>5}"
        )
    lines.append("")
    lines.append(
        f"（bad% = 速率 < {BAD_DRAW_KBPS:.0f} KB/s 的传输占比；'—' = 无样本。"
        "口径：p50/p90 一律最近秩，样本 <5 时分位数不具统计意义，别拿单次读数下结论。"
        "速率列报 p50 与 worst，因为坏签是低尾——想抓尾就看 bad% 与 p90s）"
    )
    return "\n".join(lines)


def _expand(paths: list[str]) -> list[Path]:
    """参数展开：目录 → 其中所有 `*.log`（按名排序，顺序不影响聚合）。"""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(p.glob("*.log")))
        elif p.is_file():
            files.append(p)
        else:
            print(f"跳过（不是文件/目录）: {p}", file=sys.stderr)
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 wire 传输账聚成 p50/p90 与坏签比例")
    ap.add_argument("paths", nargs="+", help="日志文件或目录（目录取全部 *.log）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供后续脚本/看板消费）")
    args = ap.parse_args(argv)

    files = _expand(args.paths)
    if not files:
        print("没有可读的日志文件", file=sys.stderr)
        return 2
    lines: list[str] = []
    for f in files:
        try:
            lines.extend(f.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError as e:
            print(f"读失败 {f}: {e}", file=sys.stderr)
    stats = parse_lines(lines)
    _merge_rerolls(stats)
    rows = summarize(stats)
    if args.json:
        print(json.dumps({"files": [str(f) for f in files], "rows": rows}, ensure_ascii=False, indent=2))
    elif not rows:
        print(f"{len(files)} 个文件里没有 wire 账（旧版本 worker 的日志？）")
    else:
        print(f"{len(files)} 个文件，{len(rows)} 个段：")
        print(render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
