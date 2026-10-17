"""collect-only 采样子进程（T4 双缓冲）——2026-09-02 从 trainer/run_rl.py 拆出。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import common.distribution
from biz.course import build_pairs
from common.log import log
from common.platform_utils import popen_kwargs
from trainer.dispatch_queue import (
    REPO_ROOT,
    RUN_ID,
    local_slots_max_of,
    run_rollout,
    run_rollout_queue,
)


def run_collect_only(args, traj_root, rotate_seed, bun) -> None:
    """吞吐 T4：仅采集一轮落盘后退出（双缓冲子进程模式）。不 PPO/不 eval/不写权重。
    落盘 shard 的 manifest.wver = 快照权重指纹 → 主进程下一轮 completed_pairs 命中走聚合。
    --precollect-games>0 时只采前 N 局（下一轮首波 wave 语料，其余由下轮以 θ_N 现场采）。
    --precollect-samples>0 时按样本量提前 halt（样本数达标即停，无需等满 N 局）。
    """
    import threading as _threading

    # D14 语料血缘：预采 shard 也带 course_fp（manifest 由 exporter 写入，
    # resume 对账按 course_fp 过滤——旧课程 shard 不混入新课程预采目录）。
    # 定义自 S4 第十九刀起住 `trainer/loop_lifecycle.py`；仍**延迟** import：本模块是预采子进程
    # 的入口，启动期不拉主循环那条链（torch-free / 最小 import 面）。
    from trainer.loop_lifecycle import _course_file_fp

    it = args.start_it or 1
    traj_dir = traj_root / f"it{it}"
    traj_dir.mkdir(parents=True, exist_ok=True)
    pairs = build_pairs(args, it, rotate_seed)
    pre_games = int(getattr(args, "precollect_games", 0) or 0)
    pre_samples = int(getattr(args, "precollect_samples", 0) or 0)
    halt_event: _threading.Event | None = None
    if 0 < pre_games < len(pairs):
        pairs = pairs[:pre_games]
        log(
            f"[collect-only] it{it}: limited to first {pre_games} games "
            f"(rest collected by next round with θ_N)"
        )
    elif pre_samples > 0 and len(pairs) > 0:
        # 按样本量 halt：不截断 pairs，用 halt_event 在累计样本达标时提前停采。
        # 主进程的轮询逻辑在检测到足够 shard 后也会放行，两者互补。
        halt_event = _threading.Event()
        log(
            f"[collect-only] it{it}: {len(pairs)} pairs, target_samples={pre_samples}, "
            f"halt when reached (subprocess exits early, excess collected by next round)"
        )
    dist_cfg = common.distribution.load_dist_config()
    # iter_id 必须遵循 "{RUN_ID}.{it}" —— run_rollout_queue 用 rsplit('.',1)[-1]
    # 做 int() 解析迭代号（2026-08-31 实测：'collect-1-<pid>' 格式直接 ValueError
    # 崩崩，分布式路径的子进程从未成功采集过一轮，等于双缓冲静默失效）。
    iter_id = f"{RUN_ID}.{it}"
    log(f"[collect-only] it{it}: {len(pairs)} pairs -> {traj_dir} (weights={args.bc})")
    if halt_event:
        # 样本量提前 halt 模式：on_result 累计样本，达标即 set halt_event。
        # 在途局自然收尾，子进程退出，主进程 wait() 返回。
        _pre_samples_acc = [0]

        def _on_result(summary):
            s = summary.get("totalSamples", 0) or summary.get("samples", 0) or 0
            _pre_samples_acc[0] += s
            if _pre_samples_acc[0] >= pre_samples:
                halt_event.set()

        if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
            report = run_rollout_queue(
                bun,
                args.bc,
                traj_dir,
                pairs,
                args,
                dist_cfg,
                iter_id,
                on_result=_on_result,
                halt_event=halt_event,
                # 本机直跑槽位（2026-09-09 补齐：此前漏传 ⇒ rl.local_slots 失效、
                # local 恒按 workers 满额并发）。0 = 关闭本机直跑（远端兜底保留）。
                local_slots_max=local_slots_max_of(args),
                course_fp=_course_file_fp(args),
            )
        else:
            report = run_rollout(bun, args.bc, traj_dir, pairs, args)
    else:
        if dist_cfg and any(n.get("enabled", True) for n in dist_cfg.get("nodes", [])):
            report = run_rollout_queue(
                bun,
                args.bc,
                traj_dir,
                pairs,
                args,
                dist_cfg,
                iter_id,
                local_slots_max=local_slots_max_of(args),
                course_fp=_course_file_fp(args),
            )
        else:
            report = run_rollout(bun, args.bc, traj_dir, pairs, args)
    log(
        f"[collect-only] it{it}: games={report['games']} samples={report['totalSamples']} "
        f"outcomes={json.dumps(report['outcomes'])}"
    )


#: 预采子进程的入口：`run_rl.py` —— **唯一**拥有 `--collect-only` 语义的那个入口。
#:
#: ★ 2026-10-17（plan/single-resident-trainer §7-P0-3）：这里原先写 `os.path.abspath(__file__)`
#: （= **本模块**），而本模块没有 `__main__` ⇒ 子进程零输出 exit 0 —— 预采静默零产出，而
#: `precollect_ready` 因「子进程已退出」立刻为真 ⇒ 不卡、也不采（静默失效，最难查的那一种）。
RUN_RL_ENTRY = Path(__file__).resolve().with_name("run_rl.py")


def collect_child_spec(
    args, snap: str, next_it: int, pre_samples: int, cfg_path: str
) -> tuple[list[str], dict] | None:
    """预采子进程的 `(argv, 动作覆盖 JSON)`。**纯函数**（无 I/O，可单测）。

    为什么子进程不再继承父进程的 argv（`*sys.argv[1:]`）：两种父形态里它都是错的 ——
    ① serve（`run_rl_cluster.py --serve …`）的 argv 里根本没有课程参数，子进程重建不出命名空间；
    ② 就算父进程是 per-course `run_rl`，课程那些键（`bc`/`iters`）在 `apply_course` 之后会被
    课程值**覆盖**、显式 CLI 还会被 `course_cli_conflicts` **拒启**（实跑：exit 1，两条冲突）。
    ⇒ 子进程只带 `--course`（单一事实来源），动作键（collect_only/bc/start_it/precollect_*）
    走 `--collect-only-config` 这条**课程之后**的通道（`worker/course_args.apply_collect_only_config`）。

    返回 None = 这条腿跑不了：无课程的老调用没有被课程定义的命名空间，拿错快照比不预采更糟。
    """
    course = str(getattr(args, "course_path", "") or getattr(args, "course", "") or "")
    if not course:
        return None
    payload = {
        "collect_only": 1,
        "bc": snap,
        "start_it": int(next_it),
        "precollect_games": int(getattr(args, "precollect_games", 0) or 0),
        "precollect_samples": int(pre_samples or 0),
    }
    argv = [
        sys.executable,
        "-u",
        str(RUN_RL_ENTRY),
        "--course",
        course,
        "--collect-only-config",
        cfg_path,
    ]
    return argv, payload


def spawn_collect_next(args, it, snap_src: str | None = None) -> subprocess.Popen | None:
    """吞吐 T4：后台子进程预采 it+1（行为快照）。返回 Popen 或 None。

    正确性：子进程用 args.out（或 snap_src，提前预采时=epoch3 快照）的**快照**——下轮
    PPO 若写回 args.out 不回影响子进程（它读 snap 文件）；shards 自带快照权重的 lp →
    IS 分母天然正确（快照≈θ_N 时仍 on-policy 带内，差最后一段梯度）。子进程同样继承
    --precollect-games：>0 则只采下一轮首波，其余局由下轮以 θ_N 现场采（严格 on-policy）。
    --precollect-samples 自动从 training_log 上一轮实测数据计算：wave_games × avg_sppg × 1.5。
    """

    next_it = it + 1
    if args.iters > 0 and next_it > args.iters:
        return None
    src = snap_src or args.out
    snap = str(Path(args.out).with_name(f"weights-collect-{next_it}.json"))
    if os.path.abspath(src) != os.path.abspath(snap):
        try:
            shutil.copyfile(src, snap)
        except OSError as e:
            log(f"[double-buffer] snapshot fail: {e} — skip precollect")
            return None
    # 提前预采（snap_src 已由调用方 save_weights_json 写好目标文件）：src==snap，
    # 跳过 copy 直接复用——否则 copyfile(自己→自己) 抛 same-file 错误。
    elif snap_src is not None:
        log(f"[double-buffer] early snapshot already at {snap} — reuse")

    # 动态计算 precollect_samples：从 training_log 上一轮实测 avg_sppg × wave_games × 1.5
    pre_samples = 0
    jsonl_path = Path(args.traj) / "training_log.jsonl"
    if jsonl_path.exists():
        try:
            with open(jsonl_path) as _f:
                _lines = [line for line in _f if line.strip()]
            if _lines:
                _last = json.loads(_lines[-1])
                _last_samples = _last.get("samples", 0)
                _last_games = _last.get("expectedGames", 1)
                if _last_samples > 0 and _last_games > 0:
                    _avg_sppg = _last_samples / _last_games
                    _cfg = common.distribution.load_dist_config()
                    if _cfg is None:
                        _cfg = {}
                    _wave = max(4, int(_cfg.get("policy", {}).get("streamWaveGames", 12)))
                    pre_samples = int(_wave * _avg_sppg * 1.5)
                    log(
                        f"[double-buffer] precollect it{next_it}: auto-calc precollect_samples="
                        f"{pre_samples} (wave={_wave} × avg_sppg={_avg_sppg:.1f} × 1.5)"
                    )
        except Exception as _e:
            log(
                f"[double-buffer] precollect it{next_it}: calc failed ({_e}), "
                f"fallback to --precollect-games"
            )

    # 动作覆盖的落点：**系统临时目录**（不进仓 `tmp/`：`.json` 不被 `tools/tmp-clean.py` 的
    # LOG_SUFFIXES 扫，堆在那里没人管；系统 tmp 由 OS 兜底）。路径进 argv ⇒ `ps` 里看得见
    # 子进程是「预采」而不是「常驻训练」（这对一条曾被误认为常驻写者的腿很重要）。
    try:
        tmp_dir = tempfile.mkdtemp(prefix=f"precollect-{next_it}-")
    except OSError as e:
        log(f"[double-buffer] precollect config fail: {e} — skip precollect")
        return None
    cfg_path = os.path.join(tmp_dir, "collect-only.json")
    spec = collect_child_spec(args, snap, next_it, pre_samples, cfg_path)
    if spec is None:
        log("[double-buffer] no course in args — skip precollect（重建不出子进程命名空间）")
        _unlink_best_effort(cfg_path)
        return None
    argv, payload = spec
    try:
        with open(cfg_path, "w", encoding="utf-8") as _f:
            json.dump(payload, _f, ensure_ascii=False)
    except OSError as e:
        log(f"[double-buffer] precollect config fail: {e} — skip precollect")
        _unlink_best_effort(cfg_path)
        return None
    # 子进程的 stdout/stderr **落文件**（不再 DEVNULL）：这条腿曾以「零输出 exit 0」静默失效
    # 几个月（评审 P0-3）——失败必须留痕。`.log` 在 tmp-clean 的 LOG_SUFFIXES 里，会被扫。
    log_path = Path(args.traj) / f"precollect-{next_it}.log"
    # ★ 2026-10-17：手写 `0x08000000` 改成走唯一原语（`popen_kwargs` = `POPEN_NO_WINDOW` + 扩展）。
    # 手写魔数的形状正是「别处漏了就复发」的那一种：没有名字可以 grep、没有守卫认得出来。
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as lf:
            p = subprocess.Popen(
                argv,
                cwd=str(REPO_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=lf,
                stderr=subprocess.STDOUT,
                **popen_kwargs(),
            )
    except Exception as e:
        log(f"[double-buffer] spawn fail: {e} — skip precollect")
        _unlink_best_effort(cfg_path)
        return None
    log(
        f"[double-buffer] precollect it{next_it} spawned pid={p.pid} snapshot={snap} "
        f"log={log_path}" + (f" (early, src={snap_src})" if snap_src else "")
    )
    return p


def _unlink_best_effort(path: str) -> None:
    """清掉动作覆盖的临时目录（失败无所谓：系统 tmp 会兜底；这里只为不白堆）。"""
    try:
        os.unlink(path)
    except OSError:
        pass
    try:
        os.rmdir(os.path.dirname(path))
    except OSError:
        pass


def precollect_snapshot_wver(out_path: str, it: int) -> str | None:
    """回读上一轮提前预采（epoch3 快照）写入的 weights-collect-{it}.json 的 wver。

    提前预采时快照=θ_{N,e3}（尚未最终写回 args.out），其 shard 的 wver=快照指纹；
    本轮对账需把该 wver 也纳入 done 判定（双白名单），否则预采首波被当作未完成清场。
    文件存在才返回（无提前预采/常规尾部预采都返回 None——尾部预采时 args.out 已是 θ_N，
    wver 与当前一致，无需双白名单）。"""
    try:
        snap = Path(out_path).with_name(f"weights-collect-{it}.json")
        if not snap.exists():
            return None
        return common.distribution.weights_fingerprint(str(snap))
    except OSError:
        return None
