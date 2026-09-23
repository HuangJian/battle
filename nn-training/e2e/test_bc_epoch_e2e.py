"""BC 每 epoch 回传 + 中断接续 —— 真 hub HTTP 全链路集成测试（2026-09-13 补跑 26f9171）。

补 26f9171 评审要求的集成测试：单测（test_bc_epoch_resume.py）只覆盖 _JobStore /
bc.on_epoch / eval 块解析；本文件把**真 HTTP hub** 拉进环——
发布 → 领取（租约）→ 每 epoch 回传 → 中断（worker 死亡）→ 重领 → hub resume 接续
→ 假训练续完 → result 回传 → 零重训终态；以及 bc_loop.wait_bc_round 的指标入账 +
eval 边界派发（假 eval 节点多图干净评估聚合）。

全程**假文件、零真训练、零真语料生成**：
  · 语料 = 伪造 shard 目录（只有 manifest.json 供 D14 校验——bc_train 被替换为假体）；
  · 权重 = gzip 占位字节 / 2 参数 torch 微型模块（只序列化不训练）；
  · 训练 = monkeypatch train.bc.train（断言 resume/epoch_offset 透传正确 + 驱动 on_epoch）；
  · eval 节点 = 本地线程 HTTP 服务器（/v1/ping /v1/weights /v1/task 回显 eval manifest）。
"""

from __future__ import annotations

import gzip
import json
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dist_common
from common.protocol import decode_weights_json, encode_weights_json
from remote import worker as worker_mod
from remote.hub_client import AUTH_HEADER
from remote.hub_server import _JobStore, make_server
from rl import bc_loop
from rl.bc_config import BcEvalBlock, load_bc_course
from tests.helpers.hub_poll import hub_poll

TOKEN = "test-token"
COURSE_FP = "course-fp-e2e"
CORPUS_FP = "corpus-fp-e2e"
COMMIT = "c" * 40
DATA_FP = "d" * 64


def _quiet(msg: str) -> None:  # pragma: no cover — 测试静音
    pass


# ------------------------------------------------------------------ harness


def _boot_hub(tmp_path: Path) -> tuple[str, _JobStore, Any]:
    """真 hub-server（随机端口）。返回 (base_url, store, srv)。"""
    store = _JobStore(tmp_path / "jobs", tmp_path / "hub-ledger.jsonl")
    srv = make_server(store, 0, TOKEN, host="127.0.0.1")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return f"http://127.0.0.1:{srv.server_address[1]}", store, srv


