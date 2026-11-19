"""lazy_corpus.py — PPO 语料的 **lazy mmap** 装载（plan/replay-corpus.plan.md P3/D6）。

为什么需要（事实见 plan §1.6）：eager 的 `load_episodes → chunk_episodes` 先把全部
episode `np.concatenate` 成一份，再 fancy-index 复制成 chunks ⇒ 峰值 ≈ 3× obs。
491k 样本 = 5.65 GB obs ⇒ 11–17 GB；小内存/小 VRAM 机器直接不可用。

本模块只做**装载与取行**，不 import torch：

  * `obs.npy` 逐 shard `mmap_mode="r"`（唯一的大张量，走 page cache，不要求常驻）；
  * 其余字段（scalars/extra/动作/lp/value/mask/policy_mask + adv/ret）491k 样本
    合计 ≈ 200 MB，**整份常驻内存**——它们每步都要用，做成 mmap 只会把「一次读」
    变成「每步缺页」；obs 之外的字段才是与大语料无关的小头；
  * GAE / 配额截断 / 全局归一与 eager 共用 `np_core` 的实现与顺序（逐字节对账）；
  * `np.random.permutation(n)` 与 eager 的 `chunk_episodes` **消费同一段 RNG 流**；
  * `row_map` 显式记录 global_row → (shard_id, local_row)，供对账与测试。

对账契约（plan §5.2）：shard 顺序 = `discover_rl_shards`；行序 = `np.concatenate`
顺序；permutation 同一次；quota 截断在 GAE 之前；adv/ret 归一含被丢尾部；
per-epoch 不重排（perm 只换 chunk 顺序，chunk 内容由全局置换固定）。
"""

from __future__ import annotations

import os
import time

import numpy as np

import worker.ppo.np_core as np_core
from common.log import log
from common.log_bundle import LogBundle
from worker.data.npyio import CorpusMemoryPlan, check_corpus_memory
from worker.ppo._episode import ShardRow

#: 取 chunk 时要交给训练器的字段（obs 单独走 mmap；reward/done/stage 是装载中间量）。
#: 真正的键集来自 shard 本身（v6 的 a_hold/lp_hold 是可选列），这里只排除中间量。
_SKIP_KEYS = frozenset({"obs", "reward", "done", "stage"})


