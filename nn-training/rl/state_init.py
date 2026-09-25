"""state_init —— rollout 起始分布（人类中段快照）的派生与护栏（plan/x20-state-init.plan.md P3）。

**一句话**：一局仍是标准的 `(stage, seed)` 游戏，但起始世界换成人类 demo 在 tick T 的**快照**
（`tools/sim/build-state-init-bank.ts` 的产物），T 之后交棒给策略。本模块只做两件事：

  1. **派生**（纯函数）：`(rotate_seed, it, stage, seed)` → 银行里某个 `(局, 切点)`。同 key 必同
     结果（断点续跑/跨机重放逐字节一致），换 `it` 必换（§15.1 逐轮换语料——起始状态确实变了，
     而 `(stage, seed)` 的种子流仍按 `build_pairs`/`volume_waves` 的老规矩轮换）。
  2. **护栏**：课程没开 `state_init` ⇒ 一切照旧（返回 None，零回归）；开了但缺银行/缺派生输入/
     走上云路 ⇒ **响亮 SystemExit**（宁可不发，不可静默跑成标准开局——那等于换了一个实验，
     而判据照算）。

为什么派生要住 Python 而不是让导出器自己挑：挑切点要读银行 manifest（数据），而「这一局用哪个
状态」是**采集侧采样规则**的一部分（进 `corpus_identity_fp` 的血缘面）——`build_pairs` 的同族。

与 AGENTS §2.2（No Hidden State）：唯一的状态是**不可变**的 `BankIndex`（纯数据的 memo），
按 `(path, mtime)` 缓存；没有任何模块级游标（禁「上一轮抽到哪」这类记忆），派生完全由 key 决定。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

#: 派生种子流的 tag（与 `build_pairs` 的 0x5EED / 补波 0xAA9E 同族，便于血缘识别）。
#: ⚠ 改它 = 整条切点序列变 = 新实验（§15.5）。
STATE_INIT_TAG = 0x517A7E


@dataclass(frozen=True)
class SnapshotRef:
    """一次派生结果：银行局的某个切点。"""

    #: 导出器 CLI 用的路径（`--init-snapshot <path>`）。
    path: str
    #: 银行内的相对名（写进 shard manifest；排障时能一眼看出这局从哪个状态起跑）。
    snapshot: str
    #: 交棒 tick（局内游戏时钟）。
    tick: int
    #: 银行局 id（`s<stage>-<demoSeed>`）。
    game: str


@dataclass(frozen=True)
class GameEntry:
    id: str
    stage: int
    demo_seed: int
    ticks: int
    #: 该局物化好的切点（升序；`(tick, 相对文件名)`）。
    cuts: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class BankIndex:
    """银行 manifest 的只读索引（按 stage 分组；不可变——派生是纯函数的前提）。"""

    root: Path
    by_stage: dict[int, tuple[GameEntry, ...]]
    totals: dict

    @staticmethod
    def load(manifest_path: str | Path) -> BankIndex:
        p = Path(manifest_path)
        if not p.is_file():
            raise SystemExit(
                f"[state_init] 银行 manifest 不在盘上：{p}——先跑 "
                "`bun tools/sim/build-state-init-bank.ts ...`（plan P0），别拿空银行开训"
            )
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SystemExit(f"[state_init] 银行 manifest 读不了/不是 JSON：{p}（{e}）") from e
        games_raw = raw.get("games")
        if not isinstance(games_raw, list) or not games_raw:
            raise SystemExit(f"[state_init] 银行 manifest 没有 games（{p}）——空银行不许开训")
        by_stage: dict[int, list[GameEntry]] = {}
        for g in games_raw:
            cuts = tuple((int(c["tick"]), str(c["file"])) for c in g.get("cuts", []))
            if not cuts:
                continue
            entry = GameEntry(
                id=str(g["id"]),
                stage=int(g["stage"]),
                demo_seed=int(g["demoSeed"]),
                ticks=int(g["ticks"]),
                cuts=cuts,
            )
            by_stage.setdefault(entry.stage, []).append(entry)
        return BankIndex(
            root=p.parent,
            by_stage={
                k: tuple(sorted(v, key=lambda g: (g.demo_seed, g.id))) for k, v in by_stage.items()
            },
            totals=dict(raw.get("totals") or {}),
        )


@lru_cache(maxsize=8)
def _load_index_cached(path: str, mtime_ns: int) -> BankIndex:
    """`(路径, mtime)` → 索引（纯数据的 memo）。

    mtime 进 key：银行被重建（P0 重跑）时**不会**拿到旧索引——那是「换了起始分布却按旧切点记账」
    这类最难查的事。用 `lru_cache` 而不是模块级字典：没有可写游标，语义就是「纯函数缓存」。
    """
    del mtime_ns  # 只用于分缓存键；银行内容本身走 path
    return BankIndex.load(path)


def load_bank(manifest_path: str | Path) -> BankIndex:
    """读 + 缓存银行索引（相对路径按当前 cwd 解析；mtime 变化即重读）。"""
    p = Path(manifest_path)
    try:
        mtime = p.stat().st_mtime_ns
    except OSError as e:
        raise SystemExit(f"[state_init] 银行 manifest 不在盘上：{p}（{e}）") from e
    return _load_index_cached(str(p), int(mtime))


def pick(
    index: BankIndex,
    *,
    rotate_seed: int,
    it: int,
    stage: int,
    seed: int,
    rotate_cuts: bool,
) -> SnapshotRef | None:
    """派生一局的起始状态（纯函数）。

    key = `(rotate_seed, STATE_INIT_TAG, it, stage, seed)` 与 `build_pairs`/`volume_waves` 同款
    键控：同 key 跨进程/跨重启逐字节一致（断点续跑不会换起始状态），换 `it` 换一整套
    （§15.1——起始状态也是语料的一部分）。

    银行里没有该 stage 的局 ⇒ `None`（该局的 stage 没有人类 demo，安静退回标准开局——
    那是**课程配置**层面的事实，不是错误；真正的错误在开训前置：银行四关齐全）。
    `rotate_cuts=False` ⇒ 恒取该局最小切点（不消耗随机数；切点固定、局仍轮换）。
    """
    games = index.by_stage.get(int(stage))
    if not games:
        return None
    rng = np.random.default_rng([int(rotate_seed), STATE_INIT_TAG, int(it), int(stage), int(seed)])
    game = games[int(rng.integers(0, len(games)))]
    tick, rel = game.cuts[0] if not rotate_cuts else game.cuts[int(rng.integers(0, len(game.cuts)))]
    return SnapshotRef(
        path=str(index.root / rel),
        snapshot=rel.split("/")[-1],
        tick=int(tick),
        game=game.id,
    )


def argv_init_for(args, *, stage: int, seed: int, node_side: bool = False) -> SnapshotRef | None:
    """该局要追加的 `--init-snapshot`（纯函数入口；`None` = 标准开局）。

    三条响亮拒（每一条都对应一种「跑起来了但完全不是你要的」）：
      * `node_side=True`：云侧快照搬运（plan §P2.5）**尚未落地**——上云轮的 argv 里给一个
        仓库相对路径，节点上根本没有那个文件，而老导出器会**静默忽略未知 flag** ⇒ 云上跑的
        是标准开局、账本却写着中段起跑。宁可现在拒发。
      * `args._it` / `args._rotate_seed` 缺失：派生 key 缺一半 ⇒ 无法保证「同 key 同结果」与
        §15.1 轮换（`loop_steps._course_iter` 每轮注入；走别的入口 = 采集侧漏接）。
      * 银行 manifest 不在盘上（`load_bank` 里拒）。
    """
    si = getattr(args, "state_init", None)
    if not si:
        return None
    if node_side:
        raise SystemExit(
            "[state_init] 云侧快照搬运尚未落地（plan/x20-state-init.plan.md §P2.5）："
            "本腿 v1 只在**本机**采集下可跑。别把仓库相对路径发到没有那份数据的节点上——"
            "老导出器会静默忽略 --init-snapshot，云上跑标准开局而账本记中段起跑。"
        )
    it = getattr(args, "_it", None)
    rotate_seed = getattr(args, "_rotate_seed", None)
    if it is None or rotate_seed is None:
        raise SystemExit(
            "[state_init] 派生需要 args._it / args._rotate_seed（由 loop_steps._course_iter "
            "每轮注入）——当前采集入口没走那条路，拒发（不猜另一个轮次）"
        )
    # 银行路径的解析（cwd → 仓库根 → nn-training 三种基准）与 P2 启动期校验**同一实现**。
    from rl.config import resolve_state_init_bank

    bank = resolve_state_init_bank(str(si.get("bank", "")))
    if bank is None:
        raise SystemExit(
            f"[state_init] 银行 manifest 不在盘上：{si.get('bank')!r}——先跑 plan P0 的银行生成"
        )
    ref = pick(
        load_bank(bank),
        rotate_seed=int(rotate_seed),
        it=int(it),
        stage=int(stage),
        seed=int(seed),
        rotate_cuts=bool(si.get("rotate_cuts", True)),
    )
    if ref is None:
        raise SystemExit(
            f"[state_init] 银行里没有 stage={stage} 的人类局——该关无从借状态（plan §2 B1："
            "跨关借状态是另一个实验）。检查银行覆盖（四关齐全）或这门课的 stages"
        )
    return ref


__all__ = [
    "STATE_INIT_TAG",
    "BankIndex",
    "GameEntry",
    "SnapshotRef",
    "argv_init_for",
    "load_bank",
    "pick",
]
