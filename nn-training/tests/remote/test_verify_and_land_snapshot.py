"""`verify_and_land` 的第 2 项只认**发布快照**（2026-10-23 y32 事故的回归）。

事故形状（实测，`reports/y32-ppo-job-vs-iter-20261023.md`）：

  * 轮次收官时 `dispatch` 不等在飞线程（tail_join 默认 0 s，假定"在飞只剩竞速输家"）；
  * 慢节点（a96 单局 p50 200.5 s / max 661.8 s，其余 3–13 s）跑的是**计划局**，
    它的 shard 在 job 在飞期间才落进 `it{N}/`；
  * 旧实现对 `it{N}` **活目录**重算 `data_fp` ⇒ 集合漂 ⇒ 一份**完全正确**的回传被判死
    ⇒ trainer 清会话、重发新 job ⇒ worker 整遍 PPO 白烧（22 job / 10 轮，~55% GPU）。

不变量：**payload 是快照，训练吃快照 ⇒ 权威只能是 `manifest["data_fp"]`。**
worker 的 `result["data_fp"]` 本来就逐字抄这份（`remote/train_core.py:828`），
所以旧"本地重算"从来只是「目录变了没有」的自检，对云端行为零判别力。

这里钉两条：
  ① 发布后活目录**多出** shard（迟到）⇒ 落位**必须成功**，只留 WARN；
  ② 结果报的 `data_fp` 与声明值不符 ⇒ 仍然**必须拒收**（防线不能被顺手拆掉）。
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest

from common.protocol import encode_opt_tar, encode_weights_json
from remote.hub_client import HubClientError, publish_job, verify_and_land

STAGE = 2000


def _shard(traj_it: Path, seed: int, wver: str) -> Path:
    """造一张最小可识别 shard（`iter_shard_dirs` 只要求 manifest + obs/metrics 之一）。"""
    d = traj_it / f"rl_s{STAGE}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(
        json.dumps({"stage": STAGE, "seed": seed, "wver": wver}), encoding="utf-8"
    )
    (d / "obs.npy").write_bytes(b"\x00" * 8)
    return d


def _publish_local_round(tmp_path: Path) -> tuple[dict, Path, Path]:
    """发布一份**本地轮**（kind=ppo，非 iter）：2 张 shard 进 payload。"""
    course_dir = tmp_path / "c-snap"
    job_root = course_dir / "remote-jobs"
    jsonl = course_dir / "training_log.jsonl"
    course_dir.mkdir(parents=True, exist_ok=True)
    jsonl.touch()
    w = tmp_path / "init_weights.json"
    w.write_text('{"format":"nn-weights-json","params":{}}', encoding="utf-8")
    it_dir = course_dir / "it1"
    shards = [_shard(it_dir, 11, "w" * 12), _shard(it_dir, 22, "w" * 12)]
    m = publish_job(
        job_root=job_root,
        jsonl_path=jsonl,
        run_id="r-snap",
        it=1,
        traj_dir=course_dir,
        shard_dirs=shards,
        commit="c" * 40,
        code_sha256="z" * 64,
        course='// course jsonc\n{"reward": {"formula": "score"}}',
        course_fp="f" * 64,
        init_weights_path=str(w),
        reward_formula="score",
        formula_hash="h" * 40,
        metrics_version=1,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=1,
        mb=512,
        lr=3e-4,
        log=lambda _m: None,
    )
    return m, w, it_dir


def _result(m: dict, w: Path, data_fp: str) -> dict:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        info = tarfile.TarInfo("model.pt")
        blob = b"\x00" * 32
        info.size = len(blob)
        tf.addfile(info, io.BytesIO(blob))
    return {
        "job_id": m["job_id"],
        "data_fp": data_fp,
        "init_weights_fp": hashlib.sha256(w.read_bytes()).hexdigest(),
        "weights_json": encode_weights_json(b'{"format":"nn-weights-json","params":{"x":1}}'),
        "opt_tar_b64": encode_opt_tar(buf.getvalue()),
        "commit_echo": m["commit"],
        "agg": {"policy": 0.0, "value": 0.0, "entropy": 0.0, "kl": 0.0, "mean_ret": 0.0},
        "report": {"games": 2, "winRate": 1.0},
    }


def test_late_shard_after_publish_does_not_reject(tmp_path: Path) -> None:
    """① 发布后活目录多一张 shard（慢节点迟到）⇒ 落位成功，不拒收。

    判据只用**行为**：落盘字节 + 返回的 wver（= 落盘文件 sha）。不钉日志文本。
    """
    m, w, it_dir = _publish_local_round(tmp_path)
    # 模拟迟到：payload 打完之后，慢节点才把它的 shard 落进来
    _shard(it_dir, 33, "w" * 12)
    out_w = tmp_path / "out" / "weights.json"
    wver = verify_and_land(
        _result(m, w, m["data_fp"]),
        m,
        init_weights_path=str(w),
        traj_dir=str(tmp_path / "c-snap"),
        it=1,
        out_weights=str(out_w),
        log=lambda _m: None,
    )
    assert out_w.exists(), "迟到 shard 不得让一份正确结果落不了盘"
    assert wver == hashlib.sha256(out_w.read_bytes()).hexdigest()


def test_declared_fp_still_enforced(tmp_path: Path) -> None:
    """② 结果报的 data_fp 与 manifest 声明值不符 ⇒ 仍拒收（防线不许被顺手拆掉）。"""
    m, w, _it_dir = _publish_local_round(tmp_path)
    with pytest.raises(HubClientError):
        verify_and_land(
            _result(m, w, "0" * 64),
            m,
            init_weights_path=str(w),
            traj_dir=str(tmp_path / "c-snap"),
            it=1,
            out_weights=str(tmp_path / "out" / "weights.json"),
            log=lambda _m: None,
        )
