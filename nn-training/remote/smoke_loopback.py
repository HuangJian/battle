"""remote/smoke_loopback.py — M0d/M1 假云回环冒烟（plan/remote-ppo-architecture.md §9）。

在**本机**模拟「云 hub-LAN 协同训练」完整链路（不碰真实隧道/真实 GPU）：

  1. p1-ep60 权重（weights/battle-p1bc-ep60.zip → battle2-p1bc/run/weights.json）
     解压为 init_weights.json（warm-start，D12）；
  2. 按 p4-onset 课程（curricula/p4-onset.jsonc）合成 2 个结构合法的 per-tick shard
     （obs/scalars/a_move/a_fire/lp_move/lp_fire/value/metrics/done/mask + manifest
     带 wver/score/outcome/course_fp/metrics_version——与 export-rl-rollout.ts 同规）；
  3. 起旁路 hub-server（stdlib http.server，磁盘 IPC + jsonl 账本）；
  4. hub 侧 publish_job（磁盘 IPC 发布 job：payload.zip + manifest + job_pending）——
     即 TrainingLoop._remote_ppo 的发布路径；
  5. 本机起云 worker 进程（`python -m remote_worker --poll ... --once`）——即云端
     GPU 机器的无状态轮询 worker，真跑 PPO（小 epochs）→ POST 结果；
  6. hub 侧 wait_job → 三重校验（init_weights_fp/data_fp/commit）→ verify_and_land
     落位 args.out + ppo_ckpt_remote；
  7. M0d：记录「发布 → 云拉 → 下载 → PPO → POST → 校验落位」整趟墙钟（非隧道部分）。

退出码：0 = 全链路通过；非 0 = 失败（响亮报错）。运行（venv，含 torch）：
  bash nn-training/start-training.sh --script remote/smoke_loopback.py
  pwsh -ExecutionPolicy Bypass -File nn-training/start-training.ps1 -Script remote/smoke_loopback.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # nn-training/
REPO = ROOT.parent  # git 根
sys.path.insert(0, str(ROOT))

from remote.hub_client import (
    git_head,
    iter_shard_dirs,
    publish_job,
    verify_and_land,
    wait_job,
)
from remote.hub_server import _JobStore, make_server
from rl.config import load_course
from rl.reward_library import METRICS_VERSION

# ------------------------------------------------------------------ shard 合成

_METRIC_IDX = {
    "ticks": 0,
    "kills": 1,
    "deaths": 2,
    "playerHits": 3,
    "playerShots": 4,
    "enemyHits": 5,
    "baseAlive": 6,
    "baseWallTotal": 7,
    "baseWallIntact": 8,
    "stuckTicks": 9,
    "lives": 10,
    "firstKillTick": 11,
    "enemyTotal": 12,
    "timeoutTick": 13,
    "playerX": 14,
    "playerY": 15,
    "playerDir": 16,
    "playerSpeed": 17,
    "playerLv": 18,
    "enemyAlive": 19,
    "frame": 20,
}


def _synthetic_metrics(n: int, seed: int = 7):
    """n 决策行 + 1 终局行（21 维），值域贴合 p4-onset 公式的语义（不触发公式守卫）。"""
    import numpy as np

    rng = np.random.default_rng(seed)
    m = np.zeros((n + 1, 21), dtype=np.float64)
    m[:, _METRIC_IDX["ticks"]] = np.arange(n + 1) * 10.0
    kills = np.cumsum(rng.integers(0, 2, n + 1)).astype(np.float64)
    m[:, _METRIC_IDX["kills"]] = kills
    m[:, _METRIC_IDX["lives"]] = 1.0
    m[:, _METRIC_IDX["playerShots"]] = rng.integers(0, 4, n + 1)
    m[:, _METRIC_IDX["enemyHits"]] = rng.integers(0, 2, n + 1)
    m[:, _METRIC_IDX["playerHits"]] = rng.integers(0, 2, n + 1)
    m[:, _METRIC_IDX["stuckTicks"]] = 0.0
    m[:, _METRIC_IDX["enemyTotal"]] = 4.0
    m[:, _METRIC_IDX["baseAlive"]] = 1.0
    m[:, _METRIC_IDX["baseWallTotal"]] = 8.0
    m[:, _METRIC_IDX["baseWallIntact"]] = 8.0
    m[:, _METRIC_IDX["firstKillTick"]] = -1.0
    return m


def _write_synthetic_shard(
    dirpath: Path, stage: int, seed: int, wver: str, course_fp: str, n: int = 64
) -> None:
    """合成一个 PPO 可装载 shard（与 export-rl-rollout.ts 同文件名/同规）。"""
    import numpy as np

    d = dirpath
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed * 131 + 7)
    np.save(d / "obs.npy", rng.integers(0, 256, (n, 14, 26, 26), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.random((n, 19), dtype=np.float32))
    np.save(d / "a_move.npy", rng.integers(0, 5, n).astype(np.int64))
    np.save(d / "a_fire.npy", rng.integers(0, 2, n).astype(np.int64))
    np.save(d / "lp_move.npy", (rng.random(n) - 2).astype(np.float32))
    np.save(d / "lp_fire.npy", (rng.random(n) - 2).astype(np.float32))
    np.save(d / "value.npy", (rng.random(n) * 0.1).astype(np.float32))
    np.save(d / "metrics.npy", _synthetic_metrics(n, seed=seed))
    np.save(d / "done.npy", np.zeros(n, dtype=np.int64))
    np.save(d / "mask.npy", np.ones((n, 7), dtype=np.int64))
    (d / "manifest.json").write_text(
        json.dumps(
            {
                "stage": stage,
                "seed": seed,
                "wver": wver,
                "nSamples": n,
                "ticks": n * 10,
                "outcome": "stage_clear",
                "score": 0.5,
                "course_fp": course_fp,
                "metrics_version": METRICS_VERSION,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="remote PPO loopback smoke (M0d/M1)")
    ap.add_argument("--work", default="tmp/remote-smoke", help="冒烟工作目录（仓库内，gitignore）")
    ap.add_argument("--token", default="smoke-token")
    ap.add_argument("--epochs", type=int, default=2, help="云 worker PPO epochs（小值提速）")
    ap.add_argument("--iters", type=int, default=2, help="合成 shard 数（每 shard 64 决策步）")
    args = ap.parse_args()

    import numpy as np  # noqa: F401 — 确保 import 顺序稳定

    work = ROOT / args.work
    if work.exists():
        shutil.rmtree(work)  # 冒烟目录可整体重建（非训练产物）
    work.mkdir(parents=True)
    def log(msg: str) -> None:
        print(f"[smoke] {msg}", flush=True)

    # ---- 0) 课程 + course_fp（D14：sha256 of 课程文件字节） ----
    course_path = ROOT / "curricula" / "p4-onset.jsonc"
    course_bytes = course_path.read_bytes()
    course_fp = hashlib.sha256(course_bytes).hexdigest()
    course = load_course(str(course_path))
    log(f"course p4-onset loaded: reward={course.reward.formula!r}")

    # ---- 1) p1-ep60 权重 → init_weights.json（warm-start，D12） ----
    zip_path = ROOT / "weights" / "battle-p1bc-ep60.zip"
    init_w = work / "init_weights.json"
    with zipfile.ZipFile(zip_path) as z:
        z.extract("battle2-p1bc/run/weights.json", work)
    shutil.copyfile(work / "battle2-p1bc" / "run" / "weights.json", init_w)
    init_weights_fp = hashlib.sha256(init_w.read_bytes()).hexdigest()
    log(f"init weights p1-ep60 -> {init_w} ({init_w.stat().st_size} bytes, fp={init_weights_fp[:12]}…)")

    # ---- 2) 合成 shard（traj/it1/rl_s2000_seed{...}——p4 自定义关 ID 2000） ----
    traj_dir = work / "traj"
    it1 = traj_dir / "it1"
    wver = init_weights_fp  # 与 rollout 对账口径一致：wver = 发布时 init 权重指纹
    for i in range(max(1, args.iters)):
        _write_synthetic_shard(it1 / f"rl_s2000_seed{i}", 2000, i, wver, course_fp)
    shard_dirs = iter_shard_dirs(str(traj_dir), 1)
    assert len(shard_dirs) == max(1, args.iters), f"iter_shard_dirs 找到 {len(shard_dirs)}"
    log(f"synthetic shards: {[d.name for d in shard_dirs]}")

    # ---- 3) 起旁路 hub-server（假云；磁盘 IPC + jsonl 账本） ----
    store = _JobStore(work / "jobs", work / "training_log.jsonl")
    srv = make_server(store, 0, args.token, host="127.0.0.1")
    port = srv.server_address[1]
    import threading

    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    hub_url = f"http://127.0.0.1:{port}"
    log(f"hub-server (fake cloud) on {hub_url}")

    # ---- 4) hub 发布 job（磁盘 IPC，TrainingLoop._remote_ppo 同路径） ----
    commit = git_head(REPO)
    m = publish_job(
        job_root=work / "jobs",
        jsonl_path=str(work / "training_log.jsonl"),
        run_id="smoke-loopback",
        it=1,
        traj_dir=str(traj_dir),
        shard_dirs=shard_dirs,
        init_weights_path=str(init_w),
        ckpt_remote_dir=None,
        commit=commit,
        course=course_bytes.decode("utf-8"),
        course_fp=course_fp,
        reward_formula=course.reward.formula,
        formula_hash=course.reward_spec().identity(),
        metrics_version=METRICS_VERSION,
        gamma=float(course.gamma),
        lam=float(course.lam),
        mode="per-tick",
        epochs=args.epochs,
        mb=512,
        lr=float(course.lr),
        kl_coef=0.0,
        kl_cap=None,
        adv_norm="auto",
        shuffle=True,
        schedule_raw=course.ppo_schedule_dicts(),
        log=log,
    )
    jid = m["job_id"]
    log(f"job published: {jid} (data_fp={m['data_fp'][:12]}… payload={m['payload_sha256'][:12]}…)")

    # ---- 5) 本机起云 worker（无状态轮询；--once 处理一个 job 后退出） ----
    t0 = time.time()
    worker_env = dict(os.environ)
    worker_env["PYTHONPATH"] = str(ROOT) + os.pathsep + worker_env.get("PYTHONPATH", "")
    worker_cmd = [
        sys.executable,
        "-m",
        "remote_worker",
        "--poll",
        hub_url,
        "--token",
        args.token,
        "--out",
        str(work / "worker"),
        "--once",
        "--poll-sec",
        "1",
    ]
    log(f"starting cloud worker: {' '.join(worker_cmd)}")
    r = subprocess.run(worker_cmd, cwd=str(ROOT), env=worker_env, timeout=600)
    if r.returncode != 0:
        log(f"FATAL: cloud worker exited rc={r.returncode}")
        return 1

    # ---- 6) hub 等待 → 三重校验 → 落位 ----
    result = wait_job(hub_url, args.token, jid, timeout_sec=300, poll_sec=1, log=log)
    out_weights = work / "weights.json"
    verify_and_land(
        result,
        m,
        init_weights_path=str(init_w),
        traj_dir=str(traj_dir),
        it=1,
        out_weights=str(out_weights),
        log=log,
    )
    t_total = time.time() - t0

    # ---- 7) 断言 ----
    out_bytes = out_weights.read_bytes()
    assert out_bytes, "落位权重为空"
    landed_fp = hashlib.sha256(out_bytes).hexdigest()
    # 云 PPO 确实训练了：产出权重 ≠ init 权重（warm-start 后梯度更新）
    assert landed_fp != init_weights_fp, "云 PPO 未改变权重（训练未生效？）"
    ckpt = work / "traj" / "it1" / "ppo_ckpt_remote"
    for f in ("model.pt", "opt.pt"):
        assert (ckpt / f).exists(), f"ppo_ckpt_remote 缺 {f}（D5 opt 状态未往返）"
    # state.json（numpy RNG）由 pack_opt_tar 显式排除（H5 评审：per-job 种子已足够，
    # 不往返死数据）——不提文件不存在。assert (ckpt / "state.json").exists() 将因
    # pack_opt_tar 只打 model.pt+opt.pt 而失败，属预期行为。
    # 账本双态：job_pending（发布时写）→ job_completed（mark_job_completed 写，幂等）
    from remote.hub_client import mark_job_completed

    mark_job_completed(str(work / "training_log.jsonl"), jid)
    mark_job_completed(str(work / "training_log.jsonl"), jid)  # 幂等：不双写
    ledger = (work / "training_log.jsonl").read_text(encoding="utf-8")
    assert ledger.count("job_pending") == 1 and ledger.count("job_completed") == 1, (
        f"账本双态异常：pending={ledger.count('job_pending')} completed={ledger.count('job_completed')}"
    )

    agg = result.get("agg", {})
    log(
        f"SMOKE PASS: round-trip={t_total:.1f}s jid={jid} "
        f"steps={agg.get('steps')} chunks={agg.get('chunks')} "
        f"kl={agg.get('kl')} ppo_sec={result.get('ppo_sec')} "
        f"landed_wver={landed_fp[:12]}… opt_ckpt={'/'.join(f for f in ('model.pt','opt.pt','state.json') if (ckpt/f).exists())}"
    )
    srv.shutdown()
    th.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
