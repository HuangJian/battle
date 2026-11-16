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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from common.errors import ProtocolError
from common.platform_utils import effective_cores, rmtree_best_effort
from common.proc import run_capture

CONVERTER_NAME = "replay_converter.js"
MARKER_NAME = "replay_converted.json"
MARKER_VERSION = 1
#: 单 shard 转换上界（秒）。一局最多 ~36000 tick；实测 600 tick 约 60ms，
#: 36000 tick 约 3.6s；900s 是「机器假死」与「真慢」的分界。
SHARD_TIMEOUT_SEC = 900.0


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
    tmp: Path, *, converter: Path, runtime: str, timeout_sec: float, label: str
) -> None:
    """一次性 spawn 转换一个事务目录（池不可用/池回退时的唯一路径）。"""
    # 绝对路径：一次性路径继承调用方 cwd，池里的 worker 的 cwd 是 **bundle 所在目录**
    # （`ServePool._spawn` 用 ts_dir 当 cwd）⇒ 相对路径在池里必然解析错。
    p = run_capture(
        [runtime, str(converter), "--shard-dir", str(Path(tmp).resolve())],
        timeout=timeout_sec,
    )
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
    try:
        run(tmp)
        _finish_shard(shard_dir, tmp, converter_sha)
        return False
    except subprocess.TimeoutExpired as e:
        rmtree_best_effort(tmp, ignore_errors=True)
        raise ProtocolError(
            f"replay 转换超时（{shard_dir.name}，>{timeout_sec:.0f}s）—— 确定性失败，不重试"
        ) from e
    except OSError as e:
        rmtree_best_effort(tmp, ignore_errors=True)
        raise ProtocolError(f"replay 转换器起不来（{runtime}）：{e}") from e
    except Exception:
        rmtree_best_effort(tmp, ignore_errors=True)
        raise


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


