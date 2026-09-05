"""test_remote_ppo.py — 远程 PPO（云 hub 协同训练）M1 单测。

覆盖（plan/remote-ppo-architecture.md §9 M1 清单 + §6 D1/D5/D6/D9/D12/D14）：
  * 协议编解码：weights_json / opt_tar base64 往返；
  * 幂等：job_id = sha256(idempotency_key) 稳定、同幂等键重发布不重复 pending；
  * chaos：旧 worker 迟到写回拒收（store_result 409）、job 重发幂等；
  * commit 不一致拒收（result.commit_echo != manifest.commit）；
  * 本地 vs 云 weights_json 字节 + 指纹一致（D12 产出方锁死）；
  * NaN 注入 fail-fast（save_weights_json 拒写坏权重 / reward 非有限拒收）；
  * 账本新事件（job_pending/job_completed）不破坏 last_completed_iter；
  * write_shard manifest 双写修复（落盘字节 == indent=2 版本）；
  * course_fp 血缘：resume 过滤 + 发布端课程校验（坏公式/坏关卡响亮拒绝，免 torch）；
  * hub-server：job 队列/租约/鉴权/jsonl 账本 + 磁盘 IPC 全链路。

免 torch 原则：本文件顶层不 import torch（hub 侧免 torch，D2）。torch 依赖用例
（D12 字节比对 / NaN）延迟导入，与 B7 / test_no_torch_on_import 同款。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import tarfile
import threading
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.hub_server import _JobStore, make_server
from remote.protocol import (
    AUTH_HEADER,
    LEASE_SEC,
    MANIFEST_REQUIRED,
    ProtocolError,
    data_fp,
    decode_opt_tar,
    decode_weights_json,
    encode_opt_tar,
    encode_weights_json,
    idempotency_key,
    job_seed,
    normalize_manifest,
    pack_payload,
    unpack_payload,
    validate_result,
)
from remote.protocol import (
    job_id as make_job_id,
)

REPO = ROOT.parent  # git 根（hub_client.REPO_ROOT 与 git_head 用）


# ------------------------------------------------------------------ fixtures

def _mini_manifest(**over) -> dict:
    """最小合法 manifest（必填齐全；over 覆盖任意字段）。"""
    m = {
        "proto": 1,
        "runId": "test-run",
        "it": 3,
        "job_id": "j" * 16,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": "// course jsonc\n{\"reward\": {\"formula\": \"score\"}}",
        "course_fp": "f" * 64,
        "reward_formula": "score",
        "formula_hash": "h" * 40,
        "metrics_version": 1,
        "gamma": 0.995,
        "lam": 0.95,
        "mode": "per-tick",
        "seed": "s" * 64,
        "epochs": 2,
        "mb": 512,
        "lr": 3e-4,
        "init_weights_fp": "w" * 64,
        "data_fp": "d" * 64,
        "payload_sha256": "p" * 64,
    }
    m.update(over)
    return m


def _write_shard(dirpath: Path, stage: int, seed: int, wver: str = "w" * 64,
                 course_fp: str | None = "f" * 64) -> dict:
    """写一个最小完整 shard（obs.npy + metrics.npy + manifest.json）。"""
    dirpath.mkdir(parents=True, exist_ok=True)
    np.save(dirpath / "obs.npy", np.zeros((4, 14, 26, 26), dtype=np.uint8))
    np.save(dirpath / "metrics.npy", np.zeros((5, 21), dtype=np.float64))
    np.save(dirpath / "value.npy", np.zeros(4, dtype=np.float32))
    mm = {
        "stage": stage,
        "seed": seed,
        "wver": wver,
        "nSamples": 4,
        "score": 10.0,
        "outcome": "win",
        "ticks": 100,
    }
    if course_fp is not None:
        mm["course_fp"] = course_fp
    (dirpath / "manifest.json").write_text(
        json.dumps(mm, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return mm


# ------------------------------------------------------------------ 协议编解码

def test_weights_json_b64_roundtrip() -> None:
    raw = b'{"format":"nn-weights-json","params":{}}' + b"\x00" * 37
    b64 = encode_weights_json(raw)
    assert decode_weights_json(b64) == raw
    # base64 必须 JSON 安全（hub-server 落盘 result.json 用）
    json.dumps({"weights_json": b64})


def test_opt_tar_b64_roundtrip() -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        info = tarfile.TarInfo("model.pt")
        payload = b"\x00" * 64
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    raw = buf.getvalue()
    assert decode_opt_tar(encode_opt_tar(raw)) == raw


def test_manifest_normalize_required_and_defaults() -> None:
    m = normalize_manifest(_mini_manifest())
    for k in MANIFEST_REQUIRED:
        assert k in m, f"必填字段 {k} 被归一化丢弃"
    # 可选字段默认值
    assert m["kl_coef"] == 0.0
    assert m["kl_cap"] is None
    assert m["adv_norm"] == "auto"
    assert m["shuffle"] is True
    assert m["schedule_raw"] == []
    assert m["opt_init"] == ""


def test_manifest_normalize_missing_field_fails() -> None:
    m = _mini_manifest()
    del m["course_fp"]
    with pytest.raises(ProtocolError, match="course_fp"):
        normalize_manifest(m)


def test_manifest_normalize_wrong_proto_fails() -> None:
    with pytest.raises(ProtocolError, match="proto"):
        normalize_manifest(_mini_manifest(proto=99))


def test_manifest_normalize_mode_redline() -> None:
    with pytest.raises(ProtocolError, match="per-tick"):
        normalize_manifest(_mini_manifest(mode="intent"))


# ------------------------------------------------------------------ data_fp / 幂等

def test_data_fp_deterministic_and_order_independent(tmp_path: Path) -> None:
    d1 = tmp_path / "it1"
    _write_shard(d1 / "rl_s1_seed10", 1, 10)
    _write_shard(d1 / "rl_s2_seed20", 2, 20)
    dirs = [d1 / "rl_s1_seed10", d1 / "rl_s2_seed20"]
    fp_a = data_fp(dirs)
    fp_b = data_fp(list(reversed(dirs)))  # 排序后路径一致 → 同指纹
    assert fp_a == fp_b
    assert len(fp_a) == 64


def test_data_fp_changes_with_shard_content(tmp_path: Path) -> None:
    d1 = tmp_path / "it1"
    _write_shard(d1 / "rl_s1_seed10", 1, 10, wver="w" * 64)
    dirs = [d1 / "rl_s1_seed10"]
    fp_a = data_fp(dirs)
    # 换权重 → manifest.wver 变 → data_fp 变（云训练语料漂移可被检出）
    _write_shard(d1 / "rl_s1_seed10", 1, 10, wver="x" * 64)
    assert data_fp(dirs) != fp_a


def test_idempotency_key_and_job_id_stable() -> None:
    m1 = _mini_manifest()
    m2 = _mini_manifest()  # 同幂等键（runId/it/init_weights_fp/data_fp 相同）
    k1, k2 = idempotency_key(m1), idempotency_key(m2)
    assert k1 == k2
    assert make_job_id(m1) == make_job_id(m2)
    assert len(make_job_id(m1)) == 16
    # 幂等键任一分量变 → job_id 变
    m3 = _mini_manifest(data_fp="e" * 64)
    assert make_job_id(m3) != make_job_id(m1)


def test_job_seed_deterministic_and_distinct() -> None:
    s1 = job_seed("run", 1, "w" * 64)
    s2 = job_seed("run", 1, "w" * 64)
    s3 = job_seed("run", 2, "w" * 64)
    assert s1 == s2 and len(s1) == 64
    assert s1 != s3


# ------------------------------------------------------------------ payload zip

def test_pack_unpack_payload_roundtrip(tmp_path: Path) -> None:
    shard_dir = tmp_path / "rl_s1_seed10"
    _write_shard(shard_dir, 1, 10)
    m = _mini_manifest(data_fp=data_fp([shard_dir]))
    zip_path = tmp_path / "payload.zip"
    sha = pack_payload([shard_dir], m, zip_path)
    assert sha == hashlib.sha256(zip_path.read_bytes()).hexdigest()
    # 解包：shard 目录 + 根 manifest
    dest = tmp_path / "out"
    manifest, shard_dirs = unpack_payload(zip_path, dest)
    assert manifest["job_id"] == m["job_id"]
    assert [Path(d).name for d in shard_dirs] == ["rl_s1_seed10"]
    assert (dest / "rl_s1_seed10" / "obs.npy").exists()


def test_pack_payload_missing_shard_fails(tmp_path: Path) -> None:
    with pytest.raises(ProtocolError):
        pack_payload([tmp_path / "nope"], _mini_manifest(), tmp_path / "x.zip")


# ------------------------------------------------------------------ result 校验（chaos）

def test_result_commit_mismatch_rejected() -> None:
    m = normalize_manifest(_mini_manifest())
    r = {
        "job_id": m["job_id"],
        "data_fp": m["data_fp"],
        "init_weights_fp": m["init_weights_fp"],
        "weights_json": encode_weights_json(b"{}"),
        "opt_tar_b64": "",
        "agg": {"policy": 0.1, "value": 0.2, "entropy": 0.3, "kl": 0.4, "mean_ret": 0.5},
        "commit_echo": "d" * 40,  # != manifest.commit
    }
    with pytest.raises(ProtocolError, match="commit_echo"):
        validate_result(r, m)


def test_result_job_id_mismatch_rejected() -> None:
    m = normalize_manifest(_mini_manifest())
    r = {
        "job_id": "x" * 16,
        "data_fp": m["data_fp"],
        "init_weights_fp": m["init_weights_fp"],
        "weights_json": encode_weights_json(b"{}"),
        "agg": {"policy": 0.1, "value": 0.2, "entropy": 0.3, "kl": 0.4, "mean_ret": 0.5},
        "commit_echo": m["commit"],
    }
    with pytest.raises(ProtocolError, match="job_id"):
        validate_result(r, m)


def test_result_data_fp_drift_rejected() -> None:
    m = normalize_manifest(_mini_manifest())
    r = {
        "job_id": m["job_id"],
        "data_fp": "e" * 64,  # 云训了别的语料
        "init_weights_fp": m["init_weights_fp"],
        "weights_json": encode_weights_json(b"{}"),
        "agg": {"policy": 0.1, "value": 0.2, "entropy": 0.3, "kl": 0.4, "mean_ret": 0.5},
        "commit_echo": m["commit"],
    }
    with pytest.raises(ProtocolError, match="data_fp"):
        validate_result(r, m)


def test_result_agg_missing_fields_rejected() -> None:
    m = normalize_manifest(_mini_manifest())
    r = {
        "job_id": m["job_id"],
        "data_fp": m["data_fp"],
        "init_weights_fp": m["init_weights_fp"],
        "weights_json": encode_weights_json(b"{}"),
        "agg": {"policy": 0.1},  # 缺 value/entropy/kl/mean_ret
        "commit_echo": m["commit"],
    }
    with pytest.raises(ProtocolError, match="agg"):
        validate_result(r, m)


# ------------------------------------------------------------------ D12 产出方锁死 + NaN fail-fast（torch 延迟导入）

def test_weights_json_local_vs_cloud_byte_identical(tmp_path: Path) -> None:
    """D12：本地 _export_weights（save_weights_json）与云 worker 同一函数 → 字节+指纹一致。

    模拟：同一模型状态 → 本地 save 一份、把状态经「协议传输」（encode/decode base64）
    在另一端 save 一份 → 字节逐位相等 + sha256 指纹相等。
    """
    import torch

    from data.weights_io import save_weights_json

    torch.manual_seed(7)
    net = torch.nn.Sequential(
        torch.nn.Linear(8, 8), torch.nn.ReLU(), torch.nn.Linear(8, 4)
    )
    out_local = tmp_path / "local.json"
    save_weights_json(net, str(out_local))
    # 协议传输：state_dict 以 base64 过 wire（等价云 worker 回传的 weights_json）
    buf = io.BytesIO()
    torch.save(net.state_dict(), buf)
    transmitted = encode_weights_json(buf.getvalue())
    net2 = torch.nn.Sequential(
        torch.nn.Linear(8, 8), torch.nn.ReLU(), torch.nn.Linear(8, 4)
    )
    net2.load_state_dict(torch.load(io.BytesIO(decode_weights_json(transmitted))))
    out_cloud = tmp_path / "cloud.json"
    save_weights_json(net2, str(out_cloud))
    b_local = out_local.read_bytes()
    b_cloud = out_cloud.read_bytes()
    assert b_local == b_cloud, "本地与云权重字节不一致（D12 产出方漂移）"
    assert hashlib.sha256(b_local).hexdigest() == hashlib.sha256(b_cloud).hexdigest()


def test_nan_weights_fail_fast(tmp_path: Path) -> None:
    """NaN 注入 → save_weights_json 拒绝写出（云路径同一函数 → 同样 fail-fast）。"""
    import torch

    from data.weights_io import save_weights_json

    net = torch.nn.Linear(4, 4)
    with torch.no_grad():
        net.weight.fill_(float("nan"))
    with pytest.raises(ValueError, match="非有限值"):
        save_weights_json(net, str(tmp_path / "bad.json"))
    assert not (tmp_path / "bad.json").exists(), "坏权重必须不落盘"


def test_reward_nonfinite_rejected(tmp_path: Path) -> None:
    """reward 公式产出非有限值 → 响亮拒绝（防污染 GAE）。"""
    from rl.config import load_course
    from rl.reward_library import (
        FormulaError,
        build_reward_fn,
        reward_from_spec,
    )

    course = load_course(str(ROOT / "curricula" / "p4-onset.jsonc"))
    spec = course.reward_spec()
    fn = build_reward_fn(spec)
    metrics = np.zeros((3, 21), dtype=np.float64)
    # 注入 NaN 指标（整行，避免公式未用该列而漏检）→ phi 应抛
    metrics[1, :] = float("nan")
    try:
        fn(metrics, "win", it=1)
        raise AssertionError("NaN 指标应当被公式引擎拒绝")
    except (FormulaError, ValueError, OverflowError):
        pass
    # 正常指标不应抛
    r = reward_from_spec(spec, np.zeros((3, 21), dtype=np.float64), "win", it=1)
    assert r.shape == (2,)


# ------------------------------------------------------------------ 账本 / resume 不破坏

def test_ledger_new_events_do_not_break_last_completed_iter(tmp_path: Path) -> None:
    """job_pending/job_completed 事件混入 jsonl → last_completed_iter 不受影响。"""
    from rl.resume import last_completed_iter

    jl = tmp_path / "training_log.jsonl"
    lines = [
        {"event": "run_start", "rotateSeed": 7},
        {"event": "iteration", "iter": 2, "samples": 10},
        {"event": "job_pending", "job_id": "a" * 16, "it": 3, "ts": time.time()},
        {"event": "iteration", "iter": 5, "samples": 20},
        {"event": "job_completed", "job_id": "a" * 16, "ts": time.time()},
    ]
    jl.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert last_completed_iter(jl) == 5


def test_resume_course_fp_filter(tmp_path: Path) -> None:
    """D14：completed_pairs 按 course_fp 过滤——跨课程 shard 不参与对账。"""
    from rl.resume import completed_pairs

    traj = tmp_path / "traj" / "it3"
    _write_shard(traj / "rl_s1_seed10", 1, 10, course_fp="a" * 64)
    _write_shard(traj / "rl_s2_seed20", 2, 20, course_fp="b" * 64)
    wver = "w" * 64
    assert len(completed_pairs(traj, wver)) == 2  # 无过滤：全部算数
    assert completed_pairs(traj, wver, course_fp="a" * 64) == {(1, 10)}
    assert completed_pairs(traj, wver, course_fp="z" * 64) == set()
    # 无 course_fp 的旧 shard（None=不过滤旧行为）在过滤后不混入
    _write_shard(traj / "rl_s3_seed30", 3, 30, course_fp=None)
    assert completed_pairs(traj, wver, course_fp="a" * 64) == {(1, 10)}


def test_write_shard_single_write_indent2(tmp_path: Path) -> None:
    """F8.3 修复：write_shard 只写一次 manifest（indent=2），磁盘字节无紧凑残留。"""
    import dist_common

    out = tmp_path / "shard"
    out.mkdir(parents=True)
    mm = {"stage": 1, "seed": 2, "wver": "w" * 64}
    files = {name: b"\x00" * 16 for name in dist_common.SHARD_FILES}
    dist_common.write_shard(files, mm, str(out))
    text = (out / "manifest.json").read_text(encoding="utf-8")
    # 唯一一份、indent=2 规格（与 TS exporter 同规）
    assert text == json.dumps(mm, ensure_ascii=False, indent=2)
    assert json.loads(text) == mm


# ------------------------------------------------------------------ 发布端课程校验（免 torch）

def test_publish_time_course_validation_bad_formula(tmp_path: Path) -> None:
    """D13：坏公式课程 publish 前必须被 load_course/build_reward_fn 响亮拒绝（免 torch）。"""
    from rl.config import load_course
    from rl.reward_library import build_reward_fn

    bad = tmp_path / "bad-formula.jsonc"
    bad.write_text(
        json.dumps({"reward": {"formula": "not_a_real_function_xyz(1)"}}),
        encoding="utf-8",
    )
    from rl.reward_library import FormulaError

    with pytest.raises(FormulaError):
        course = load_course(str(bad))
        build_reward_fn(course.reward_spec())


def test_publish_time_course_validation_bad_stage(tmp_path: Path) -> None:
    """D13：坏关卡（grid 非 13×13）课程发布前响亮拒绝（pydantic fail fast）。"""
    from rl.config import load_course

    bad = tmp_path / "bad-stage.jsonc"
    bad.write_text(
        json.dumps(
            {
                "reward": {"formula": "score"},
                "stages": [{"name": "bad", "grid": [[0] * 13] * 12}],  # 12 行 ≠ 13
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="grid"):
        load_course(str(bad))


def test_course_path_mounted_on_args() -> None:
    """course_from_args 挂 args.course_path（远程发布 snapshot 用，D13）。"""
    import types

    from rl.config import course_from_args

    args = types.SimpleNamespace(course="p4-onset", course_file="")
    course = course_from_args(args)
    assert course is not None
    assert args.course_path.endswith("p4-onset.jsonc")
    assert Path(args.course_path).exists()


# ------------------------------------------------------------------ hub-server 全链路（磁盘 IPC + HTTP）

def _boot_server(tmp_path: Path, token: str = "sekret") -> tuple:
    """起一个 hub-server（随机端口），返回 (base_url, store, server, thread)。"""
    store = _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl")
    srv = make_server(store, 0, token, host="127.0.0.1")
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return f"http://127.0.0.1:{port}", store, srv, th


def _http(base_url: str, token: str, path: str, method: str = "GET",
          data: bytes | None = None,
          extra_headers: dict[str, str] | None = None) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    headers = {AUTH_HEADER: f"Bearer {token}"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        base_url + path,
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


def _http_raw(base_url: str, token: str, path: str) -> tuple[int, bytes]:
    """payload 下载等二进制端点（响应体非 JSON）。"""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        base_url + path, headers={AUTH_HEADER: f"Bearer {token}"}, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_hub_server_auth_and_job_lifecycle(tmp_path: Path) -> None:
    """鉴权（401/闭锁）+ 发布（磁盘 IPC）→ 领取 → payload → 结果 → 状态全链路。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        # 未带 token → 401
        import urllib.request

        req = urllib.request.Request(base + "/jobs/next")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("无 token 应当 401")
        except urllib.error.HTTPError as e:
            assert e.code == 401

        # hub 侧磁盘 IPC 发布（模拟 publish_job 的写盘行为）
        manifest = normalize_manifest(_mini_manifest())
        jid = manifest["job_id"]
        jd = store._job_dir(jid)
        jd.mkdir(parents=True, exist_ok=True)
        (jd / "payload.zip").write_bytes(b"PK\x03\x04fake")
        (jd / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        store.publish(jid, manifest, b"PK\x03\x04fake")

        # 领取 → payload 下载（H2：领取时下发 lease_token，后续心跳/结果回传须携带）
        st, body = _http(base, "sekret", "/jobs/next")
        assert st == 200 and body["job_id"] == jid
        assert body["manifest"]["data_fp"] == manifest["data_fp"]
        lease_token = str(body.get("lease_token", ""))
        assert lease_token, "H2：领取时应下发 lease_token"
        st2, payload_bytes = _http_raw(base, "sekret", f"/jobs/{jid}/payload")
        assert st2 == 200 and payload_bytes == b"PK\x03\x04fake"
        # 已领取未过期 → 不再领取（Q7：不重发）
        st3, body3 = _http(base, "sekret", "/jobs/next")
        assert body3["job_id"] is None or body3["job_id"] != jid

        # 结果 POST（带 lease_token + 正确 commit_echo）→ status done → 取回
        result = {
            "job_id": jid,
            "data_fp": manifest["data_fp"],
            "init_weights_fp": manifest["init_weights_fp"],
            "weights_json": encode_weights_json(b'{"ok":true}'),
            "opt_tar_b64": "",
            "agg": {"policy": 0.1, "value": 0.2, "entropy": 0.3, "kl": 0.4, "mean_ret": 0.5},
            "commit_echo": manifest["commit"],
        }
        st4, _ = _http(base, "sekret", f"/jobs/{jid}/result", method="POST",
                       data=json.dumps(result).encode("utf-8"),
                       extra_headers={"X-Lease-Token": lease_token})
        assert st4 in (200, 201)
        st5, body5 = _http(base, "sekret", f"/jobs/{jid}/status")
        assert st5 == 200 and body5["state"] == "done"
        st6, body6 = _http(base, "sekret", f"/jobs/{jid}/result")
        assert st6 == 200 and body6["weights_json"] == result["weights_json"]
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_hub_server_late_worker_writeback_rejected(tmp_path: Path) -> None:
    """旧 worker 迟到写回：结果已存在 → 409 拒收（防重复写回覆盖）。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        manifest = normalize_manifest(_mini_manifest())
        jid = manifest["job_id"]
        store.publish(jid, manifest, b"PK\x03\x04fake")
        # 先领取获得 lease_token（H2）
        st_claim, body_claim = _http(base, "sekret", "/jobs/next")
        assert st_claim == 200 and body_claim["job_id"] == jid
        lease_token = str(body_claim.get("lease_token", ""))
        result = {
            "job_id": jid,
            "data_fp": manifest["data_fp"],
            "init_weights_fp": manifest["init_weights_fp"],
            "weights_json": encode_weights_json(b"{}"),
            "agg": {"policy": 0.1, "value": 0.2, "entropy": 0.3, "kl": 0.4, "mean_ret": 0.5},
            "commit_echo": manifest["commit"],
        }
        st1, _ = _http(base, "sekret", f"/jobs/{jid}/result", method="POST",
                       data=json.dumps(result).encode("utf-8"),
                       extra_headers={"X-Lease-Token": lease_token})
        assert st1 in (200, 201)
        st2, body = _http(base, "sekret", f"/jobs/{jid}/result", method="POST",
                          data=json.dumps(result).encode("utf-8"),
                          extra_headers={"X-Lease-Token": lease_token})
        assert st2 == 409, f"迟到写回应 409，收到 {st2} {body}"
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_hub_server_commit_mismatch_rejected_at_post(tmp_path: Path) -> None:
    """commit 不一致的结果在 server 端 POST 时即被拒收（400）。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        manifest = normalize_manifest(_mini_manifest())
        jid = manifest["job_id"]
        store.publish(jid, manifest, b"PK\x03\x04fake")
        # 先领取获得 lease_token（H2）
        st_claim, body_claim = _http(base, "sekret", "/jobs/next")
        assert st_claim == 200 and body_claim["job_id"] == jid
        lease_token = str(body_claim.get("lease_token", ""))
        result = {
            "job_id": jid,
            "data_fp": manifest["data_fp"],
            "init_weights_fp": manifest["init_weights_fp"],
            "weights_json": encode_weights_json(b"{}"),
            "agg": {"policy": 0.1, "value": 0.2, "entropy": 0.3, "kl": 0.4, "mean_ret": 0.5},
            "commit_echo": "d" * 40,  # 旧 worker（不同 commit）迟到写回
        }
        st, body = _http(base, "sekret", f"/jobs/{jid}/result", method="POST",
                         data=json.dumps(result).encode("utf-8"),
                         extra_headers={"X-Lease-Token": lease_token})
        assert st == 400, f"commit 不一致应 400，收到 {st} {body}"
        assert store.get_result(jid) is None, "拒收的结果不得落盘"
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_hub_server_lease_expiry_requeues(tmp_path: Path) -> None:
    """租约过期 → job 回可领取池（云 worker 崩溃后重拉同一 job，幂等键去重）。"""
    base, store, srv, th = _boot_server(tmp_path)
    try:
        manifest = normalize_manifest(_mini_manifest())
        jid = manifest["job_id"]
        store.publish(jid, manifest, b"PK\x03\x04fake")
        # 领取 → 租约生效
        st, body = _http(base, "sekret", "/jobs/next")
        assert body["job_id"] == jid
        # 手动过期（无需等 30min）
        store._leases[jid] = store._now() - 1
        st2, body2 = _http(base, "sekret", "/jobs/next")
        assert st2 == 200 and body2["job_id"] == jid, "租约过期后必须可重领"
    finally:
        srv.shutdown()
        th.join(timeout=5)


def test_claimable_pool_rebuilt_from_ledger(tmp_path: Path) -> None:
    """D8：可领取池 = jsonl（job_pending）+ 租约重算——进程重启纯重读即可重建。"""
    store1 = _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl")
    manifest = normalize_manifest(_mini_manifest())
    jid = manifest["job_id"]
    store1.publish(jid, manifest, b"PK\x03\x04fake")
    # 模拟 hub kill -9：新 store（同磁盘）纯重读
    store2 = _JobStore(tmp_path / "jobs", tmp_path / "training_log.jsonl")
    assert store2.claimable_job_ids() == [jid]
    # job_completed 账本事件 → 不再可领取（幂等：重复 mark 不双写）
    store2.mark_completed(jid)
    store2.mark_completed(jid)
    assert store2.claimable_job_ids() == []
    completed = [
        e for e in store2._read_ledger() if e.get("event") == "job_completed"
    ]
    assert len(completed) == 1


# ------------------------------------------------------------------ 重传机制（2026-09-05，DECISIONS §340）

import remote.worker as worker_mod
from remote.protocol import RetryableError


@pytest.fixture()
def _no_sleep(monkeypatch):
    """重试退避不真睡——测试只验证次序与分类。"""
    monkeypatch.setattr(worker_mod.time, "sleep", lambda _s: None)


def test_download_retry_transient_then_success(monkeypatch, _no_sleep) -> None:
    calls = []

    def fake_request(base_url, token, path, timeout=30.0, **kw):
        calls.append(path)
        if len(calls) == 1:
            return 503, b"edge error"
        return 200, b"payload-bytes"

    monkeypatch.setattr(worker_mod, "_request", fake_request)
    out = worker_mod.download_payload("http://hub", "t", "jid1", log=lambda _m: None)
    assert out == b"payload-bytes"
    assert calls == ["/jobs/jid1/payload"] * 2


def test_download_4xx_is_deterministic(monkeypatch, _no_sleep) -> None:
    calls = []

    def fake_request(base_url, token, path, timeout=30.0, **kw):
        calls.append(path)
        return 404, b"not found"

    monkeypatch.setattr(worker_mod, "_request", fake_request)
    with pytest.raises(ProtocolError, match="404"):
        worker_mod.download_code("http://hub", "t", "jid1", log=lambda _m: None)
    assert len(calls) == 1  # 4xx 确定性拒绝：不重试


def test_download_exhausted_raises_retryable(monkeypatch, _no_sleep) -> None:
    monkeypatch.setattr(worker_mod, "_request", lambda *_a, **_k: (500, b"x"))
    with pytest.raises(RetryableError, match="重试 3 次"):
        worker_mod.download_payload("http://hub", "t", "jid1", attempts=3, log=lambda _m: None)


def test_post_result_retry_then_success(monkeypatch, _no_sleep) -> None:
    statuses = iter([502, 503, 200])

    def fake_request(base_url, token, path, timeout=30.0, **kw):
        return next(statuses), b"{}"

    monkeypatch.setattr(worker_mod, "_request", fake_request)
    rc = worker_mod.post_result(
        "http://hub", "t", "jid1",
        {"job_id": "jid1"}, lease_token="L", log=lambda _m: None,
    )
    assert rc == 200


def test_post_result_409_is_idempotent_success(monkeypatch, _no_sleep) -> None:
    monkeypatch.setattr(worker_mod, "_request", lambda *_a, **_k: (409, b"duplicate"))
    rc = worker_mod.post_result(
        "http://hub", "t", "jid1", {"job_id": "jid1"}, log=lambda _m: None,
    )
    assert rc == 409


def test_post_result_4xx_rejected_no_retry(monkeypatch, _no_sleep) -> None:
    calls = []

    def fake_request(base_url, token, path, timeout=30.0, **kw):
        calls.append(1)
        return 400, b"result rejected: bad fingerprints"

    monkeypatch.setattr(worker_mod, "_request", fake_request)
    with pytest.raises(ProtocolError, match="400"):
        worker_mod.post_result("http://hub", "t", "jid1", {"x": 1}, log=lambda _m: None)
    assert len(calls) == 1


def test_store_release_returns_to_pool(tmp_path: Path) -> None:
    store = _JobStore(tmp_path / "jobs", tmp_path / "ledger.jsonl")
    store.publish("jid-r", {"job_id": "jid-r"}, b"payload")
    token = store.claim("jid-r")
    assert token is not None
    assert store.claimable_job_ids() == []  # 租约期内不可领
    assert store.release("jid-r", "wrong-token") is False  # H2：非持有人拒释放
    assert store.release("jid-r", token) is True
    assert store.claimable_job_ids() == ["jid-r"]  # 立即回池
    assert store.release("jid-r", token) is False  # 已释放：幂等拒绝


def test_run_job_result_cache_reuse(tmp_path: Path, monkeypatch) -> None:
    """上次算完但回传失败 → 重领同 job 直接复用缓存结果，不再下载/重算。"""
    m = _mini_manifest()
    jid = m["job_id"]
    cached = {
        "job_id": jid,
        "data_fp": m["data_fp"],
        "init_weights_fp": m["init_weights_fp"],
        "weights_json": encode_weights_json(b"{}"),
        "opt_tar_b64": encode_opt_tar(b""),
        "agg": {"policy": 0.0, "value": 0.0, "entropy": 0.0, "kl": 0.0, "mean_ret": 0.0},
        "commit_echo": m["commit"],
        "ppo_sec": 0.0,
        "smoke": True,
    }
    rdir = tmp_path / jid
    rdir.mkdir(parents=True)
    (rdir / "_result.json").write_text(json.dumps(cached), encoding="utf-8")

    def _boom(*_a, **_k):  # 缓存命中时不允许发生任何网络/重算
        raise AssertionError("cache hit must not download or recompute")

    monkeypatch.setattr(worker_mod, "download_payload", _boom)
    monkeypatch.setattr(worker_mod, "download_code", _boom)
    out = worker_mod.run_job(
        "http://hub", "t", {"job_id": jid, "manifest": m},
        work_dir=tmp_path, echo=False, log=lambda _m: None,
    )
    assert out == cached