class LazyCorpus:
    """逐 shard mmap obs + 常驻小字段的 PPO 语料。

    `row_shard` / `row_local` 就是 plan 要求的 `row_map`：第 g 行 = 第
    `row_shard[g]` 个 shard 的第 `row_local[g]` 行。二者在装载时一次算好
    （int32，491k 行 ≈ 3.9 MB），取行时按 shard 分组只做一次 fancy index。
    """

    lazy = True
    loader = "lazy"

    def __init__(
        self,
        *,
        obs_parts: list[np.ndarray],
        shard_sizes: list[int],
        fields: dict[str, np.ndarray],
        keys: tuple[str, ...],
        plan: CorpusMemoryPlan,
        source: str,
    ) -> None:
        self._obs = obs_parts
        self._shard_sizes = np.asarray(shard_sizes, dtype=np.int64)
        self._fields = fields
        self.keys = keys
        self._plan = plan
        self.source = source
        self.obs_tail = tuple(int(x) for x in obs_parts[0].shape[1:])
        self.obs_dtype = obs_parts[0].dtype
        # row_map：global_row → (shard_id, local_row)
        self.row_shard = np.repeat(
            np.arange(len(shard_sizes), dtype=np.int32), self._shard_sizes
        )
        starts = np.concatenate(
            [np.zeros(1, dtype=np.int64), np.cumsum(self._shard_sizes)[:-1]]
        )
        self.row_local = (
            np.arange(int(self._shard_sizes.sum()), dtype=np.int64) - starts[self.row_shard]
        ).astype(np.int32)
        self.samples = int(self._shard_sizes.sum())
        self.peak_mb = 0.0  # make_chunks 里按真实 mb 预检后填（日志读数）

    def gather(self, rows: np.ndarray) -> dict[str, np.ndarray]:
        """全局行号（int 数组）→ 一个 minibatch 的 numpy dict。

        obs 按 shard 分组各做一次 fancy index（每块只复制要用的行，不碰整份 5.65 GB）；
        其余字段常驻内存，直接 fancy index。返回数组全部可写（fancy index 必复制）。
        """
        rows = np.asarray(rows, dtype=np.int64)
        b = int(rows.shape[0])
        obs = np.empty((b, *self.obs_tail), dtype=self.obs_dtype)
        sid = self.row_shard[rows]
        loc = self.row_local[rows]
        order = np.argsort(sid, kind="stable")
        s_sorted = sid[order]
        uniq, first = np.unique(s_sorted, return_index=True)
        bounds = np.append(first, b)
        for n, shard in enumerate(uniq):
            sel = order[bounds[n] : bounds[n + 1]]
            obs[sel] = self._obs[int(shard)][loc[sel]]
        out: dict[str, np.ndarray] = {"obs": obs}
        for key in self.keys:
            out[key] = self._fields[key][rows]
        return out

    def make_chunks(
        self, mb: int, shuffle: bool = True, *, rng: np.random.Generator | None = None
    ) -> LazyChunks:
        """与 `np_core.chunk_episodes` **同语义**的 chunk 视图（延迟物化）。

        ★ RNG 顺序是契约的一部分：shuffle 路径在这里消费一次 `permutation(n)`，
        与 eager 的 chunk_episodes 用**同一条显式流**（`PPORng.chunk`，§P1-7）；
        `rng=None` 只留给旧调用方/工具（生产入口一律显式传）。
        `shuffle=False` 或只有一个 shard 时**不消费 RNG**、不裁尾（eager 的
        `len(episodes) <= 1` 分支——单池裁尾等于裁局末，有偏）。
        """
        mb = int(mb)
        if mb <= 0:
            raise ValueError(f"[ppo] lazy loader: mb 必须 > 0（收到 {mb}）")
        # 内存预检（plan D6：复用 BC 的公式，batch_size = 真实 mb）。
        peak = self._plan.lazy_peak_bytes(batch_size=mb)
        check_corpus_memory(self._plan, lazy=True, batch_size=mb, num_workers=0)
        self.peak_mb = peak / (1 << 20)
        log(
            f"[ppo] loader=lazy peak≈{self.peak_mb:.0f}MB（不含 page cache；"
            f"{self._plan.shards} shards / {self.samples} 样本 / mb={mb}）"
        )
        n = self.samples
        if n == 0:
            raise SystemExit(f"[ppo] lazy loader: 无样本（{self.source}）")
        if not shuffle or len(self._shard_sizes) <= 1:
            sizes: list[int] = []
            for total in (int(x) for x in self._shard_sizes):
                full, rest = divmod(total, mb)
                sizes.extend([mb] * full)
                if rest:
                    sizes.append(rest)
            order: np.ndarray = np.arange(n, dtype=np.int64)
            return LazyChunks(self, order, sizes)
        idx = (rng if rng is not None else np.random).permutation(n)
        n_used = (n // mb) * mb
        if n_used == 0:
            # 与 eager 同一条兜底：池子 < mb 时保留一块 ragged（丢弃 = 静默不训练）。
            log(f"[ppo] lazy chunk WARN 池子 {n} 步 < mb={mb} ⇒ 不裁剪（单独一块 ragged）")
            n_used = n
        order = np.asarray(idx[:n_used], dtype=np.int64)
        sizes = [mb] * (n_used // mb) or [n_used]
        return LazyChunks(self, order, sizes)


class LazyChunks:
    """`LazyCorpus` 上的 minibatch 序列（`get(i)` 才真正读 obs 页）。

    刻意**不**实现 `__len__`/`__getitem__`：序列协议会把这两个 Dunder 名散到第三个
    模块（`tests/test_reuse_ledger.py` 的同名散落基线），而调用面只有 `ppo_update`
    一处；它走 `.sizes` / `.get(i)` 反而把「延迟物化」写在脸上。
    """

    lazy = True
    loader = "lazy"

    def __init__(self, corpus: LazyCorpus, order: np.ndarray, sizes: list[int]) -> None:
        self._corpus = corpus
        self._order = order
        self.sizes = [int(x) for x in sizes]
        starts = np.concatenate([np.zeros(1, dtype=np.int64), np.cumsum(self.sizes)[:-1]])
        self._starts = starts.astype(np.int64)
        self.peak_mb = corpus.peak_mb
        self.samples = int(sum(self.sizes))

    def get(self, k: int) -> dict[str, np.ndarray]:
        """取第 k 个 minibatch（负下标按 Python 惯例）。"""
        i = int(k)
        if i < 0:
            i += len(self.sizes)
        if not 0 <= i < len(self.sizes):
            raise IndexError(f"[ppo] lazy chunk 下标 {k} 越界（N={len(self.sizes)}）")
        s = int(self._starts[i])
        return self._corpus.gather(self._order[s : s + self.sizes[i]])


def load_ppo_corpus(
    data_root: str,
    *,
    loader: str = "eager",
    gamma: float = np_core.GAMMA,
    lam: float = np_core.LAM,
    normalize_adv: bool = True,
    normalize_ret: bool = False,
    per_stage_quota: int = 0,
    mb: int = 512,
    bundle: LogBundle | None = None,
):
    """`--ppo-loader` 的唯一分派点：eager = 旧 `np_core.load_episodes`（逐字节不变）。

    调用方（`remote/train_core`、`worker/ppo/engine` CLI）只认这个函数，不要各自
    复制 if/else——否则「manifest 说 lazy、代码走 eager」会是一处静默的错账。
    """
    if loader == "eager":
        return np_core.load_episodes(
            data_root,
            gamma,
            lam,
            normalize_adv=normalize_adv,
            normalize_ret=normalize_ret,
            per_stage_quota=per_stage_quota,
            bundle=bundle,
        )
    if loader != "lazy":
        raise ValueError(f"ppo_loader={loader!r} 未知（只认 'eager' | 'lazy'）")
    return load_lazy_corpus(
        data_root,
        gamma=gamma,
        lam=lam,
        normalize_adv=normalize_adv,
        normalize_ret=normalize_ret,
        per_stage_quota=per_stage_quota,
        mb=mb,
        bundle=bundle,
    )


def load_lazy_corpus(
    data_root: str,
    *,
    gamma: float = np_core.GAMMA,
    lam: float = np_core.LAM,
    normalize_adv: bool = True,
    normalize_ret: bool = False,
    per_stage_quota: int = 0,
    mb: int = 512,
    bundle: LogBundle | None = None,
) -> LazyCorpus:
    """Discover shards → mmap obs → per-shard GAE → global normalize → `LazyCorpus`.

    与 `np_core.load_episodes` 的差异只有「数据住哪」：GAE、配额截断、归一化顺序
    逐字节沿用（同一个 `compute_gae` / `trim_shard_arrays` 实现）。
    """
    shards = np_core.discover_rl_shards(data_root)
    if not shards:
        raise SystemExit(f"[ppo] no RL shards found under {data_root}")
    if bundle is not None:
        bundle.add("shards", f"{len(shards)} RL")
    else:
        log(f"[ppo] loaded {len(shards)} RL shards (lazy mmap) from {data_root}")

    obs_parts: list[np.ndarray] = []
    shard_sizes: list[int] = []
    parts: dict[str, list[np.ndarray]] = {}
    advs: list[np.ndarray] = []
    rets: list[np.ndarray] = []
    keys: tuple[str, ...] | None = None
    seen_steps: dict[int, int] = {}
    dropped_shards = 0
    samples_phys = 0
    obs_bytes = 0
    other_bytes = 0
    t_load = time.monotonic()
    for k, sd in enumerate(shards):
        if k > 0 and k % 128 == 0:
            if bundle is not None:
                bundle.add("装载", f"{k}/{len(shards)} {time.monotonic() - t_load:.0f}s")
            else:
                log(
                    f"[ppo] lazy loading shards {k}/{len(shards)} "
                    f"({time.monotonic() - t_load:.0f}s)"
                )
        # 内存预检的足迹：只看盘上字节（obs 单列；其余字段含 metrics/reward，偏保守）。
        obs_path = os.path.join(sd, "obs.npy")
        obs_bytes += os.path.getsize(obs_path)
        for name in os.listdir(sd):
            if name.endswith(".npy") and name != "obs.npy":
                other_bytes += os.path.getsize(os.path.join(sd, name))
        d: ShardRow = np_core.load_shard(sd, mmap=True)
        n = int(d["obs"].shape[0])
        samples_phys += n
        if n == 0:
            continue
        if per_stage_quota > 0:
            # 与 load_episodes_common 同一套逐关配额：截断在 GAE **之前**（反向递推）。
            stage = int(d.get("stage", -1))
            room = per_stage_quota - seen_steps.get(stage, 0)
            if room <= 0:
                dropped_shards += 1
                continue
            if room < n:
                d = np_core.trim_shard_arrays(d, room)
                n = room
            seen_steps[stage] = seen_steps.get(stage, 0) + n
        adv, ret = np_core.compute_gae(d["reward"], d["value"], d["done"], gamma, lam)
        these = tuple(kk for kk in d if kk not in _SKIP_KEYS)
        if keys is None:
            keys = these
        elif these != keys:
            raise ValueError(
                f"[ppo] lazy loader: shard {sd} 的字段集与首个 shard 不一致"
                f"（{these} vs {keys}）——v6/legacy 语料混装，无法拼成同一份张量"
            )
        obs_parts.append(d["obs"])
        shard_sizes.append(n)
        for kk in keys:
            if kk == "obs":
                continue
            parts.setdefault(kk, []).append(d[kk])
        advs.append(adv.astype(np.float32))
        rets.append(ret.astype(np.float32))

    if keys is None or not shard_sizes:
        raise SystemExit(f"[ppo] lazy loader: {len(shards)} 个 shard 全是空样本（{data_root}）")
    fields = {kk: np.concatenate(v, axis=0) for kk, v in parts.items()}
    adv_all = np.concatenate(advs)
    ret_all = np.concatenate(rets)
    if normalize_adv:
        amean, astd = adv_all.mean(), adv_all.std() + 1e-8
        adv_all = (adv_all - amean) / astd
    if normalize_ret:
        rmean, rstd = ret_all.mean(), ret_all.std() + 1e-8
        ret_all = (ret_all - rmean) / rstd
    fields["adv"] = adv_all
    fields["ret"] = ret_all
    keys = (*keys, "adv", "ret")

    if per_stage_quota > 0:
        total = sum(seen_steps.values())
        short = {
            s: per_stage_quota - n
            for s, n in sorted(seen_steps.items())
            if n < per_stage_quota
        }
        line = (
            f"per-stage quota={per_stage_quota}: kept {total} steps / "
            f"{len(seen_steps)} stages, dropped {dropped_shards} shards"
        )
        if bundle is not None:
            bundle.add("配额", line)
            if short:
                bundle.note(f"SHORT (供给不足) stages={short}")
        else:
            log(f"[ppo] {line}" + (f"; SHORT (供给不足) stages={short}" if short else ""))

    elapsed = time.monotonic() - t_load
    plan = CorpusMemoryPlan(str(data_root), len(shards), samples_phys, obs_bytes, other_bytes)
    if bundle is not None:
        bundle.add("装载", f"{len(shards)}/{len(shards)} {elapsed:.0f}s")
        bundle.add("episodes", f"{int(sum(shard_sizes))} rows（lazy，GAE 已算）")
    else:
        log(
            f"[ppo] lazy shard IO + GAE done for {len(shard_sizes)} shards / "
            f"{int(sum(shard_sizes))} rows ({elapsed:.0f}s)"
        )
    return LazyCorpus(
        obs_parts=obs_parts,
        shard_sizes=shard_sizes,
        fields=fields,
        keys=keys,
        plan=plan,
        source=str(data_root),
    )