def convert_workers(requested: int | None = None, shards: int | None = None) -> int:
    """转换承载宽度：显式值优先；缺省 **用完本进程所有可用核**（`effective_cores()`）。

    两个约束决定了这个口径（用户 2026-11-16 两次点明）：
      1. **GPU 云机的 CPU 核数是浮动的** —— 低配 2 物理 / 4 逻辑、中配 4/8、高配 24。写死 2 在高配上
         白扔一个数量级；
      2. **转换腿不与他人共享 CPU** —— 它是训练前的 pre-pass，跑完就结束，同机没有 rollout/eval
         在并行（那两条腿才需要留核给数据回传，所以它们走 `cpu_worker_slots()`）。这里留核 =
         纯白等墙钟。

    为什么不用 `cpu_worker_slots()`：那条口径是「rollout 与 eval 交替、给回传留两三个核」的槽位，
    属于**并发腿之间**的分配；转换是单腿独占阶段，直接 `effective_cores()`（读 cgroup 配额与亲和掩码，
    不采信宿主机的 `os.cpu_count()`）。

    `shards` 给了就按 `min(cores, shards)` 收敛 —— 3 个 shard 时起 19 个 worker 只是多付启动。

    实测校准（Kaggle 2 物理 / 4 逻辑，350 shard）：`w=2` 22.9 s、`w=4` 21.6 s（超线程只值 6%，
    但仍不该留核）；本机 8 物理 / 16 逻辑、60 shard：`w=2` 2.8 s、`w=14` 2.3 s。单 worker 常驻
    ~120 MB（一次只持一个 shard），19 worker ≈ 2.3 GB，相对 GPU 机内存可忽略。
    """
    n = max(1, int(requested)) if requested is not None else max(1, int(effective_cores()))
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
    shard_dirs: list[Path],
    manifest: dict,
    *,
    log: Callable[[str], None] = lambda _m: None,
    workers: int | None = None,
    pool_factory: Callable[[str, Path, int], Any] | None = None,
) -> dict[str, Any]:
    """把一整轮 replay shard 就地转成 obs/scalars/extra，返回读数。

    `pool_factory(runtime, converter, workers)` = 长驻转换池的工厂，**由调用方注入**
    （生产 = `worker.serve_pool.pool_for_bundle`）。为什么不在这里直接 import：
    `remote/` 不能依赖 `worker/` —— `tests/test_remote_dag.py` 按 import 边算拓扑秩，
    本模块被 `hub_client`(L1) 与 `download`(L3) 依赖，抬高自己的秩会连坐整串。
    缺省 None = 逐 shard 一次性 spawn（行为与接池前逐字节相同）。
    """
    if not shard_dirs:
        raise ProtocolError("corpus_fmt=replay 但 payload 里没有任何 shard——拒收（不训空）")
    converter, conv_sha = verify_converter(job_dir, manifest)
    runtime = resolve_js_runtime()
    n_workers = convert_workers(workers, len(shard_dirs))
    t0 = time.time()
    reused = 0
    # D10 墙钟账（additive）：replay/labels 的真实字节在转换前量（转换只增不减）。
    replay_bytes, labels_bytes = _corpus_bytes(shard_dirs)

    pool = pool_factory(runtime, converter, n_workers) if pool_factory is not None else None

    def _submit_via_pool(tmp: Path, label: str) -> None:
        """池里跑一次；池给不出确定成功 ⇒ 回退一次性 spawn（**只慢不错**）。"""
        argv = [str(converter), "--shard-dir", str(Path(tmp).resolve())]
        got = (
            pool.try_capture(argv, SHARD_TIMEOUT_SEC, label=label, kind="conv", where=str(tmp))
            if pool is not None
            else None
        )
        if got is None:
            _run_oneshot(
                tmp, converter=converter, runtime=runtime, timeout_sec=SHARD_TIMEOUT_SEC, label=label
            )

    def _one(sd: Path) -> bool:
        return _convert_tx(
            sd,
            converter=converter,
            converter_sha=conv_sha,
            runtime=runtime,
            timeout_sec=SHARD_TIMEOUT_SEC,
            submit=lambda tmp: _submit_via_pool(tmp, sd.name),
        )

    try:
        n = max(1, min(n_workers, len(shard_dirs)))
        with ThreadPoolExecutor(max_workers=n) as ex:
            for got in ex.map(_one, shard_dirs):
                if got:
                    reused += 1
    finally:
        if pool is not None:
            log(pool.summary())
            pool.close()
    sec = time.time() - t0
    runtime_name = Path(runtime).name
    log(
        f"replay 转换完成：{len(shard_dirs)} shard（复用 {reused}），"
        f"{sec:.1f}s runtime={runtime_name} workers={n_workers} converter={conv_sha[:12]}…"
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


def prepass(
    job_dir: str | Path,
    shard_dirs: list[Path],
    manifest: dict,
    *,
    log: Callable[[str], None] = lambda _m: None,
    bundle: Any = None,
    pool_factory: Callable[[str, Path, int], Any] | None = None,
    workers: int | None = None,
) -> dict[str, Any] | None:
    """`run_job` 的 replay pre-pass 入口：非 replay 原样返回 None（零行为变化）。

    kind 闸在这里而不是散在宿主里：replay 只允许 kind=ppo（节点自跑 rollout 的 kind=iter
    强制 full，同机没有 wire 收益）。
    """
    if str(manifest.get("corpus_fmt") or "full") != "replay":
        return None
    if str(manifest.get("kind")) != "ppo":
        raise ProtocolError(
            f"corpus_fmt=replay 只支持 kind=ppo，收到 kind={manifest.get('kind')!r}"
            "——kind=iter 的节点自跑 rollout 强制 full（plan/replay-corpus.plan.md D2）"
        )
    got = convert_replay_corpus(
        job_dir, shard_dirs, manifest, log=log, pool_factory=pool_factory, workers=workers
    )
    if bundle is not None:
        bundle.add(
            "replay 转换",
            f"{got['sec']}s → {got['shards']} shard（复用 {got['reused']}）",
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
    "attach_converter",
    "convert_replay_corpus",
    "convert_shard",
    "convert_workers",
    "converter_sha",
    "prepass",
    "resolve_js_runtime",
    "verify_converter",
]
