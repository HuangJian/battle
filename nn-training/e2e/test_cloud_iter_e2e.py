"""e2e/test_cloud_iter_e2e.py — 「云端同机 rollout + PPO」全链路集成（真 hub / 真协议 / 真落位）。

用户指令（2026-09-17）：确保「云端同机 rollout + PPO」全流程畅通，本地 dashboard 能正常
读出**云端回传**的每 it 权重与指标。纪律：rollout / PPO 用假节点（不 spawn bun、不加载
torch、不跑一局游戏）；其余每一段都走真代码。

链路（kind=iter = 一整轮上云：云机自己跑 rollout + PPO）：

    训练侧 TrainingLoop._remote_iter(it)
      ├─ 真 build_iter_spec（课程 + 逐局 argv，与 _serial_ppo 同一个拼命令函数）
      ├─ 真 publish_job（磁盘 IPC：job 目录三件套 + payload.tar.xz + 账本 job_pending）
      ├─ 真 hub-server（remote.hub_server，127.0.0.1 临时端口；真租约 / 真账本）
      ├─ 假云机（本测试的线程）
      │    ├─ peek + claim  → 真领取（真租约 token）
      │    ├─ GET payload / code / ts_code → 真下载 + 逐 sha 对账（节点启动自检同规）
      │    ├─ 假 rollout：按 argv 逐局写**导出器同形**产物（w{i}/rl_sX_seedY + _rl_report.json）
      │    ├─ 真 verify_shards（实产集 == 声明集，data_fp 两侧同函数）
      │    ├─ 真 collect_reports + combine_reports（聚合口径不假）
      │    ├─ 假 PPO：换一份权重 + opt tar + agg（不跑一步 torch）
      │    └─ 真 validate_result + 真 pack_result_v2 → POST /jobs/{id}/result（v2 体）
      ├─ 真 wait_job → 真 verify_and_land（三重校验 + 权重原子落位 + opt 解包）
      ├─ 真 _export_weights（归档，只归档不清理）
      └─ 真 _record_iteration（iteration 账本）

断言面 = 本地 dashboard 的**三个真读者**（本测试按它们的契约独立重实现；这里红 ⇒ 面板
那一格读不出东西）：

  · 每 it 权重  `dashboard/src/server/eval-board/ckpts.ts`
                 - `iterFromCkpt` 从文件名 `{prefix}.it{N}.{ts}.json` 解 iter（`/\\.it(\\d+)\\./`）
                 - `buildEvalCkptsView` 扫 `nn-training/weights/<leg>/*.json`（归档）+
                   `tmp/<课>/weights.json`（活动指针）
  · 每 it 指标  `dashboard/src/server/iters.ts::readIterMetrics`（event=="iteration" 的字段表）
  · job 账本    `dashboard/src/server/api/ppo-queue.ts::detectPpoQueueStall`
                 （`job_pending` → `job_completed` + `claimed`/`result` 目录标记）

「字段存在」必须单独断言：`readIterMetrics` 用 `Number(x ?? 0)`，字段缺失会被**静默读成
0**（曲线掉零、表格显示 0% 却不报错）——读者不会替我们发现这种写入侧回归。

层纪律（e2e/ 自 60e5f69 起 hermetic，进门禁）：不 spawn bun、不加载 torch、不起真节点；
HTTP 全在本机临时端口。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 仓根（只读面板源码做双端锚，见用例末尾）。
_REPO = ROOT.parent

from common.protocol import (
    decode_opt_tar,
    encode_opt_tar,
    encode_weights_json,
    iter_expected_data_fp,
    normalize_manifest,
    pack_result_v2,
    unpack_payload,
    validate_result,
)
from remote.hub_server import _JobStore, make_server
from remote.iter_rollout import collect_reports, verify_shards
from remote.worker import d14_corpus_match  # 该模块顶层零 torch（延迟导入）
from rl.cli import build_argparser  # 模块级：真 CLI 解析器（大对象，别在用例内首次 import）
from rl.config import apply_course, course_from_args
from rl.loop_core import TrainingLoop
from rl.reports import combine_reports
from tests.helpers.hub_poll import hub_poll

#: 本轮 it（>1，避免与 it0 基线评估的语义混淆）。
IT = 7
#: 本轮对局（stage 用 tiny-a 的 1000 系自定义关，seed 任意）。
PAIRS = [(1000, 11), (1000, 12)]
#: hub 侧共享 token（真鉴权边界，不是摆设）。
TOKEN = "e2e-cloud-iter-token"

#: 云机 PPO 产出的新权重（与 init 不同字节 ⇒ 断言「回传的那份落地了」，不是本地起点）。
CLOUD_WEIGHTS = b'{"stub_cloud_ppo": "it7"}'
#: 云机自报的真训练秒（iteration 行 ppo_cloud_sec 的唯一来源）。
CLOUD_PPO_SEC = 3.5
#: 云机自报的采集墙钟（iteration 行 rollout_sec 的唯一来源）。
NODE_ROLLOUT_SEC = 12.5
#: 云机 agg（iteration 行 policy/value/entropy/kl/mean_ret）。
CLOUD_AGG = {
    "policy": -0.0125,
    "value": 0.4375,
    "entropy": 0.3125,
    "kl": 0.0021,
    "mean_ret": 0.125,
    "steps": 480,
    "chunks": 4,
}

#: 逐局报告（与导出器 `_rl_report.json` 同形；combine_reports 只吃这些键）。
#: 一胜一负 ⇒ winRate 0.5、score mean 0.1、dimMeans.kills 0.5 —— 断言的就是这几个数。
GAME_REPORTS = [
    {
        "games": 1,
        "outcomes": {"stage_clear": 1},
        "totalSamples": 30,
        "totalTicks": 900,
        "scoreList": [0.4],
        "dimLists": {"kills": [1.0], "dmg": [0.5]},
        "totalKills": 1,
    },
    {
        "games": 1,
        "outcomes": {"lives_exhausted": 1},
        "totalSamples": 30,
        "totalTicks": 900,
        "scoreList": [-0.2],
        "dimLists": {"kills": [0.0], "dmg": [0.5]},
        "totalKills": 0,
    },
]


# ────────────────────────── 本机 HTTP 小工具（真 wire，不 mock transport） ──────────────────────────


def _req(
    url: str, *, method: str = "GET", data: bytes | None = None, headers: dict | None = None
) -> tuple[int, bytes]:
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _flag(argv: list[str], flag: str) -> str:
    """argv 里 flag 的值（whitelist 已由 protocol 校验，测试侧只需读）。"""
    return argv[argv.index(flag) + 1]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ────────────────────────── 假云机（只假 rollout / PPO 两段计算） ──────────────────────────


class _FakeCloudNode(threading.Thread):
    """一台假云机：真 HTTP 领取/下载/回传 + 真协议校验，只有 rollout/PPO 是假的。

    内部任何失败都：(a) 记进 `self.errors`（主线程据此断言，根因不埋在超时里）、
    (b) POST `/jobs/{id}/fail` 报**确定性失败** —— 真链路里这条会让训练侧 `wait_job`
    立刻带原因收兵（而不是等满 30 分钟超时），所以测试红了也只在秒级。
    """

    def __init__(self, hub_url: str, work_dir: Path) -> None:
        super().__init__(daemon=True, name="fake-cloud-node")
        self.hub = hub_url
        self.work_dir = work_dir
        self.errors: list[str] = []
        self.job_id = ""
        self.lease_token = ""
        self.race = True
        self.manifest: dict = {}
        self.report: dict = {}
        self.result_status = 0
        #: 三件套的实测 sha（与 manifest 对账，等价节点的启动自检）
        self.shas: dict[str, str] = {}
        #: M0 计量对账用：payload 下行字节 / result 上行字节（两边各自实测）
        self.payload_bytes = 0
        self.result_bytes = 0

    # ---- 一次 job 的全流程 ----
    def _handle(self, job: dict) -> None:
        self.job_id = str(job["job_id"])
        self.lease_token = str(job.get("lease_token") or "")
        self.race = bool(job.get("race"))
        manifest = normalize_manifest(job["manifest"])
        self.manifest = manifest
        spec = manifest["rollout"]

        # 1) 真下载三件套 + 逐 sha 对账（payload / code.zip / ts_code.zip）
        blobs = {
            "payload": (f"/jobs/{self.job_id}/payload", manifest["payload_sha256"]),
            "code": (f"/jobs/{self.job_id}/code", manifest["code_sha256"]),
            "ts_code": (f"/jobs/{self.job_id}/ts_code", manifest["ts_code_sha256"]),
        }
        raw: dict[str, bytes] = {}
        for name, (path, want) in blobs.items():
            st, body = _req(f"{self.hub}{path}")
            if st != 200:
                raise AssertionError(f"{name} 下载失败 HTTP {st}: {body[:200]!r}")
            if _sha(body) != want:
                raise AssertionError(f"{name} sha256 与 manifest 不符（传输/打包口径漂了）")
            raw[name] = body
            self.shas[name] = _sha(body)
        self.payload_bytes = len(raw["payload"])

        # 2) 真解包 payload（与 worker.run_job 同一函数）——shard 由下面的假 rollout 现场产
        job_dir = self.work_dir / self.job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "payload.tar.xz").write_bytes(raw["payload"])
        unpack_payload(job_dir / "payload.tar.xz", job_dir)

        # 3) 假 rollout → 真校验/真聚合
        self.report = self._fake_rollout(job_dir, manifest, spec)

        # 4) 假 PPO → 真结果校验 → 真 v2 线格式回传
        result = self._fake_ppo_result(manifest, self.report)
        validate_result(result, manifest, commit_echo_must_match=True)
        payload = pack_result_v2(result)
        self.result_bytes = len(payload)
        st, body = _req(
            f"{self.hub}/jobs/{self.job_id}/result",
            method="POST",
            data=payload,
            headers={"Content-Type": "application/octet-stream", "X-Lease-Token": self.lease_token},
        )
        self.result_status = st
        if st != 200:
            raise AssertionError(f"回传被拒 HTTP {st}: {body[:300]!r}")

    def _fake_rollout(self, job_dir: Path, manifest: dict, spec: dict) -> dict:
        """假 rollout：不 spawn bun，按 argv 逐局写导出器同形产物；校验与聚合用真代码。"""
        for i, argv in enumerate(spec["argv"]):
            stage, seed = int(_flag(argv, "--stages")), int(_flag(argv, "--seeds"))
            wdir = job_dir / _flag(argv, "--out")
            shard = wdir / f"rl_s{stage}_seed{seed}"
            shard.mkdir(parents=True, exist_ok=True)
            mm = {
                "wver": spec["wver"],
                "stage": stage,
                "seed": seed,
                "course_fp": manifest.get("course_fp", ""),
                "corpus_fp": manifest.get("corpus_fp", ""),
                "node": "fake-cloud",
            }
            (shard / "manifest.json").write_text(json.dumps(mm), encoding="utf-8")
            (shard / "obs.npy").write_bytes(b"\x00")  # 占位载荷（测试不装载轨迹）
            (wdir / "_rl_report.json").write_text(
                json.dumps(GAME_REPORTS[i % len(GAME_REPORTS)]), encoding="utf-8"
            )
            # D14 语料血缘：shard 与 job 同课程（真链路上 worker 会逐 shard 校验）
            assert d14_corpus_match(
                str(manifest.get("course_fp", "")),
                str(manifest.get("corpus_fp", "") or ""),
                mm,
            ), "D14: shard 的 course_fp/corpus_fp 与 job 不一致（跨课语料混训）"
        # 真节点侧验收：实产 shard 集必须 == argv 声明集（data_fp 两侧同函数）
        shard_dirs = verify_shards(job_dir, iter_expected_data_fp(spec))
        rep = combine_reports(collect_reports(job_dir, spec))
        rep.update(
            {"shards": len(shard_dirs), "elapsedSec": NODE_ROLLOUT_SEC, "rolloutSrc": "node"}
        )
        return rep

    def _fake_ppo_result(self, manifest: dict, report: dict) -> dict:
        """假 PPO：权重/opt/agg 的形状与真 worker 一致，只是没有一步 torch。"""
        import io
        import tarfile

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo("adam_state.txt")
            info.size = len(CLOUD_WEIGHTS)
            tf.addfile(info, io.BytesIO(CLOUD_WEIGHTS))
        return {
            "job_id": self.job_id,
            "data_fp": manifest["data_fp"],
            "init_weights_fp": manifest["init_weights_fp"],
            "weights_json": encode_weights_json(CLOUD_WEIGHTS),
            "opt_tar_b64": encode_opt_tar(buf.getvalue()),
            "commit_echo": manifest["commit"],
            "report": report,
            "agg": dict(CLOUD_AGG),
            "ppo_sec": CLOUD_PPO_SEC,
            # worker 侧计量块（真 worker 的 _wire_block 同形）：payload 原始字节数，
            # 不是 sha 串长度——与 hub 实测的 sent_bytes 应当相等。
            "wire": {"payload_bytes": self.payload_bytes, "unpack_sec": 0.01},
        }

    def run(self) -> None:
        deadline = time.time() + 60.0
        while time.time() < deadline and self.result_status == 0 and not self.errors:
            job = hub_poll(self.hub, TOKEN)
            if job is None or not job.get("job_id"):
                # sleep-ok: 轮询步长（等的是「hub 已发布 job」这个状态，60s 只当挂起兜底）
                time.sleep(0.05)  # 短轮询（真 worker 的 poll 节奏）
                continue
            try:
                self._handle(job)
            except Exception as e:  # 任何失败都上报为**确定性失败**，别让训练侧等满超时
                self.errors.append(f"{type(e).__name__}: {e}")
                _req(
                    f"{self.hub}/jobs/{job['job_id']}/fail",
                    method="POST",
                    data=json.dumps({"reason": self.errors[-1], "fail_kind": "test"})[
                        :4000
                    ].encode(),
                )
            return
        if self.result_status == 0 and not self.errors:
            self.errors.append("60s 内没领到 job（publish / 账本 / job 目录有断点）")


# ────────────────────────── 夹具：课程参数 + 临时 hub ──────────────────────────


def _args_and_loop(tmp_path: Path, hub_url: str, weights: Path) -> tuple[TrainingLoop, Path, Path]:
    """真 CLI 解析 + 真 apply_course（课程是单一事实来源），只把落盘位置改到 tmp。"""
    traj = tmp_path / "traj"
    traj.mkdir(parents=True, exist_ok=True)  # hub 的 _JobStore 可能已建（job_root 在 traj 下）
    # tiny-a = 自带说明书「多课程并行流程验证夹具」的微型课程（stage 1000，per-tick）：
    # 集成测试正是它的用途，不借能力训练腿的配置（§15.2 哨兵口径）。
    args = build_argparser("per-tick", {}).parse_args(["--course", "tiny-a"])
    course = course_from_args(args)
    assert course is not None, "--course tiny-a 未解析出课程"
    apply_course(args, course)
    # 落盘位置一律改到 tmp（课程里的 tmp/tiny-a 是仓库相对路径，测试不写仓）。
    args.out = str(weights)
    args.traj = str(traj)
    args.backup_dir = str(tmp_path / "weights-arch")
    args.backup_prefix = "tiny-a-cloud"
    # 传输面：走本地 hub pull（push 直推云机由 test_push_mode_integration.py 覆盖）。
    args.remote_hub_url = hub_url
    args.remote_token = TOKEN
    args.remote_transport = "pull"
    # 整轮上云 = 每轮一次结算（单一 PPO 路径的语义；课程自带 stream=1 是历史本地腿的默认，
    # 单一 PPO 路径下 stream/double-buffer 恒置 0——见 `rl/config.py` §3 块）。
    args.stream = 0
    args.max_ticks = 60
    loop = TrainingLoop(args, None, "bun", {})
    loop._jsonl_path = traj / "training_log.jsonl"
    loop._traj_dir = traj / f"it{IT}"
    return loop, traj, Path(args.backup_dir)


class _Hub:
    """真 hub-server（临时端口）+ 真 _JobStore（磁盘账本 = traj 的 training_log.jsonl）。"""

    def __init__(self, job_root: Path, jsonl: Path) -> None:
        store = _JobStore(job_root, jsonl)
        self.srv = make_server(store, 0, TOKEN, host="127.0.0.1")
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        # shutdown + server_close + join：**把监听套接字真的释放**（同 worker 里一个测试
        # 一个 hub，只 shutdown 会漏掉 fd 与端口，长时间全量跑是不必要的资源累积）。
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(timeout=5.0)


# ────────────────────────── 主用例 ──────────────────────────


@pytest.fixture
def fast_wait_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 `wait_job` 的**轮询节奏**从生产默认 5s 调到 0.05s。

    唯一被动到的参数就是 `poll_sec`：5s 是给隧道抖动设计的节奏（真网络下没必要更密），
    **不是本测试要验的契约**。`wait_job` 本体全部真跑——404（还没回）→ 继续轮询、410
    （节点报确定性失败）→ 立即带原因收兵、5xx → 指数退避、超时前二次确认 `/status`。

    不调它的话，用例地板恒为 ~5s：假云机在 publish 后几百 ms 就把结果 POST 上来了，而
    训练侧的下一次轮询要等满 5s（实测 6.8s = 1.0s 准备/打包/发布 + 5.0s 轮询窗 + 收尾）。
    """
    import remote.hub_client as hc

    real = hc.wait_job

    def _fast(base_url: str, token: str, jid: str, **kw: object) -> dict:
        kw.setdefault("poll_sec", 0.05)
        return real(base_url, token, jid, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(hc, "wait_job", _fast)


def test_cloud_iter_full_chain_is_dashboard_readable(
    tmp_path: Path, fast_wait_poll: None
) -> None:
    """云端同机 rollout+PPO 一轮跑通 → 每 it 权重 + 指标 + job 账本都能被面板读出。"""
    weights = tmp_path / "weights.json"
    init_bytes = b'{"stub_init": "it6"}'
    weights.write_bytes(init_bytes)
    # hub 先起（拿端口），loop 的参数里带 hub_url
    job_root = tmp_path / "traj" / "remote-jobs"
    hub = _Hub(job_root, tmp_path / "traj" / "training_log.jsonl")
    try:
        loop, traj, arch_dir = _args_and_loop(tmp_path, hub.url, weights)
        node = _FakeCloudNode(hub.url, tmp_path / "node-work")
        node.start()

        # ---- 真链路（训练侧在独立线程里跑，主线程限时监督，红了也不陪等 30 分钟超时）----
        raised: list[BaseException] = []

        def _iter() -> None:
            try:
                loop._remote_iter(IT, PAIRS)  # 真发布 → 真等待 → 真三重校验 → 真落位
            except BaseException as e:  # 原样带出给主线程断言（别在后台线程里丢栈）
                raised.append(e)

        run = threading.Thread(target=_iter, name="trainer-iter", daemon=True)
        run.start()
        run.join(timeout=45.0)
        node.join(timeout=10.0)
        assert node.errors == [], f"假云机失败（根因在云侧）：{node.errors}"
        assert raised == [], f"训练侧不该抛异常：{raised!r}"
        assert not run.is_alive(), "训练侧 45s 内未收口（wait_job 挂在半路）"

        # ---- 云侧契约：单 worker 走独占（竞速广播是单课程多卡的开关，不由一个 worker 触发）----
        assert node.lease_token, "领取未下发租约 token（P3b 独占路径）"
        assert node.race is False, "单 worker 轮询不应触发竞速广播"
        assert node.shas["ts_code"] == node.manifest["ts_code_sha256"]  # M3 TS 运行时送达

        # ---- 真后处理两步（与主循环同一顺序：_export_weights → 下游 _record_iteration）----
        loop._export_weights(IT)
        loop._record_iteration(IT)

        # ================= ① 每 it 权重 =================
        # dashboard/src/server/eval-board/ckpts.ts::iterFromCkpt —— `\.it(\d+)\.` 解 iter。
        arch = sorted(arch_dir.glob("*.json"))
        assert len(arch) == 1, f"归档应有且仅有一份：{[p.name for p in arch]}"
        m = re.search(r"\.it(\d+)\.", arch[0].name)  # 与 iterFromCkpt 同正则
        assert m is not None, f"归档名 {arch[0].name!r} 解不出 iter（面板会显示 iter=null）"
        assert int(m.group(1)) == IT
        assert arch[0].read_bytes() == CLOUD_WEIGHTS, "归档的不是云端回传的权重"
        # buildEvalCkptsView 的「活动指针」兜底：tmp/<课>/weights.json。
        assert weights.name == "weights.json"
        assert weights.read_bytes() == CLOUD_WEIGHTS, "活动指针未换成云端回传的权重"
        # opt ckpt：verify_and_land 解包 + 原字节 sibling（下一轮 publish 的 opt_sha 源）。
        ckpt_dir = traj / f"it{IT}" / "ppo_ckpt_remote"
        assert (ckpt_dir / "adam_state.txt").read_bytes() == CLOUD_WEIGHTS
        assert (traj / f"it{IT}" / "ppo_ckpt_remote.tar").exists()

        # ================= ② 每 it 指标 =================
        # dashboard/src/server/iters.ts::readIterMetrics 的读法：event=="iteration" 的行。
        raw_lines = (traj / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
        rows = [json.loads(ln) for ln in raw_lines if ln.strip()]
        iters = [r for r in rows if r.get("event") == "iteration"]
        assert len(iters) == 1, f"应恰好一条 iteration 行：{[r.get('event') for r in rows]}"
        row = iters[0]
        assert row["iter"] == IT
        # time：面板的实际值缓存的匹配键（相等才命中），缺失会让缓存永远失效。
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", str(row["time"]))
        # 字段**存在且非空**（`Number(x ?? 0)` 会把缺字段静默读成 0 —— 这正是要抓的回归）
        for key in (
            "winRate",
            "score_mean",
            "score_std",
            "samples",
            "rollout_sec",
            "ppo_sec",
            "ppo_cloud_sec",
            "kl",
            "entropy",
            "policy",
            "value",
            "mean_ret",
            "dim_means",
        ):
            assert key in row and row[key] is not None, f"面板要读的 {key} 缺席"
        # 逐值对账：这里的数就是假云机自报的那几个（云侧口径贯穿到账本）
        assert row["winRate"] == 0.5  # 一胜一负（combine_reports 真聚合）
        assert row["score_mean"] == 0.1  # (0.4 + -0.2) / 2
        assert row["samples"] == 60
        assert row["dim_means"]["kills"] == 0.5
        assert row["rollout_sec"] == NODE_ROLLOUT_SEC  # 节点侧采集墙钟
        assert row["ppo_cloud_sec"] == CLOUD_PPO_SEC  # 云机真训练秒（不是往返墙钟）
        assert row["kl"] == CLOUD_AGG["kl"]
        assert row["entropy"] == CLOUD_AGG["entropy"]
        assert row["policy"] == CLOUD_AGG["policy"]
        assert row["value"] == CLOUD_AGG["value"]
        assert row["mean_ret"] == CLOUD_AGG["mean_ret"]
        # M3 归因：面板据此区分「本地采集」与「整轮上云」。
        assert row["wire"]["rollout_src"] == "node"
        assert row["ppo_sec"] > 0  # 往返墙钟（含打包/排队/回传）
        # M0 统一计量：hub 实测的传输字节随结果进账本（面板 wire 列的数据源），
        # 且与节点侧自报的 payload_bytes 对得上——两半各测一半、互不覆盖。
        assert row["wire"]["up_bytes"] == node.payload_bytes
        assert row["wire"]["down_bytes"] == node.result_bytes
        assert row["wire"]["worker"]["payload_bytes"] == node.payload_bytes

        # ================= ③ job 账本 =================
        # dashboard/src/server/api/ppo-queue.ts::detectPpoQueueStall 的输入面：
        # 账本 job_pending → job_completed，且目录留下 claimed / result 标记
        # （面板靠它区分「排队等取」与「已在跑」——两者混了就会误报/漏报排队事故）。
        jid = node.job_id
        events = [(r.get("event"), r.get("job_id")) for r in rows]
        assert ("job_pending", jid) in events, f"账本缺 job_pending：{events}"
        assert ("job_completed", jid) in events, f"账本缺 job_completed：{events}"
        assert events.index(("job_pending", jid)) < events.index(("job_completed", jid))
        jd = job_root / jid
        assert (jd / "result" / "result.json").exists(), "hub 未落 result（面板会当悬空 job）"
        assert (jd / "claimed").exists(), "缺 claimed 标记（面板读不到「已被领取」）"
        assert not (jd / "fail.json").exists()
        # 回传结果里带的正是云侧的采集报告（hub 侧无本地 shard 可算，只能信这份）
        stored = json.loads((jd / "result" / "result.json").read_text(encoding="utf-8"))
        assert stored["report"]["winRate"] == 0.5 and stored["report"]["shards"] == len(PAIRS)
        assert decode_opt_tar(stored["opt_tar_b64"])  # opt 段确有其物

        # ================= ④ 双端锚：断言口径必须与面板真源码同版 =================
        _assert_dashboard_readers_unchanged()
    finally:
        hub.close()


# ────────────────────────── 双端锚（跨项目契约哨兵） ──────────────────────────


def _assert_dashboard_readers_unchanged() -> None:
    """双端锚：上面那些断言口径钉在面板**真源码**的字面量上。

    同 `tests/nn/schema-fingerprint.test.ts` 的思路（两端共锚一个字面量）：面板侧把读法
    改掉（换事件名 / 换归档命名解析）而这里没跟 ⇒ 本测试红，而不是静默分叉成「面板读不
    出但没人知道」。只锚**名字级**字面量（事件名、文件名正则），不锚排版——重构不改语义的
    改动不会误红。

    只**只读**面板源码（不 import 它的模块、不跑 bun）：方向纪律（AGENTS §3「反向永远
    禁止」）约束的是代码依赖，这里只是把跨项目契约钉成字面量的哨兵。
    """
    targets = {
        "ckpts": _REPO / "dashboard/src/server/eval-board/ckpts.ts",
        "iters": _REPO / "dashboard/src/server/iters.ts",
        "ppo-queue": _REPO / "dashboard/src/server/api/ppo-queue.ts",
    }
    for p in targets.values():
        assert p.is_file(), f"面板源码缺席 {p}（双端锚需要整仓检出，不能只拷 nn-training/）"
    ckpts = targets["ckpts"].read_text(encoding="utf-8")
    iters = targets["iters"].read_text(encoding="utf-8")
    assert r"\.it(\d+)\." in ckpts, "面板的 ckpt→iter 解析变了（归档命名/本测试必须同步）"
    assert re.search(r"event[^\n]*'iteration'", iters), "面板的 iteration 事件名变了"
    assert "job_pending" in targets["ppo-queue"].read_text(encoding="utf-8"), (
        "面板的 job 账本事件名变了"
    )



if __name__ == "__main__":  # 允许 standalone 直跑（与 e2e/ 其余文件同规）
    raise SystemExit(pytest.main([__file__, "-q"]))