def _publish_bc_job(tmp_path: Path, store: _JobStore, *, epochs: int, jid: str) -> Path:
    """模拟 publish_job 的写盘行为（test_remote_ppo 同款磁盘 IPC）：manifest +
    payload 存根 + job_pending 账本事件 → peek/claim 可领取。"""
    jd = store.job_root / jid
    jd.mkdir(parents=True, exist_ok=True)
    manifest = {
        "job_id": jid,
        "kind": "bc",
        "proto": 1,
        "runId": "run-e2e",
        "it": 0,
        "arch": "student",
        "epochs": epochs,
        "mb": 32,
        "lr": 1e-3,
        "val_split": 0.2,
        "mirror_p": 0.0,
        "ckpt_every": 0,
        "value_coef": 0.0,
        "data_fp": DATA_FP,
        "init_weights_fp": "bc",
        "commit": COMMIT,
        "code_sha256": "e" * 64,
        "course": "{}",
        "course_fp": COURSE_FP,
        "corpus_fp": CORPUS_FP,
        "course_name": "e2e",
        "mode": "bc",
        "seed": "1a2b3c4d",  # job_seed hex str（D5）
        "payload_sha256": "stub",
    }
    (jd / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (jd / "payload.tar.xz").write_bytes(b"stub-payload")
    with open(store.jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": "job_pending", "job_id": jid, "ts": time.time()}) + "\n")
    return jd


def _fake_shard_dir(job_dir: Path) -> str:
    """伪造 shard 目录：只含 manifest.json（D14 血缘比对用；真训练被替换）。"""
    sd = job_dir / "bc_s0_seed1"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "manifest.json").write_text(
        json.dumps({"course_fp": COURSE_FP, "corpus_fp": CORPUS_FP, "stage": 0, "seed": 1}),
        encoding="utf-8",
    )
    return str(sd)


def _fake_weights(ep: int) -> bytes:
    # mtime=0：gzip 头默认嵌当前时间——同内容两次压缩字节不同，断言必须确定式
    return gzip.compress(json.dumps({"ep": ep}).encode("utf-8"), mtime=0)


def _epoch_body(ep: int) -> dict[str, Any]:
    return {
        "epoch": ep,
        "weights": encode_weights_json(_fake_weights(ep)),
        "metrics": {
            "train_loss": round(1.0 / ep, 4),
            "val_loss": round(1.2 / ep, 4),
            "move_acc": ep / 10,
            "fire_acc": ep / 20,
            "lr": 3e-3,
        },
    }


def _http_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    token: str = TOKEN,
    extra_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    import urllib.error
    import urllib.request

    headers = {AUTH_HEADER: f"Bearer {token}", **(extra_headers or {})}
    req = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


def _read_ledger_events(p: Path, event: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        if e.get("event") == event:
            out.append(e)
    return out


# ------------------------------------------------------------------ 假 eval 节点


class _FakeEvalNodeHandler(BaseHTTPRequestHandler):
    """/v1/ping（evalSupport + codeHash 一致）/ /v1/weights / /v1/task（同步 200，
    v1 JSON 容器回显 eval manifest——win 按 seed 奇偶，计数器按 seed 确定式生成）。"""

    hits = {"weights": 0, "tasks": 0}

    def log_message(self, fmt: str, *args: object) -> None:  # pragma: no cover
        pass

    def _send_json(self, obj: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/v1/ping":
            self._send_json(
                {
                    "evalSupport": True,
                    "codeHash": dist_common.compute_code_hash(),
                    "concurrency": 2,
                }
            )
            return
        if parsed.path == "/v1/task":
            type(self).hits["tasks"] += 1
            q = dict(urllib.parse.parse_qsl(parsed.query))
            stage, seed = int(q["stage"]), int(q["seed"])
            win = seed % 2 == 1
            manifest = {
                "wver": q.get("wver"),
                "mode": "eval",
                "policy": "nn",
                "stage": stage,
                "seed": seed,
                "win": win,
                "cleared": win,
                "outcome": "cleared" if win else "max_ticks",
                "kills": seed,
                "playerHits": 5 - seed,
                "powerUpsCollected": seed % 3,
                "score": 100 + seed,
                "ticks": 100 * seed,
            }
            body = gzip.compress(json.dumps({"manifest": manifest, "files": {}}).encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if urllib.parse.urlparse(self.path).path == "/v1/weights":
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            type(self).hits["weights"] += 1
            self._send_json({"cache": "kept"})
            return
        self._send_json({"error": "not found"}, 404)


def _boot_fake_eval_node() -> tuple[str, ThreadingHTTPServer]:
    _FakeEvalNodeHandler.hits = {"weights": 0, "tasks": 0}  # 类级计数器按测试复位
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeEvalNodeHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv


# ------------------------------------------------------------------ ①② 中断演练


def test_e2e_bc_interrupt_resume_continue_and_zeroretrain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全链中断演练：worker A 领取回传 ep1..3 → 死亡 → worker B 重领 → hub resume
    接续（resume+epoch_offset 透传给 bc.train，假训练体驱动 on_epoch 回传 ep4）→
    result 回传 → 再领时 resume 已达总数 → 零重训直接终态。"""
    base, store, srv = _boot_hub(tmp_path)
    jid = "b" * 16
    jd = _publish_bc_job(tmp_path, store, epochs=4, jid=jid)
    try:
        # ---- worker A：领取 + 回传 epoch 1..3（租约内）----
        claim = hub_poll(base, TOKEN)
        assert claim is not None and claim["job_id"] == jid
        tok_a = str(claim["lease_token"])
        for ep in (1, 2, 3):
            _bc_post = worker_mod._bc_post_epoch
            _bc_post(base, TOKEN, jid, _epoch_body(ep), tok_a, log=_quiet)
        st, body = _http_json(base, f"/jobs/{jid}/bc-metrics")
        assert st == 200 and [r["epoch"] for r in body["rows"]] == [1, 2, 3]
        st, body = _http_json(base, f"/jobs/{jid}/resume")
        assert st == 200 and body["epoch"] == 3
        assert decode_weights_json(str(body["weights"])) == _fake_weights(3)

        # ---- 中断：worker A 死亡（租约释放 = 过期同效），epoch 4 永未回传 ----
        assert store.release(jid, tok_a) is True

        # ---- worker B：重领同 job（新租约）；旧僵尸回传被 403 拒（不覆盖新 resume）----
        claim_b = hub_poll(base, TOKEN)
        assert claim_b is not None and claim_b["job_id"] == jid
        tok_b = str(claim_b["lease_token"])
        assert tok_b != tok_a
        st, _ = _http_json(
            base,
            f"/jobs/{jid}/epoch",
            method="POST",
            data=json.dumps(_epoch_body(3)).encode("utf-8"),
            token=TOKEN,
            extra_headers={"Content-Type": "application/json", "X-Lease-Token": tok_a},
        )
        assert st == 403

        resumed = worker_mod._bc_fetch_resume(base, TOKEN, jid, log=_quiet)
        assert resumed is not None and resumed[0] == 3
        assert resumed[1] == _fake_weights(3)

        # ---- B 续训：假 bc_train 驱动 on_epoch（ep4 → hub + 本地 resume）----
        import torch

        calls: list[tuple[int, int]] = []

        def fake_train(ns: Any) -> dict[str, Any]:
            assert ns.resume is not None and Path(str(ns.resume)).read_bytes() == _fake_weights(3)
            calls.append((int(ns.epochs), int(ns.epoch_offset)))
            m = torch.nn.Linear(2, 2)
            for i in range(1, int(ns.epochs) + 1):
                ns.on_epoch(
                    int(ns.epoch_offset) + i,
                    m,
                    {
                        "train_loss": 0.5,
                        "val_loss": 0.4,
                        "move_acc": 0.9,
                        "fire_acc": 0.8,
                        "lr": 1e-3,
                    },
                )
            out = Path(str(ns.out))
            out.write_bytes(_fake_weights(4))
            return {
                "out": str(out),
                "history": {"move_acc": [0.9], "fire_acc": [0.8]},
                "sizes": {"train": 10, "val": 2},
                "best_val_loss": 0.4,
                "params": 6,
            }

        monkeypatch.setattr("train.bc.train", fake_train)
        manifest = json.loads((jd / "manifest.json").read_text(encoding="utf-8"))
        result = worker_mod._run_bc_job(
            jid=jid,
            manifest=manifest,
            job_dir=jd,
            shard_dirs=[_fake_shard_dir(jd)],
            device="cpu",
            torch_threads=1,
            echo=False,
            base_url=base,
            token=TOKEN,
            lease_token=tok_b,
            log=_quiet,
        )
        assert calls == [(1, 3)]  # 只训剩下的 1 个 epoch，offset=3
        assert result["metrics"]["resumed_from"] == 3
        assert decode_weights_json(str(result["weights_json"])) == _fake_weights(4)
        st, body = _http_json(base, f"/jobs/{jid}/resume")
        assert body["epoch"] == 4
        st, body = _http_json(base, f"/jobs/{jid}/bc-metrics")
        assert [r["epoch"] for r in body["rows"]] == [1, 2, 3, 4]
        assert (jd.parent / "bc-resume" / f"{jid}.json").exists()  # push 模式兜底落地

        # result 回传（真 HTTP post_result，v2 wire）
        code = worker_mod.post_result(base, TOKEN, jid, result, lease_token=tok_b, log=_quiet)
        assert code == 200
        st, body = _http_json(base, f"/jobs/{jid}/result")
        assert st == 200 and body["job_id"] == jid

        # ---- 零重训：resume(4) >= total(4) → 不触碰 bc_train 直接产出终态 ----
        def boom(ns: Any) -> dict[str, Any]:  # pragma: no cover — 必须不被调用
            raise AssertionError("resume 已达总 epoch，不得重训")

        monkeypatch.setattr("train.bc.train", boom)
        # 期望终态 = hub resume 现有字节（B 的 on_epoch 最后一次上传的权重文件）
        _, resume_body = _http_json(base, f"/jobs/{jid}/resume")
        expected_final = decode_weights_json(str(resume_body["weights"]))
        result2 = worker_mod._run_bc_job(
            jid=jid,
            manifest=manifest,
            job_dir=jd,
            shard_dirs=[_fake_shard_dir(jd)],
            device="cpu",
            torch_threads=1,
            echo=False,
            base_url=base,
            token=TOKEN,
            lease_token=tok_b,
            log=_quiet,
        )
        assert result2["metrics"]["resumed_complete"] is True
        assert result2["metrics"]["epochs"] == 4
        assert decode_weights_json(str(result2["weights_json"])) == expected_final
    finally:
        srv.shutdown()


# ------------------------------------------------------------------ ③ 指标入账 + eval 边界


def test_e2e_wait_bc_round_ledger_ingest_and_eval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """wait_bc_round：hub bc-metrics 增量 → bc_epoch 账本事件（控制台数据源）；
    eval.every_epochs 边界 → hub resume 快照 → 假节点多图干净评估 → bc_eval 账本
    事件（RL 门指标口径聚合）；result 200 后返回。"""
    base, store, srv = _boot_hub(tmp_path)
    node_base, node_srv = _boot_fake_eval_node()
    jid = "c" * 16
    _publish_bc_job(tmp_path, store, epochs=4, jid=jid)
    tok = store.claim(jid)
    assert tok is not None
    ledger = tmp_path / "train-ledger.jsonl"
    course = load_bc_course("bc-e2e").model_copy(
        update={"eval": BcEvalBlock(every_epochs=2, games_per_stage=3, levels=["arena4", "arena6"])}
    )
    cfg = {
        "nodes": [
            {"id": "fake-1", "url": node_base, "authKey": "", "concurrency": 2},
            # 不可达节点：eval 节点筛选必须跳过它而不炸
            {"id": "dead", "url": "http://127.0.0.1:1", "authKey": "", "concurrency": 1},
        ]
    }
    try:
        real_sleep = time.sleep
        monkeypatch.setattr(bc_loop.time, "sleep", lambda s: real_sleep(0.01))

        def fake_worker() -> None:
            for ep in (1, 2, 3, 4):
                worker_mod._bc_post_epoch(base, TOKEN, jid, _epoch_body(ep), tok, log=_quiet)
                real_sleep(0.05)
            # 等 wait 环把 4 行 epoch **与两次边界 eval** 全部入账再回 result——
            # result 200 会立即返回，不入账的行就永远丢了。
            # 2026-09-15 修：原条件只看 bc_epoch，于是「4 行 epoch 已入账」就回 result，
            # 而 epoch 4 的 eval 结果可能还在飞行中 ⇒ 断言 `[e["epoch"]] == [2, 4]`
            # 在满负荷下（python-gate 满编并行 / CI）偶发只拿到 [2]。这就是
            # docs/nn/engineering.md §7 记的「负载型 flake」的真根因：不是被测代码
            # 有 bug，而是测试自己的同步条件漏了它随后要断言的那部分状态。
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    if (
                        len(_read_ledger_events(ledger, "bc_epoch")) >= 4
                        and len(_read_ledger_events(ledger, "bc_eval")) >= 2
                    ):
                        break
                except OSError:
                    pass
                real_sleep(0.05)
            result = {
                "job_id": jid,
                "data_fp": DATA_FP,
                "init_weights_fp": "bc",
                "weights_json": encode_weights_json(_fake_weights(4)),
                "opt_tar_b64": "",
                "metrics": {
                    "epochs": 4,
                    "train_samples": 100,
                    "val_samples": 20,
                    "best_val_loss": 0.4,
                },
                "commit_echo": COMMIT,
                "bc_sec": 1.0,
            }
            worker_mod.post_result(base, TOKEN, jid, result, lease_token=tok, log=_quiet)

        thw = threading.Thread(target=fake_worker, daemon=True)
        thw.start()
        result = bc_loop.wait_bc_round(
            hub_url=base,
            token=TOKEN,
            jid=jid,
            jsonl_path=ledger,
            it=0,
            course=course,
            cfg=cfg,
            wait_sec=60,
            log=_quiet,
        )
        thw.join(10)

        assert result["job_id"] == jid
        epochs = _read_ledger_events(ledger, "bc_epoch")
        assert [e["epoch"] for e in epochs] == [1, 2, 3, 4]
        assert epochs[0]["train_loss"] == 1.0 and epochs[3]["move_acc"] == 0.4
        assert all(e["it"] == 0 for e in epochs)

        evals = _read_ledger_events(ledger, "bc_eval")
        assert [e["epoch"] for e in evals] == [2, 4]  # every_epochs=2 的两个边界
        for ev in evals:
            assert ev["it"] == 0 and len(ev["wver"]) == 64
            by_level = {row["level"]: row for row in ev["levels"]}
            assert set(by_level) == {"arena4", "arena6"}
            for row in by_level.values():
                # 1 stage × 3 games；win = seed 奇 → 2/3 胜，其余指标按公式聚合
                assert row["n"] == 3 and row["failed"] == 0
                assert row["wins"] == 2
                assert row["win_rate"] == pytest.approx(2 / 3, abs=1e-3)
                assert row["kills_mean"] == pytest.approx(2.0, abs=1e-3)
                assert row["phits_mean"] == pytest.approx(3.0, abs=1e-3)
                assert row["timeout_frac"] == pytest.approx(1 / 3, abs=1e-3)
        assert _FakeEvalNodeHandler.hits["weights"] == 2  # 每边界推送一份权重
        assert _FakeEvalNodeHandler.hits["tasks"] == 12  # 2 边界 × 2 图 × 3 局
    finally:
        node_srv.shutdown()
        srv.shutdown()
