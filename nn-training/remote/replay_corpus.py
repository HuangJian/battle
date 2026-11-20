"""remote/replay_corpus.py — worker 侧 replay → obs/scalars/extra 的 pre-pass（P2）。

判据（plan/replay-corpus.plan.md D3/D5）：

* 转换器 bundle 由 **trainer 预构建**、随 kind=ppo job 的 payload 根下发（`replay_converter.js`），
  worker **只跑不建**（2 vCPU 不付 `bun build`）；manifest 带 `replay_converter_sha256`。
* 世界重建与校验全部在转换器内完成（obsHash / tickHash / decisionTicks / 终局）；
  本模块只负责「找到运行时 → 逐 shard 跑 → 断言产物齐全 → 写完成标记」。
* **确定性失败零容差**：任一 shard 转换失败 = ProtocolError（重试无意义），
  不允许跳过该 shard 静默训少一局。
* **可重试/可续跑**：每个 shard 成功后在目录里写 `replay_converted.json`
  （converter sha + replay sha）；重启时只复用「标记 + 产物齐全 + sha 匹配」的目录。
  转换器自身是 `.tmp` + rename 原子写，崩溃后重跑会覆盖半成品。
* 运行时：优先 `node`（bundle 是 `--target=node`）；没有 Node 才用 `bun`；两者皆无 = 响亮失败。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, TypedDict

from common.errors import ProtocolError
from common.log_bundle import LogBundle
from common.platform_utils import physical_cores, rmtree_best_effort
from common.proc import run_capture
from common.runtime import Runtime

CONVERTER_NAME = "replay_converter.js"
MARKER_NAME = "replay_converted.json"
MARKER_VERSION = 1
#: 全量语料的 obs 文件名（`worker/ppo/np_core.discover_rl_shards` 的判据之一 ⇒ 缺它 =
#: 训练核读不到任何一局）。本模块只在**体检**里读它，不写。
OBS_NAME = "obs.npy"
#: replay 语料的原件；转换后仍留在 shard 目录里（与重建出的 obs 并存）。
REPLAY_NAME = "replay.json"
#: 单 shard 转换上界（秒）。一局最多 ~36000 tick；实测 600 tick 约 60ms，
#: 36000 tick 约 3.6s；900s 是「机器假死」与「真慢」的分界。
SHARD_TIMEOUT_SEC = 900.0

#: 池化宽度**按机器物理核数推导**，不是写死值（2026-11-17，8 物理 / 16 逻辑，700 shard /
#: 5,035,728 tick）：write 8w = 物理核数 8；verify-only 11w = ⌈11/8 × 物理核⌉。
#: verify-only 只读重建可轻微过订阅（8w 8.23–8.50 s → 11w 7.51–7.89 s）；write 受盘回收
#: 尖峰支配，物理核数已是最稳点（16w 15–18 s，12w 有 30–68 s 轮）。两者再受 shard 数收敛。
VERIFY_WORKERS_NUM = 11
VERIFY_WORKERS_DEN = 8


class ConvertPool(Protocol):
    """长驻转换池的最小面（生产 = `worker.serve_pool.pool_for_bundle`）。

    用协议而不是 import `worker/`：`remote/` 不能依赖 `worker/`（分层账本）。池的
    `try_capture` 返回 `None` = 给不出确定成功 ⇒ 调用方回退一次性 spawn。
    """

    def try_capture(
        self,
        argv: list[str],
        timeout_sec: float,
        *,
        label: str,
        kind: str,
        where: str,
    ) -> object | None: ...

    def summary(self) -> str: ...

    def close(self) -> None: ...


class ConvertReadout(TypedDict):
    """`convert_replay_corpus` 的读数面（iteration `wire` 子字典 + 日志）。"""

    shards: int
    reused: int
    sec: float
    converter_sha256: str
    runtime: str
    convert_workers: int
    corpus_fmt: str
    replay_bytes: int
    labels_bytes: int
    convert_jobs: int
    convert_fallback: int


def resolve_js_runtime() -> str:
    """转换器运行时：**Bun 优先**，Bun 缺失才回落 Node；都没有 = 响亮失败。

    项目标准运行时是 Bun（所有环境开工前先装 bun）；环境预装 Node 只在 bun 装不上时兜底
    （<1%，plan/replay-corpus.plan.md D5）。探测顺序写反过：2026-11-16 前是
    `("node", "bun")`，而 Kaggle/Colab 两者都装 ⇒ 稳定挑到 Node，白丢云机实测
    22.1 → 25.4 µs/tick（1.15×）的换算速度。
    """
    for name in ("bun", "node"):
        found = shutil.which(name)
        if found:
            return found
    raise ProtocolError(
        "replay 语料需要 Bun（首选）或 Node ≥20（兜底）运行预建转换器，但本机两者都没有"
        "（plan/replay-corpus.plan.md D5：Bun 优先，Bun 装不上才回落 Node；两者皆无即失败）"
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_converter(job_dir: str | Path, manifest: dict) -> tuple[Path, str]:
    """校验 payload 根下发的转换器：文件在 + sha 与 manifest 一致。"""
    p = Path(job_dir) / CONVERTER_NAME
    want = str(manifest.get("replay_converter_sha256", "") or "")
    if not want:
        raise ProtocolError(
            "corpus_fmt=replay 的 job 缺 manifest.replay_converter_sha256——无法判定转换器身份，拒收"
        )
    if not p.is_file():
        raise ProtocolError(f"corpus_fmt=replay 的 payload 缺 {CONVERTER_NAME}——拒收")
    got = _sha256_file(p)
    if got != want:
        raise ProtocolError(
            f"{CONVERTER_NAME} sha 不符（payload={got[:12]}… manifest={want[:12]}…）——拒收"
        )
    return p, want


def _marker_ok(shard_dir: Path, converter_sha: str) -> bool:
    """完成标记 + 三张产物齐全 + 转换器 sha 匹配 ⇒ 可复用。"""
    marker = shard_dir / MARKER_NAME
    if not marker.is_file():
        return False
    try:
        meta = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(meta, dict) or int(meta.get("version", -1)) != MARKER_VERSION:
        return False
    if str(meta.get("converter_sha256", "")) != converter_sha:
        return False
    return all((shard_dir / n).is_file() for n in ("obs.npy", "scalars.npy", "extra.npy"))


def _link_or_copy(src: str, dst: str) -> str:
    """shard 事务的工作副本：优先硬链接（零拷贝）；不支持时退回复制。"""
    try:
        os.link(src, dst)
        return dst
    except OSError:
        return shutil.copy2(src, dst)


def _clean_stale_transaction(shard_dir: Path) -> None:
    """清掉上次崩溃留下的 `.conv-tmp` / `.conv-old`（否则 copytree/rename 会撞）。"""
    parent, name = shard_dir.parent, shard_dir.name
    tmp = parent / (name + ".conv-tmp")
    old = parent / (name + ".conv-old")
    if tmp.exists():
        rmtree_best_effort(tmp, ignore_errors=True)
    if old.exists():
        if shard_dir.exists():
            rmtree_best_effort(old, ignore_errors=True)
        else:
            # 崩在两次 rename 之间：原目录在 .conv-old，先复位再继续。
            old.rename(shard_dir)


def _prepare_shard(shard_dir: Path, converter_sha: str) -> Path | None:
    """开事务：返回待转换的临时目录；`None` = 标记命中，直接复用（不跑子进程）。"""
    if _marker_ok(shard_dir, converter_sha):
        return None
    _clean_stale_transaction(shard_dir)
    parent, name = shard_dir.parent, shard_dir.name
    tmp = parent / (name + ".conv-tmp")
    shutil.copytree(shard_dir, tmp, copy_function=_link_or_copy)
    return tmp


def _finish_shard(shard_dir: Path, tmp: Path, converter_sha: str) -> None:
    """校验产物 → 写完成标记 → 原子换入（失败复位）。"""
    name = shard_dir.name
    missing = [n for n in ("obs.npy", "scalars.npy", "extra.npy") if not (tmp / n).is_file()]
    if missing:
        raise ProtocolError(f"replay 转换器返回 0 但产物缺失（{name}: {missing}）——拒收")
    replay = tmp / "replay.json"
    meta = {
        "version": MARKER_VERSION,
        "converter_sha256": converter_sha,
        "replay_sha256": _sha256_file(replay) if replay.is_file() else "",
    }
    (tmp / MARKER_NAME).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    old = shard_dir.parent / (name + ".conv-old")
    os.replace(shard_dir, old)
    try:
        os.replace(tmp, shard_dir)
    except OSError:
        os.replace(old, shard_dir)
        raise
    rmtree_best_effort(old, ignore_errors=True)


def _run_oneshot(
    tmp: Path,
    *,
    converter: Path,
    runtime: str,
    timeout_sec: float,
    label: str,
    verify_only: bool = False,
) -> None:
    """一次性 spawn 转换一个事务目录（池不可用/池回退时的唯一路径）。"""
    # 绝对路径：一次性路径继承调用方 cwd，池里的 worker 的 cwd 是 **bundle 所在目录**
    # （`ServePool._spawn` 用 ts_dir 当 cwd）⇒ 相对路径在池里必然解析错。
    argv = [runtime, str(converter), "--shard-dir", str(Path(tmp).resolve())]
    if verify_only:
        argv.append("--verify-only")
    p = run_capture(argv, timeout=timeout_sec)
    if p.returncode != 0:
        raise ProtocolError(
            f"replay 转换失败（{label}，rc={p.returncode}）：" f"{(p.stderr or p.stdout)[-800:]}"
        )


def _convert_tx(
    shard_dir: Path,
    *,
    converter: Path,
    converter_sha: str,
    runtime: str,
    timeout_sec: float,
    submit: Callable[[Path], None] | None = None,
) -> bool:
    """事务化转换一个 shard；返回 True = 复用标记（未跑子进程）。

    `submit(tmp)` 决定「谁来跑这一次转换」：缺省/`None` = 一次性 spawn（池不可用、池回退、
    单测桩）；批量路径传池的提交函数（暖 worker，省掉每 shard 的进程启动）。
    事务本身与两条路径无关：输入硬链接到 `<shard>.conv-tmp` → 转换 → 校验 + 写标记 →
    原子换入。**失败时原目录一字未动。**
    """
    tmp = _prepare_shard(shard_dir, converter_sha)
    if tmp is None:
        return True
    run = submit or (
        lambda t: _run_oneshot(
            t, converter=converter, runtime=runtime, timeout_sec=timeout_sec, label=shard_dir.name
        )
    )
    ok = False
    try:
        run(tmp)
        _finish_shard(shard_dir, tmp, converter_sha)
        ok = True
        return False
    except subprocess.TimeoutExpired as e:
        raise ProtocolError(
            f"replay 转换超时（{shard_dir.name}，>{timeout_sec:.0f}s）—— 确定性失败，不重试"
        ) from e
    except OSError as e:
        raise ProtocolError(f"replay 转换器起不来（{runtime}）：{e}") from e
    finally:
        # 失败（含程序员错）一律清掉半截事务目录，再把原异常原样放出去。
        if not ok:
            rmtree_best_effort(tmp, ignore_errors=True)


def convert_shard(
    shard_dir: Path,
    *,
    converter: Path,
    converter_sha: str,
    runtime: str,
    timeout_sec: float = SHARD_TIMEOUT_SEC,
) -> bool:
    """转换一个 shard（**一次性路径**）；返回 True = 复用标记（未跑子进程）。

    批量路径见 `convert_replay_corpus`（同一套事务，子进程换成池里的暖 worker）。
    """
    return _convert_tx(
        shard_dir,
        converter=converter,
        converter_sha=converter_sha,
        runtime=runtime,
        timeout_sec=timeout_sec,
    )


def _corpus_bytes(shard_dirs: list[Path]) -> tuple[int, int]:
    """(replay.json 字节和, labels 字节和)。

    `labels` 口径（D10）：shard 里除 obs/scalars/extra 三张重建产物之外的所有 `.npy`
    （a_* / lp_* / value / reward / done / mask / policy_mask / metrics）。它们与 replay
    一起构成 trainer→worker payload 的非 obs 部分；metrics 是否单列留给 P0 分解表。
    """
    replay_bytes = 0
    labels_bytes = 0
    for sd in shard_dirs:
        rp = sd / "replay.json"
        if rp.is_file():
            replay_bytes += rp.stat().st_size
        for f in sd.glob("*.npy"):
            if f.name not in ("obs.npy", "scalars.npy", "extra.npy"):
                labels_bytes += f.stat().st_size
    return replay_bytes, labels_bytes


def convert_workers(
    requested: int | None = None,
    shards: int | None = None,
    *,
    verify_only: bool = False,
) -> int:
    """转换承载宽度：显式值优先；缺省按**物理核数**推导，再按 shard 数收敛。

    2026-11-17 实测（8 物理 / 16 逻辑，700 shard / 5,035,728 tick，plan §1.4）：
      * write：**8w = 物理核数**最稳（10.75–11.6 s）；16w 15–18 s，12w 有 30–68 s 盘回收尖峰；
      * verify-only：**11w = ⌈11/8 × 物理核⌉** 最快（7.51–7.89 s；8w 8.23–8.50 s）——
        只读重建不写盘，轻微过订阅划算。

    为什么不用逻辑核（`effective_cores()`）：这台机器 16 逻辑 = 8 物理，超线程 sibling 共享
    执行单元；按 16 开 write 池实测反而更慢。`physical_cores()` 已取「物理核、cgroup 配额、
    亲和掩码」三者最小值，云机配额缩小时会跟着缩。

    `min(…, shards)` 避免为 3 个 shard 起 11 个 worker；显式 `requested` 不被核数夹取
    （压测/复现要能指定），但同样受 shard 数约束。
    """
    if requested is not None:
        n = max(1, int(requested))
    else:
        p = max(1, int(physical_cores()))
        if verify_only:
            n = max(p, (p * VERIFY_WORKERS_NUM + VERIFY_WORKERS_DEN - 1) // VERIFY_WORKERS_DEN)
        else:
            n = p
    if shards is not None:
        n = max(1, min(n, int(shards)))
    return n


def convert_fallback_flag(runtime_name: str) -> int:
    """`convert_fallback` 读数：0 = 主运行时（bun），1 = 走了回落（任何非 bun）。

    主运行时是 bun（项目纪律：一律 bun 优先，node 只在 bun 装不上时兜底）。
    2026-11-16 前这里按 node 为主判定（`0 if runtime_name == "node" else 1`），
    改成 bun 顺序后若不同步，每次正常转换都会被记成 fallback=1。
    """
    return 0 if runtime_name.startswith("bun") else 1


def convert_replay_corpus(
    job_dir: str | Path,
    shard_dirs: Sequence[Path | str],
    manifest: dict,
    *,
    log: Callable[[str], None] = lambda _m: None,
    workers: int | None = None,
    pool_factory: Callable[[str, Path, int], ConvertPool | None] | None = None,
    verify_only: bool = False,
) -> ConvertReadout:
    """把一整轮 replay shard 就地转成 obs/scalars/extra，返回读数。

    `pool_factory(runtime, converter, workers)` = 长驻转换池的工厂，**由调用方注入**
    （生产 = `worker.serve_pool.pool_for_bundle`）。为什么不在这里直接 import：
    `remote/` 不能依赖 `worker/` —— `tests/test_remote_dag.py` 按 import 边算拓扑秩，
    本模块被 `hub_client`(L1) 与 `download`(L3) 依赖，抬高自己的秩会连坐整串。
    缺省 None = 逐 shard 一次性 spawn（行为与接池前逐字节相同）。

    `verify_only=True`：只重建 + 校验，不写 obs/scalars/extra、不写完成标记、不做事务
    （诊断/演练用；生产 pre-pass 恒 False）。宽度缺省此时是 11（write 是 8）。

    ★ `shard_dirs` **两种形状都收**（2026-11-17 现场事故）：`unpack_payload` 扫出来的
    是 **str**（`download.py:810` 的 `str(p) for p in ...`），而单测与 `convert_shard`
    调用方传 `Path`。以前只在 docstring 里写 `list[Path]`、函数体直接 `sd / "replay.json"`，
    云端第一次真跑 replay 就在 `_corpus_bytes` 炸 `TypeError: unsupported operand
    type(s) for /: 'str' and 'str'`（job 报 FAILED 后无限重领 ⇒ 队列看着像卡住）。
    入口一次性归一成 `Path`，下游（`_one`/`sd.name`/`_convert_tx`）就不必各自防御。
    """
    # `Sequence` 而不是 `list[Path | str]`（2026-11-17）：`list` 不变性让 `list[Path]`
    # 的调用方（单测 / `convert_shard`）mypy 全红；归一后的形状单独用 `dirs` 承载，
    # 下游只看到 `Path`（原先重绑参数变量，声明类型不变 ⇒ 仍报 list[Path | str]）。
    dirs: list[Path] = [p if isinstance(p, Path) else Path(p) for p in shard_dirs]
    if not dirs:
        raise ProtocolError("corpus_fmt=replay 但 payload 里没有任何 shard——拒收（不训空）")
    converter, conv_sha = verify_converter(job_dir, manifest)
    runtime = resolve_js_runtime()
    runtime_name = Path(runtime).name
    n_workers = convert_workers(workers, len(dirs), verify_only=verify_only)
    # ★ 2026-11-18：运行时**开场就报**，别等到转换完才在第一行里看见。这里也是「回落到 node」
    #   唯一会被人看见的地方 —— 之前只有末尾那行 `runtime=node`，读者得先意识到「本该是 bun」。
    #   解析与判定同一个口径（`convert_fallback_flag`）：bun = 主运行时，其余 = 回落。
    if convert_fallback_flag(runtime_name):
        log(
            f"replay 转换运行时：{runtime_name}（{runtime}）· 档位=fallback"
            "（bun 不在 PATH —— 开机供给未命中，见 CFG bin_cache_dir / ensure_replay_bun 那几行）"
        )
        # ★ 2026-11-18（用户口径「fallback 档时额外打一条醒目提醒，避免长期静默慢 1.15×」）：
        #   上面那行说「发生了什么」，这一行说「**代价 + 怎么修**」—— 能让人动手的是后者。
        #   每轮都打，**不设 warned 位**：进程级去重会让长会话在日志滚过之后重新变成静默，
        #   而它一轮只出现一次（转换按 job/轮发生，不是按 shard，不会按拍刷屏）。
        log(
            f"WARN replay 转换在 {runtime_name} 回退档：比 bun 慢约 1.15×"
            "（实测 22.1 → 25.4 µs/tick，plan/replay-corpus.plan.md D5）—— 这台盘上没有可用的 bun。"
            "修法：让 bun 落进持久缓存（开机供给 / CFG bin_cache_dir / BATTLE_BIN_CACHE），"
            "或把这门课改回 corpus_fmt=full"
        )
    else:
        log(f"replay 转换运行时：{runtime_name}（{runtime}）· 档位=primary")
    t0 = time.monotonic()
    reused = 0
    # D10 墙钟账（additive）：replay/labels 的真实字节在转换前量（转换只增不减）。
    replay_bytes, labels_bytes = _corpus_bytes(dirs)

    pool = pool_factory(runtime, converter, n_workers) if pool_factory is not None else None

    def _submit_via_pool(target: Path, label: str) -> None:
        """池里跑一次；池给不出确定成功 ⇒ 回退一次性 spawn（**只慢不错**）。"""
        argv = [str(converter), "--shard-dir", str(Path(target).resolve())]
        if verify_only:
            argv.append("--verify-only")
        got = (
            pool.try_capture(argv, SHARD_TIMEOUT_SEC, label=label, kind="conv", where=str(target))
            if pool is not None
            else None
        )
        if got is None:
            _run_oneshot(
                target,
                converter=converter,
                runtime=runtime,
                timeout_sec=SHARD_TIMEOUT_SEC,
                label=label,
                verify_only=verify_only,
            )

    def _one(sd: Path) -> tuple[bool, float]:
        """（标记命中没、这个 shard 实际花了多久）。耗时按**并发**计 —— 每 shard 各算各的。"""
        t = time.monotonic()
        if verify_only:
            # 诊断路径：直接对真实 shard 跑 `--verify-only`，不建事务目录、不写标记/产物。
            _submit_via_pool(sd, sd.name)
            return False, time.monotonic() - t
        reused_one = _convert_tx(
            sd,
            converter=converter,
            converter_sha=conv_sha,
            runtime=runtime,
            timeout_sec=SHARD_TIMEOUT_SEC,
            submit=lambda tmp: _submit_via_pool(tmp, sd.name),
        )
        return reused_one, time.monotonic() - t

    #: 真跑了子进程的 shard 的**单体耗时**（复用标记的不算 —— 它们没花转换时间）。
    durations: list[float] = []
    try:
        n = max(1, min(n_workers, len(dirs)))
        # G16：线程池的唯一所有者是 `Runtime`（线程名/上限/取消点/读数都在它身上）。
        with Runtime(scope="replay-convert", workers=n) as rt:
            for got, dt in rt.map(_one, dirs, workers=n):
                if got:
                    reused += 1
                else:
                    durations.append(dt)
    finally:
        if pool is not None:
            log(pool.summary())
            pool.close()
    sec = time.monotonic() - t0
    spread = (
        f"单 shard 均值 {1000 * sum(durations) / len(durations):.1f}ms / "
        f"最长 {1000 * max(durations):.1f}ms"
        if durations
        else "全部命中标记（未跑子进程）"
    )
    log(
        f"replay 转换完成：{len(shard_dirs)} shard（复用 {reused}），"
        f"{sec:.1f}s runtime={runtime_name} workers={n_workers} · {spread} · "
        f"{'verify-only ' if verify_only else ''}converter={conv_sha[:12]}…"
    )
    return {
        "shards": len(shard_dirs),
        "reused": reused,
        "sec": round(sec, 1),
        "converter_sha256": conv_sha,
        "runtime": runtime_name,
        "convert_workers": n_workers,
        # D10（plan/replay-corpus.plan.md）：iteration `wire` 子字典的新字段。
        "corpus_fmt": "replay",
        "replay_bytes": replay_bytes,
        "labels_bytes": labels_bytes,
        "convert_jobs": len(shard_dirs) - reused,
        # 主运行时 = bun（见 convert_fallback_flag）。
        "convert_fallback": convert_fallback_flag(runtime_name),
    }


def verify_corpus_format(shard_dirs: Sequence[Path | str], manifest: dict) -> None:
    """解包后、训练前的一次对齐体检：**manifest 自述的语料格式 vs payload 里的真身**。

    ★ 2026-11-20（kaggle 真机：`[ppo] no RL shards found` 连炸 5 次、整段会话 RC=1）：
    发布端 merge 丢了一行 `PublishSpec(corpus_fmt=…)` ⇒ payload 按 replay 瘦掉了 obs，
    manifest 里却没有 `corpus_fmt`（缺键 = full）⇒ worker 走 full 的路去读一份**没有 obs**
    的语料。那道伤只在训练核里才炸（`discover_rl_shards` 返回空 → `SystemExit`），
    也就是最深的、最说不清起因的那一层。

    这里只 stat 每个 shard 的根、只看两个指标：`obs.npy`（full）与 `replay.json`（replay）。
    对不上 = **内容决定性失败**（同一份字节重领也在同一处炸）⇒ `ProtocolError` ⇒
    `report_job_failure` ⇒ hub 终局 ⇒ 训练侧带原因停腿；worker 继续轮询。

    只在 `kind=ppo` 上判：replay 转换本来就只允许 ppo（见 `prepass` 的 kind 闸），
    别的 kind 的 payload 不承诺语料格式（bc 自采集、iter 由节点自跑）。

    **零 shard 也是 ppo 的不合法形状**（2026-11-20 追查）：发布端
    `trainer/loop_transport._gate_round_shards` 对本机轮硬门「无完整 shard 不许发布」，
    而 node 轮（`rollout_spec` 非空）发的是 `kind=iter`、shard 集**必须为空** ⇒ 生产上
    不存在「合法零 shard 的 ppo job」（`--echo` 冒烟也不靠它：`hub/smoke_loopback` 造的是
    真 shard）。到这儿还是空 = 坏包，而训练核见到它只会 `discover_rl_shards` 返回空。
    特例：`corpus_fmt=replay` 的空集交给 `convert_replay_corpus` 自己拒（它有「不训空」
    那一行，措辞与读数对 replay 更准）。
    """
    if str(manifest.get("kind") or "") != "ppo":
        return
    dirs: list[Path] = [p if isinstance(p, Path) else Path(p) for p in shard_dirs]
    declared = str(manifest.get("corpus_fmt") or "full")
    if not dirs:
        if declared == "replay":
            return  # `convert_replay_corpus` 的「没有任何 shard —— 不训空」那一行
        raise ProtocolError(
            "kind=ppo 的 payload 里没有任何 shard 目录"
            f"（corpus_fmt={declared}）——训练核只会读到 0 局，拒收"
        )
    if declared == "replay":
        missing = [d for d in dirs if not (d / REPLAY_NAME).is_file()]
        if missing:
            raise ProtocolError(
                f"corpus_fmt=replay 但 {len(missing)}/{len(dirs)} 个 shard 没有 {REPLAY_NAME}"
                f"（manifest 与 payload 不是同一档；首个：{missing[0].name}）——拒收"
            )
        return
    missing = [d for d in dirs if not (d / OBS_NAME).is_file()]
    if missing:
        hint = (
            f"（这些 shard 带 {REPLAY_NAME} —— payload 其实按 replay 打包了）"
            if (missing[0] / REPLAY_NAME).is_file()
            else ""
        )
        raise ProtocolError(
            f"manifest 自述 corpus_fmt={declared} 但 {len(missing)}/{len(dirs)} 个 shard 没有"
            f" {OBS_NAME}{hint}——manifest 与 payload 不是同一档，拒收（重发同一轮才会好）"
        )


def prepass(
    job_dir: str | Path,
    # `Sequence` 而不是 `list[Path | str]`（2026-11-20）：`list` 不变性让 `list[Path]` 的调用方
    # （单测 / `convert_shard`）mypy 全红——与 `convert_replay_corpus` 同一个口径。
    shard_dirs: Sequence[Path | str],
    manifest: dict,
    *,
    log: Callable[[str], None] = lambda _m: None,
    bundle: LogBundle | None = None,
    pool_factory: Callable[[str, Path, int], ConvertPool | None] | None = None,
    workers: int | None = None,
    verify_only: bool = False,
) -> ConvertReadout | None:
    """`run_job` 的 replay pre-pass 入口：非 replay 原样返回 None（零行为变化）。

    kind 闸在这里而不是散在宿主里：replay 只允许 kind=ppo（节点自跑 rollout 的 kind=iter
    强制 full，同机没有 wire 收益）。

    ★ 2026-11-20：**先进体检**（`verify_corpus_format`）再分档 —— 非 replay 的 job 也
    要过一遍「manifest 自述与 payload 真身对齐吗」，否则 format 对不上只会一路走到训练核
    才炸（`[ppo] no RL shards found`，弱到把整段云机会话带走）。
    """
    verify_corpus_format(shard_dirs, manifest)
    if str(manifest.get("corpus_fmt") or "full") != "replay":
        return None
    if str(manifest.get("kind")) != "ppo":
        raise ProtocolError(
            f"corpus_fmt=replay 只支持 kind=ppo，收到 kind={manifest.get('kind')!r}"
            "——kind=iter 的节点自跑 rollout 强制 full（plan/replay-corpus.plan.md D2）"
        )
    got = convert_replay_corpus(
        job_dir,
        shard_dirs,
        manifest,
        log=log,
        pool_factory=pool_factory,
        workers=workers,
        verify_only=verify_only,
    )
    if bundle is not None:
        # 日志节食（短口径）：**运行时也进这一行** —— worker 日志里「这轮转换用的是 bun 还是
        # node」与耗时是同一件事的两个面，分开写就一定会有人只读到一半。
        bundle.add(
            "replay 转换",
            f"{got['sec']}s {got['runtime']} → {got['shards']} shard（复用 {got['reused']}）",
        )
    return got


def converter_sha(path: str | Path) -> str:
    """预构建转换器的字节 sha256（缺失/空路径 = 响亮拒发）。"""
    if not str(path or "") or not Path(path).is_file():
        raise ProtocolError(
            f"corpus_fmt=replay 必须带 trainer 预构建的转换器（收到 {path!r}）——拒发"
        )
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def attach_converter(
    extra_files: list[Path], tmp_dir: str | Path, path: str | Path
) -> str:
    """把预构建转换器放进 payload 根（`publish_job` 的 extra_files），返回 sha256。"""
    raw = Path(path).read_bytes()
    copy = Path(tmp_dir) / CONVERTER_NAME
    copy.write_bytes(raw)
    extra_files.append(copy)
    return hashlib.sha256(raw).hexdigest()


__all__ = [
    "CONVERTER_NAME",
    "MARKER_NAME",
    "OBS_NAME",
    "REPLAY_NAME",
    "attach_converter",
    "convert_replay_corpus",
    "convert_shard",
    "convert_workers",
    "converter_sha",
    "prepass",
    "resolve_js_runtime",
    "verify_converter",
    "verify_corpus_format",
]
