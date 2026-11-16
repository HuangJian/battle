"""每轮语料覆盖率报表（DoD #722）：逐关 games / unique seeds / trim 前后样本 / SHORT / 丢弃整局。

为什么需要它：plan §9 要求「每轮报告每关 games/unique seeds/trim 前后 samples/SHORT/丢弃整局；
硬闸「35 关均有有效样本」；同一 run 内不重复 `(stage,seed)`」。此前这块只有代码里的机制
（`volume_quota` / `np_core.per_stage_quota`），没有一份**实盘输出**，等于 DoD 无法验收。

口径与 `np_core._load_episodes_core`（`per_stage_quota>0`）逐字对齐：
  * 每关配额 = `volume_quota.target_per_stage(target_transitions, n_stages)`（ceil）；
  * shard 按**目录名排序**逐个消费（与 `discover_shards` 同），配额满则后续整局丢弃；
  * 最后一个 shard 可能只留 `room < N` 条样本（trim），不切半局给下一关。

只读 manifest.json，不读 npy：报表是调度账，不是语料身份校验（后者由 replay identity 负责）。

用法（仓库根）：
    python nn-training/tools/coverage_report.py --root <shard-root> \
        --target-transitions 24576 --expect-stages 35 --out reports/coverage-XXXX.md
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NN_ROOT = Path(__file__).resolve().parents[1]
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from common.cli import house_parser, run_main
from common.errors import ProtocolError
from worker.volume_quota import target_per_stage


@dataclass
class StageRow:
    stage: int
    games: int = 0
    seeds: set[Any] = field(default_factory=set)
    before: int = 0
    after: int = 0
    trimmed: int = 0
    dropped_games: int = 0
    truncated_games: int = 0

    @property
    def short(self) -> int:
        return int(max(0, self.quota - self.after))  # type: ignore[attr-defined]

    @property
    def valid(self) -> bool:
        return self.after > 0


def coverage_rows(
    shard_dirs: list[Path], *, target_transitions: int, expect_stages: int | None = None
) -> tuple[list[StageRow], list[str]]:
    """按 quota 逐关结算；返回逐关行与全局问题列表（空 = 全绿）。"""
    if target_transitions <= 0:
        raise ProtocolError(f"target_transitions 必须 >0，收到 {target_transitions}")

    manifests: list[tuple[Path, dict]] = []
    for d in shard_dirs:
        p = d / 'manifest.json'
        if not p.is_file():
            continue  # 与 discover_shards 一致：没有 manifest 的目录不算 shard
        try:
            m = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError) as e:
            raise ProtocolError(f"manifest 不可解析：{p}: {e}") from e
        manifests.append((d, m))
    manifests.sort(key=lambda x: x[0].name)

    stages = sorted({int(m['stage']) for _d, m in manifests if 'stage' in m})
    if expect_stages is not None and len(stages) != expect_stages:
        raise ProtocolError(
            f"关卡数硬闸失败：期望 {expect_stages} 关，语料里有 {len(stages)} 关（{stages[:5]}…）"
        )
    quota = target_per_stage(target_transitions, max(1, len(stages)))

    rows = {s: StageRow(stage=s) for s in stages}
    for row in rows.values():
        row.quota = quota  # type: ignore[attr-defined]
    problems: list[str] = []
    seen: set[tuple[int, Any]] = set()
    for d, m in manifests:
        stage = int(m['stage'])
        row = rows[stage]
        row.games += 1
        seed = m.get('seed')
        if seed is not None:
            row.seeds.add(seed)
            key = (stage, seed)
            if key in seen:
                problems.append(f"重复 (stage,seed)={key}: {d.name}")
            seen.add(key)
        n = int(m.get('nSamples') or 0)
        row.before += n
        room = quota - row.after
        if room <= 0:
            row.dropped_games += 1
            continue
        if n > room:
            row.truncated_games += 1
            row.trimmed += n - room
            row.after += room
        else:
            row.after += n

    for stage, row in rows.items():
        if not row.valid:
            # 只有硬闸失败才进 problems；SHORT 是读数（配额没吃满，照样能训）。
            # 让 SHORT 返回非 0 会让每轮调度假红，与「响亮但可继续」的纪律冲突。
            problems.append(f"stage {stage} 无有效样本（硬闸失败）")
    return [rows[s] for s in stages], problems


def render(
    rows: list[StageRow], *, target_transitions: int, problems: list[str], source: str
) -> str:
    quota = target_per_stage(target_transitions, max(1, len(rows)))
    total_before = sum(r.before for r in rows)
    total_after = sum(r.after for r in rows)
    out = [
        '# 每轮语料覆盖率报表',
        '',
        f'- 来源：`{source}`',
        f'- `target_transitions={target_transitions}` ⇒ 每关配额 **{quota}** 样本（ceil）',
        f'- 关卡数：**{len(rows)}**；games：**{sum(r.games for r in rows)}**；'
        f'unique (stage,seed)：**{sum(len(r.seeds) for r in rows)}**',
        f'- samples：trim 前 **{total_before}** → trim 后 **{total_after}**；'
        f'裁掉 **{total_before - total_after}**；丢弃整局 **{sum(r.dropped_games for r in rows)}**',
        f'- 硬闸：{len(rows)} 关均有效样本 ⇒ **{"PASS" if all(r.valid for r in rows) else "FAIL"}**；'
        f'重复 pair ⇒ **{"PASS" if not any("重复" in p for p in problems) else "FAIL"}**',
        '',
        '| stage | games | unique seeds | samples(前) | samples(后) | trimmed | 丢弃整局 | 截断局 | SHORT |',
        '|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for r in rows:
        out.append(
            f'| {r.stage} | {r.games} | {len(r.seeds)} | {r.before} | {r.after} | '
            f'{r.trimmed} | {r.dropped_games} | {r.truncated_games} | {r.short} |'
        )
    short = [(r.stage, r.short) for r in rows if r.short]
    out += ['', f'## SHORT（配额未吃满，可训但要盯）: {len(short)}/{len(rows)} 关', '']
    out += [f'- stage {s}: 缺 {n} 样本' for s, n in short] if short else ['- 无']
    if problems:
        out += ['', '## 硬闸问题', ''] + [f'- {p}' for p in problems]
    return '\n'.join(out) + '\n'


def main() -> int:
    ap = house_parser(description=__doc__)
    ap.add_argument('--root', required=True, type=Path, help='shard 根目录')
    ap.add_argument('--target-transitions', type=int, default=24576)
    ap.add_argument('--expect-stages', type=int, default=35)
    ap.add_argument('--out', type=Path, default=None, help='报表落盘路径；缺省只打印')
    args = ap.parse_args()

    dirs = [p for p in args.root.iterdir() if p.is_dir()]
    rows, problems = coverage_rows(
        dirs, target_transitions=args.target_transitions, expect_stages=args.expect_stages
    )
    text = render(rows, target_transitions=args.target_transitions, problems=problems, source=str(args.root))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding='utf-8')
        print(f'wrote {args.out}')
    print(text.splitlines()[6])  # 一行摘要
    return 0 if not problems else 2


if __name__ == '__main__':
    run_main(main)
