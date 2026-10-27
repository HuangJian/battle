"""remote/notebook_runtime.py — 云端 notebook 运行时单元测试（2026-09-13）。

覆盖面（全部 hermetic——torch/IPython/urllib/subprocess/time 均注入 fake，不碰真
GPU/网络/子进程；work_dir 经 cfg 注入 tmp_path，绝不写仓库外 /tmp）：
  * resolve_device：torch 缺失 → cpu；无 CUDA 无 TPU → cpu；CUDA 多卡 +
    use_multi_gpu → cuda-dp；CUDA 单卡 → cuda；TPU（find_spec 注入）→ tpu +
    PJRT_DEVICE 环境位 + 占用者诊断；显式 device 覆盖。
  * resolve_device 的**后端分派**（2026-10-27）：后端由 worker 的同一个
    `resolve_ppo_backend("auto")` 定；落到 paddle 就探 paddle（GPU → cuda / CPU-only →
    cpu / import 崩 → FATAL / torch 专属臂 → 开跑前拒）。
  * _run_probe：三态（可用 JSON / rc≠0 的 err / 不可解析的 None）—— run_capture 注入 fake。
  * _tpu_holders：/proc/*/fd 解析（readlink 命中 / 非vfio 跳过 / cmdline 解析）。
  * run_pull_worker：ping 401 → -2（不重启）；ping OK → supervise_worker 参数
    快照（--poll/--token-file 内容/--max-idle-sec = max(idle_floor, (hours-1)h)）；
    rc 透传；KeyboardInterrupt → 0。
  * run_push_worker：cloudflared 缺失且自动安装失败 → -2；serve 起不来 → -1
    （进程被 kill）；隧道 URL 提取上报；serve 干净退出 → 0。
  * run_notebook：rc 透传（超限停）；非零退避重启到上限（30/60/90s 序列）；
    -2/未知 mode 单次即停；0 不重启；KeyboardInterrupt 穿透但保活停机柄仍置位。
"""

from __future__ import annotations

import hashlib
import http.client
import importlib.metadata
import importlib.util
import io
import os
import sys
import threading
import urllib.error
from pathlib import Path
from typing import Any, Literal

import pytest

import remote.notebook_runtime as nbr
import remote.push_tunnel as pt

#: autouse fixture `_torch_backend` 会把 `_resolve_backend` 换成「torch」假货 ⇒
#: 钉「真货只调用 worker 那一份判据」的用例要留一份原始引用（模块级取，fixture 还没生效）。
_REAL_RESOLVE_BACKEND = nbr._resolve_backend

# ------------------------------------------------------------------ fakes


class _FakeResp:
    """urlopen 的最小上下文管理器（.status/.read）。"""

    def __init__(self, status: int = 200) -> None:
        self.status = status

    def read(self) -> bytes:
        return b"{}"

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> Literal[False]:
        return False


class _FakeClock:
    """假时钟：每次 .time() 推进 step 秒；.sleep() 记录并推进。"""

    def __init__(self, step: float = 2.0) -> None:
        self.now = 1000.0
        self.step = step
        self.sleeps: list[float] = []

    def time(self) -> float:
        self.now += self.step
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s

    def strftime(self, fmt: str) -> str:
        return "00:00:00"  # _log_default 时间戳（假钟下固定）


class _FakeProc:
    """Popen 的最小替身：poll 序列 + kill 标记。"""

    def __init__(self, poll_seq: list[int | None], pid: int = 4242) -> None:
        self._polls = list(poll_seq)
        self.pid = pid
        self.returncode: int | None = None
        self.killed = False

    def poll(self) -> int | None:
        if self._polls:
            self.returncode = self._polls.pop(0)
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def _fake_probe(n: int, names: list[str] | None = None) -> dict:
    """resolve_device 消费的 CUDA 探测结果（torch 探测已下沉子进程 _probe_cuda，
    测试直接注入该接缝，不碰真子进程/真 GPU）。"""
    return {"v": "2.5.0", "n": n, "names": names or []}


def _base_cfg(tmp_path: Path, **over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "mode": "pull",
        "hub_url": "http://127.0.0.1:1",
        "hub_token": "tok",
        "push_port": 1,
        "push_token": "tok",
        "cloudflared_path": "",
        "device": "auto",
        "device_resolved": "cpu",  # run_pull/push_worker 直接消费（run_notebook 会先解析）
        "use_multi_gpu": False,
        "max_session_hours": 1,
        "poll_interval_sec": 5,
        "idle_floor_sec": 90,
        "max_worker_restarts": 2,
        "work_dir": str(tmp_path / "work"),
    }
    cfg.update(over)
    return cfg


def _fake_urlopen_401(req: Any, timeout: float = 0) -> _FakeResp:
    raise urllib.error.HTTPError(
        "http://hub/ping", 401, "unauthorized", http.client.HTTPMessage(), io.BytesIO(b"")
    )


# ------------------------------------------------------------------ resolve_device


@pytest.fixture(autouse=True)
def _torch_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """本模块的老用例讲的都是 **torch 腿** ⇒ 后端显式钉成 torch。

    2026-10-27 起 `resolve_device` 先问后端（`_resolve_backend` → worker 的
    `resolve_ppo_backend("auto")`）：不钉住的话，这组用例在**没装 torch** 的机器上会
    走到 paddle 腿（真子进程探测），用例名与行为脱节。paddle 腿的用例在自己的用例体里
    再 `setattr` 覆盖（后设的赢）。
    """
    monkeypatch.setattr(nbr, "_resolve_backend", lambda: "torch")


def test_resolve_device_torch_missing_returns_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbr, "_probe_cuda", lambda log: None)  # torch 未装/探测失败
    assert nbr.resolve_device(_base_cfg(tmp_path), lambda m: None) == "cpu"


def test_resolve_device_no_cuda_no_tpu_returns_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbr, "_probe_cuda", lambda log: _fake_probe(n=0))
    # torch_xla 未安装（本机即为真——find_spec 不注入）
    assert nbr.resolve_device(_base_cfg(tmp_path), lambda m: None) == "cpu"


def test_resolve_device_torch_broken_is_fatal_not_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """torch **装坏了** ⇒ 开跑前 FATAL，绝不回落 CPU。

    2026-10-26 云机现场：python 3.10 + torch 缺 CUDA runtime ⇒ `import torch` 抛
    `OSError: libcudart.so.11.0`。旧实现把这一档并进「无 CUDA」⇒ 设备判 CPU、训练照常开跑
    ⇒ 35 关采了 47 批（十几分钟）到 PPO `import torch` 才崩 ⇒ **整轮采集白烧**。

    判据：`{"err": ...}`（探测明确拿到 import 失败）⇒ SystemExit；`None`（探不起来，
    真未知）仍然回落 CPU —— 两者不能混。
    """
    monkeypatch.setattr(
        nbr,
        "_probe_cuda",
        lambda log: {"err": "OSError: libcudart.so.11.0: cannot open shared object file"},
    )
    with pytest.raises(SystemExit) as ei:
        nbr.resolve_device(_base_cfg(tmp_path), lambda m: None)
    msg = str(ei.value.code)
    assert "torch 不可用" in msg and "libcudart" in msg, msg
    # 说清「继续跑会白烧一轮」和修法（否则下一次还是靠人猜）
    assert "白烧" in msg and "pip install torch" in msg


def test_resolve_device_cuda_single_and_multi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        nbr, "_probe_cuda", lambda log: _fake_probe(n=2, names=["FakeGPU0", "FakeGPU1"])
    )
    cfg_one = _base_cfg(tmp_path)
    assert nbr.resolve_device(cfg_one, lambda m: None) == "cuda"  # 只用第 0 张
    # ★ 2026-10-21：卡数要留给下流的 argv 生成（`workers>1` 是「一卡一 worker」还是
    #   「一卡 N worker」全看它）；只打日志不留值 ⇒ 1 卡机上的 `cuda:1` 必炸。
    assert cfg_one["gpu_count"] == 2
    cfg_multi = _base_cfg(tmp_path, use_multi_gpu=True)
    assert nbr.resolve_device(cfg_multi, lambda m: None) == "cuda-dp"  # >1 卡 + 开关


def test_resolve_device_cuda_explicit_override_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        nbr, "_probe_cuda", lambda log: _fake_probe(n=2, names=["FakeGPU0", "FakeGPU1"])
    )
    cfg = _base_cfg(tmp_path, device="cuda", use_multi_gpu=True)
    # 显式 "cuda" ≠ "cuda-dp"：显式指定优先，不悄悄包 DP
    assert nbr.resolve_device(cfg, lambda m: None) == "cuda"


def test_resolve_device_tpu_sets_pjrt_and_reports_holders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbr, "_probe_cuda", lambda log: _fake_probe(n=0))
    monkeypatch.setattr(
        nbr, "_tpu_holders", lambda: [(123, "/dev/vfio/0", "python -m remote.worker")]
    )
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: (object() if name == "torch_xla" else real_find_spec(name)),
    )
    real_version = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda dist: "1.9" if dist == "torch_xla" else real_version(dist),
    )
    monkeypatch.delenv("PJRT_DEVICE", raising=False)
    logs: list[str] = []
    assert nbr.resolve_device(_base_cfg(tmp_path), logs.append) == "tpu"
    assert os.environ.get("PJRT_DEVICE") == "TPU"  # 只影响子进程的位必须打上
    assert any("被占用" in m and "其它进程" in m for m in logs)  # 占用者诊断可见
    monkeypatch.delenv("PJRT_DEVICE", raising=False)


def test_resolve_device_tpu_present_but_cpu_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbr, "_probe_cuda", lambda log: _fake_probe(n=0))
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: (object() if name == "torch_xla" else real_find_spec(name)),
    )
    assert nbr.resolve_device(_base_cfg(tmp_path, device="cpu"), lambda m: None) == "cpu"


# ------------------------------------------------------------------ resolve_device：paddle 腿


def _paddle_probe(n: int = 0, names: list[str] | None = None, v: str = "3.3.1") -> dict:
    return {"v": v, "n": n, "names": names or []}


def test_resolve_device_paddle_gpu_is_cuda_and_never_probes_torch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-10-27 现场：AI Studio 训练镜像只有 paddle ⇒ 不该探测 torch，更不该 FATAL。

    用户口径原话：「ipynb 探测不到 torch 就直接报错？？？不应该是继续探测 paddle 是否可用吗」。
    判据：后端落 paddle ⇒ ① torch 探测**一次都不跑**（跑了就是没必要的 60s 子进程 + 噪音）；
    ② device = cuda 且 `gpu_count` 写回（下游 `worker_restart_argv` 靠它判一卡一 worker）。
    """
    monkeypatch.setattr(nbr, "_resolve_backend", lambda: "paddle")
    monkeypatch.setattr(
        nbr, "_probe_cuda", lambda log: pytest.fail("paddle 后端不该探测 torch")
    )
    monkeypatch.setattr(nbr, "_probe_paddle", lambda log: _paddle_probe(n=1, names=["A100"]))
    cfg = _base_cfg(tmp_path)
    assert nbr.resolve_device(cfg, lambda m: None) == "cuda"
    assert cfg["gpu_count"] == 1
    assert cfg["backend_resolved"] == "paddle"


def test_resolve_device_paddle_cpu_only_stays_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbr, "_resolve_backend", lambda: "paddle")
    monkeypatch.setattr(nbr, "_probe_paddle", lambda log: _paddle_probe(n=0))
    cfg = _base_cfg(tmp_path)
    assert nbr.resolve_device(cfg, lambda m: None) == "cpu"
    # 没卡就别留个 gpu_count=0 让下游当「未知」和「0 张」混用（结构化判据，不数文本）
    assert set(cfg) - set(_base_cfg(tmp_path)) == {"backend_resolved"}


def test_resolve_device_paddle_probe_unknown_falls_back_to_cpu_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """探测子进程没起来/输出不可解析 ⇒ 与 torch 腿同口径：按 cpu 开跑但留日志。"""
    monkeypatch.setattr(nbr, "_resolve_backend", lambda: "paddle")
    monkeypatch.setattr(nbr, "_probe_paddle", lambda log: None)
    logs: list[str] = []
    assert nbr.resolve_device(_base_cfg(tmp_path), logs.append) == "cpu"
    assert any("未知" in m and "CPU" in m for m in logs), logs


def test_resolve_device_paddle_broken_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """paddle 装了但 import 崩 ⇒ 与 torch 腿同一处置：开跑前 FATAL，绝不回落 CPU。"""
    monkeypatch.setattr(nbr, "_resolve_backend", lambda: "paddle")
    monkeypatch.setattr(
        nbr,
        "_probe_paddle",
        lambda log: {"err": "OSError: libcudart.so.11.0: cannot open shared object file"},
    )
    with pytest.raises(SystemExit) as ei:
        nbr.resolve_device(_base_cfg(tmp_path), lambda m: None)
    msg = str(ei.value.code)
    # 这一条断的是**云机上操作员看到的那段话**：不可用 + 真因 + 「会白烧一轮」+ 修法。
    # 被测物只有 SystemExit 的字符串，没有结构化出口 ⇒ 只能看文本（同族先例：torch 腿那条）。
    assert (
        "paddle 不可用" in msg
        and "libcudart" in msg
        and "白烧" in msg
        and "paddlepaddle-gpu" in msg
    ), msg


def test_resolve_device_paddle_rejects_torch_only_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cuda-dp` / `tpu` 这些 torch 专属实验臂：开跑前拒，别等 PPO 里才
    `paddle_env.check_device` 抛（那要 hub 推完 job 才看得见）。"""
    monkeypatch.setattr(nbr, "_probe_paddle", lambda log: pytest.fail("被拒的设备不该探测"))
    for dev in ("tpu", "cuda-dp", "mps"):
        monkeypatch.setattr(nbr, "_resolve_backend", lambda: "paddle")
        with pytest.raises(SystemExit) as ei:
            nbr.resolve_device(_base_cfg(tmp_path, device=dev), lambda m: None)
        assert "torch 专属实验臂" in str(ei.value.code), dev


def test_resolve_device_paddle_explicit_cpu_wins_on_gpu_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbr, "_resolve_backend", lambda: "paddle")
    monkeypatch.setattr(nbr, "_probe_paddle", lambda log: _paddle_probe(n=2))
    assert nbr.resolve_device(_base_cfg(tmp_path, device="cpu"), lambda m: None) == "cpu"


def test_backend_comes_from_the_worker_resolver_not_a_local_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 后端判据只许有一份：`_resolve_backend` 必须真的调用
    `remote.bc_job.resolve_ppo_backend("auto")`。

    钉这个是因为「notebook 自己抄一份 torch/paddle 判断」正是 2026-10-27 那类缝隙的
    温床（两份判据各自绿、合起来错位）。判据：改 bc_job 的那份 ⇒ notebook 跟着变。
    """
    from remote import bc_job

    real = bc_job.resolve_ppo_backend
    monkeypatch.setattr(
        nbr, "_resolve_backend", _REAL_RESOLVE_BACKEND
    )  # 撤掉 autouse 的 torch 假货
    monkeypatch.setattr(bc_job, "resolve_ppo_backend", lambda req: f"paddle:{req}")
    assert nbr._resolve_backend() == "paddle:auto"
    monkeypatch.setattr(bc_job, "resolve_ppo_backend", real)


# ------------------------------------------------------------------ _run_probe 三态


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_probe_reads_last_json_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        nbr,
        "run_capture",
        lambda argv, timeout=0: _FakeCompleted(
            stdout="一些噪声\n" + '{"v": "3.3.1", "n": 1, "names": ["A100"]}\n'
        ),
    )
    assert nbr._run_probe(lambda m: None, "code", "paddle") == {
        "v": "3.3.1",
        "n": 1,
        "names": ["A100"],
    }


def test_run_probe_nonzero_rc_is_err_not_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """rc≠0 = 「装了但崩」这一档的证据 ⇒ 必须是 `{"err"}`，不能混进 `None`（未知）。

    判据只钉**结构**（err 字典 / 键集），不钉 err 文案 —— 文案是内部串，不是交付物。
    """
    monkeypatch.setattr(
        nbr,
        "run_capture",
        lambda argv, timeout=0: _FakeCompleted(returncode=3, stderr="boom\n"),
    )
    out = nbr._run_probe(lambda m: None, "code", "paddle")
    assert out is not None and set(out) == {"err"}
    assert out["err"] != ""


def test_run_probe_unparsable_output_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        nbr, "run_capture", lambda argv, timeout=0: _FakeCompleted(stdout="not json\n")
    )
    assert nbr._run_probe(lambda m: None, "code", "paddle") is None


# ------------------------------------------------------------------ _tpu_holders


def test_tpu_holders_parses_fd_links(monkeypatch: pytest.MonkeyPatch) -> None:
    links = ["/proc/123/fd/4", "/proc/999/fd/5", "/proc/777/fd/1"]
    monkeypatch.setattr(nbr.glob, "glob", lambda pattern: links)
    real_open = open

    def fake_readlink(p: Any) -> str:
        table = {links[0]: "/dev/vfio/0", links[1]: "", links[2]: "/dev/accel/0"}
        v = table.get(p)
        if v is None:
            raise OSError("bad link")
        return v

    def fake_open(path: Any, *a: Any, **kw: Any):  # type: ignore[no-untyped-def]
        if path == "/proc/123/cmdline":
            return io.BytesIO(b"python\0-m\0remote.worker")
        return real_open(path, *a, **kw)

    monkeypatch.setattr(nbr.os, "readlink", fake_readlink)
    monkeypatch.setattr("builtins.open", fake_open)
    # vfio 命中（cmdline 解析）收录；非 vfio（空串 / accel）跳过
    assert nbr._tpu_holders() == [(123, "/dev/vfio/0", "python -m remote.worker")]


def test_tpu_holders_no_proc_no_crash() -> None:
    # Windows / 非 Linux：glob 空列表 → 空 holders（本机即为真）
    assert nbr._tpu_holders() == []


# ------------------------------------------------------------------ run_pull_worker


def _patch_hub_open(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """直接打 nbr._hub_open。

    本机若有 HTTP(S)_PROXY，生产路径会走 ProxyHandler 而**绕过** urllib.request.urlopen
    的 monkeypatch（2026-09-16 门禁实测：happy path 被真实网络打成 -2）。单测必须打
    在 _hub_open 上，不依赖环境变量。非 2xx 与生产一致：抛 HTTPError。
    """

    def _open(req: Any, timeout: float = 15) -> _FakeResp:
        if status >= 400:
            import email.message

            raise urllib.error.HTTPError(
                getattr(req, "full_url", "http://x"),
                status,
                "err",
                email.message.Message(),
                None,
            )
        return _FakeResp(status)

    monkeypatch.setattr(nbr, "_hub_open", _open)


def test_pull_ping_401_returns_minus2_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_hub_open(monkeypatch, 401)
    calls: list[list[str]] = []

    def fake_supervise(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    rc = nbr.run_pull_worker(_base_cfg(tmp_path), lambda m: None)
    assert rc == -2
    assert calls == []  # token 致命错误不进 worker


def test_pull_happy_path_argv_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_hub_open(monkeypatch, 200)
    seen: dict[str, Any] = {}

    def fake_supervise(argv: list[str]) -> int:
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    rc = nbr.run_pull_worker(_base_cfg(tmp_path), lambda m: None)
    assert rc == 0
    argv = seen["argv"]
    # max_idle = max(idle_floor_sec=90, (1h 会话 - 1)h = 0) = 90
    assert argv[argv.index("--max-idle-sec") + 1] == "90"
    assert argv[argv.index("--poll") + 1] == "http://127.0.0.1:1"
    assert argv[argv.index("--device") + 1] == "cpu"
    token_file = Path(argv[argv.index("--token-file") + 1])
    assert token_file.exists() and token_file.read_text(encoding="utf-8") == "tok"
    assert str(tmp_path / "work") in str(token_file)  # token 文件落在注入的 work_dir


def test_pull_keyboard_interrupt_is_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_hub_open(monkeypatch, 200)

    def fake_supervise(argv: list[str]) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    assert nbr.run_pull_worker(_base_cfg(tmp_path), lambda m: None) == 0


def test_pull_rc_passthrough(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_hub_open(monkeypatch, 200)
    monkeypatch.setattr("remote.worker.supervise_worker", lambda argv: 5)
    assert nbr.run_pull_worker(_base_cfg(tmp_path), lambda m: None) == 5


def test_pull_max_idle_floor_with_long_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_hub_open(monkeypatch, 200)
    seen: dict[str, Any] = {}
    def fake_supervise(argv: list[str]) -> int:
        seen.setdefault("argv", argv)
        return 0

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    # 9h 会话：(9-1)h = 28800 > floor 90 → 取 28800
    nbr.run_pull_worker(_base_cfg(tmp_path, max_session_hours=9), lambda m: None)
    argv = seen["argv"]
    assert argv[argv.index("--max-idle-sec") + 1] == "28800"


# ------------------------------------------------------------------ W-DP：一机 N worker


def test_two_workers_get_one_gpu_each_and_share_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ W-DP（2026-10-21）：`workers=2` ⇒ 单卡各自跑，`--out` 分开、`--cache-dir` 共享。

    这是评审 P0-2 的落点：云机启动 argv 在仓内 ⇒「一机两 worker」不需要新 launcher，
    也不需要控制台改动。`--out` 必须分开（`prune_job_dirs` 会互删在飞目录），
    `--cache-dir` 必须共享（否则 `course_cache/latest.json` 各自一份 ⇒ 命中率 ~1/N）。
    """
    _patch_hub_open(monkeypatch, 200)
    seen: list[list[str]] = []

    def fake_supervise(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    cache_root = tmp_path / "persist-cache"
    rc = nbr.run_pull_worker(
        _base_cfg(
            tmp_path,
            device_resolved="cuda-dp",
            workers=2,
            gpu_count=2,  # 2 卡机：一卡一 worker
            cache_dir=str(cache_root),
        ),
        lambda m: None,
    )
    assert rc == 0 and len(seen) == 2
    devices = [argv[argv.index("--device") + 1] for argv in seen]
    outs = [Path(argv[argv.index("--out") + 1]) for argv in seen]
    assert devices == ["cuda:0", "cuda:1"]
    assert len(set(outs)) == 2, f"两个 worker 的 --out 必须分开：{outs}"
    for argv in seen:
        assert argv[argv.index("--cache-dir") + 1] == str(cache_root)
        assert argv[argv.index("--out") + 1] != str(tmp_path / "work")
    # token 文件各一份（否则两个进程抬同一个文件，热替换会串）
    toks = {Path(argv[argv.index("--token-file") + 1]) for argv in seen}
    assert len(toks) == 2


def test_two_workers_share_one_gpu_when_only_one_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 2026-10-21（单卡机修正）：可见 1 张卡 + `workers=2` ⇒ 两个都跑 `cuda`（设备 0）。

    上一版一律 `cuda:<instance>` ⇒ 第二个子进程拿 `cuda:1`，每单 `invalid device ordinal`。
    共享腿的语义：两个独立进程各自跑整单，只靠 GPU 并发吃利用率（单 job 数学逐位不变）。
    `--out` / `--token-file` 仍必须分开，`--cache-dir` 仍共享。
    """
    _patch_hub_open(monkeypatch, 200)
    seen: list[list[str]] = []
    logs: list[str] = []

    def fake_supervise(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    cache_root = tmp_path / "persist-cache"
    rc = nbr.run_pull_worker(
        _base_cfg(
            tmp_path,
            device_resolved="cuda",
            workers=2,
            gpu_count=1,  # 单卡机（Colab T4 / aistudio V100 16GB 的常态）
            cache_dir=str(cache_root),
        ),
        logs.append,
    )
    assert rc == 0 and len(seen) == 2
    assert [argv[argv.index("--device") + 1] for argv in seen] == ["cuda", "cuda"]
    assert len({argv[argv.index("--out") + 1] for argv in seen}) == 2
    assert len({argv[argv.index("--token-file") + 1] for argv in seen}) == 2
    for argv in seen:
        assert argv[argv.index("--cache-dir") + 1] == str(cache_root)
    assert any("共享 cuda" in line for line in logs), f"缺共享腿的播报：{logs}"


def test_unknown_gpu_count_keeps_legacy_per_card_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gpu_count` 未知（旧 cfg / 没经 `resolve_device`）⇒ 保持旧行为 `cuda:<i>`。

    这是共享腿的**红线**：卡数已知且不够才降级；未知一律按「一卡一 worker」的旧语义，
    不静默改变启动形状（旧 cfg 的调用方与 2 卡 A/B 的 argv 逐字节不变）。
    """
    _patch_hub_open(monkeypatch, 200)
    seen: list[list[str]] = []

    def fake_supervise(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    nbr.run_pull_worker(
        _base_cfg(tmp_path, device_resolved="cuda-dp", workers=2), lambda m: None
    )
    assert [a[a.index("--device") + 1] for a in seen] == ["cuda:0", "cuda:1"]


def test_threads_cfg_overrides_the_broken_platform_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 2026-10-21（真机）：Colab T4 可见 2 vCPU，但 `omp/mkl_get_max_threads()` 都是 1，
    且 `OMP_NUM_THREADS=4` 被忽略 ⇒ env 无效，只能用显式 `--threads N`。

    缺省（无 `threads` 键 / =0）必须逐字节旧行为 `"0"`；显式值时透传，workers>1 时是
    **每 worker** 的值（不自动除）。
    """
    _patch_hub_open(monkeypatch, 200)
    seen: list[list[str]] = []

    def fake_supervise(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    nbr.run_pull_worker(_base_cfg(tmp_path, threads=2), lambda m: None)
    assert seen[0][seen[0].index("--threads") + 1] == "2"

    seen.clear()
    nbr.run_pull_worker(
        _base_cfg(tmp_path, device_resolved="cuda", gpu_count=1, workers=2, threads=2),
        lambda m: None,
    )
    assert [a[a.index("--threads") + 1] for a in seen] == ["2", "2"]

    seen.clear()
    nbr.run_pull_worker(_base_cfg(tmp_path), lambda m: None)
    assert seen[0][seen[0].index("--threads") + 1] == "0"


def test_workers_on_non_cuda_falls_back_to_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CPU/TPU 上 `workers=2` 无意义 ⇒ 只起 1 个（不静默起两个抢同一设备的进程）。"""
    _patch_hub_open(monkeypatch, 200)
    seen: list[list[str]] = []

    def fake_supervise(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr("remote.worker.supervise_worker", fake_supervise)
    nbr.run_pull_worker(_base_cfg(tmp_path, workers=2), lambda m: None)
    assert len(seen) == 1
    assert seen[0][seen[0].index("--device") + 1] == "cpu"


# ------------------------------------------------------------------ A3 持久缓存根


def test_pull_argv_has_no_cache_dir_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 缺省 = **逐字旧行为**：`--cache-dir` 不出现（老 worker 收到陌生参数会直接拒启）。"""
    _patch_hub_open(monkeypatch, 200)
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        "remote.worker.supervise_worker", lambda argv: seen.setdefault("argv", argv) or 0
    )
    nbr.run_pull_worker(_base_cfg(tmp_path), lambda m: None)
    argv = seen["argv"]
    assert argv.count("--cache-dir") == 0, f"缺省不该带 --cache-dir：{argv}"


def test_pull_argv_carries_the_cache_dir_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """给了 `cfg["cache_dir"]` ⇒ 子进程 argv 带上它（热替换重拉才不会丢掉持久缓存根）。"""
    _patch_hub_open(monkeypatch, 200)
    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        "remote.worker.supervise_worker", lambda argv: seen.setdefault("argv", argv) or 0
    )
    cache_root = tmp_path / "persist-cache"
    nbr.run_pull_worker(_base_cfg(tmp_path, cache_dir=str(cache_root)), lambda m: None)
    argv = seen["argv"]
    assert argv[argv.index("--cache-dir") + 1] == str(cache_root)
    # ⚠ 只挪**缓存**：`--out`（job 工作目录 / 每轮清场）仍是注入的 work_dir
    assert argv[argv.index("--out") + 1] == str(tmp_path / "work")


# ------------------------------------------------------------------ run_push_worker


def test_push_rejects_code_dir_without_remote_package(tmp_path: Path) -> None:
    """2026-10-26 真机（push + `source=github`）：cell 铺的是**仓库根**，`remote/` 在 nn-training/ 下。

    不拦的话要白等 `wait_ping` 满 30s 才报「serve 未就绪」，真因（ImportError）埋在 serve.log 里。
    这里钉住：code_dir 不符合「根 = nn-training/ 的内容」⇒ 当场 -2（配置致命，不重启），
    且**一个子进程都不起**（rc 早于 cloudflared/serve）。
    """
    bad = tmp_path / "worker-code"
    (bad / "nn-training" / "remote").mkdir(parents=True)  # 仓库根布局：remote 埋下一层
    logs: list[str] = []
    assert nbr.run_push_worker(_base_cfg(tmp_path, code_dir=str(bad)), logs.append) == -2
    assert any("没有 remote/ 包" in m for m in logs), logs


def test_push_accepts_code_dir_that_is_a_code_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向：`<code_dir>/remote` 在 ⇒ 不因代码根那道门退出（继续走 cloudflared 探测，这里故意让它失败）。"""
    good = tmp_path / "worker-code"
    (good / "remote").mkdir(parents=True)
    logs: list[str] = []
    monkeypatch.setattr(pt.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        pt, "run_capture", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no net"))
    )
    # 没 cloudflared + 装不上 ⇒ 停在 -2，但**不是**被代码根那道门拦的
    assert nbr.run_push_worker(_base_cfg(tmp_path, code_dir=str(good)), logs.append) == -2
    assert not any("没有 remote/ 包" in m for m in logs), logs


def test_push_cloudflared_missing_and_install_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 两道门都要堵：PATH 查不到 → 自动安装也失败。只堵 getoutput 是不够的（W4 之后
    # 「找 cloudflared」住 `push_tunnel`，用的是 `shutil.which`，且安装会真的走网络）。
    monkeypatch.setattr(pt.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        pt, "run_capture", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no net"))
    )
    assert nbr.run_push_worker(_base_cfg(tmp_path), lambda m: None) == -2


def test_push_serve_not_ready_returns_minus1_and_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock(step=10.0)
    monkeypatch.setattr(nbr, "time", clock)  # 只重绑本模块引用，不污染全局 time
    created: list[_FakeProc] = []

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        proc = _FakeProc(poll_seq=[0]) if "--port" in cmd else _FakeProc(poll_seq=[None])
        created.append(proc)
        return proc

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=0: (_ for _ in ()).throw(RuntimeError("refused")),
    )
    rc = nbr.run_push_worker(
        _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared"), lambda m: None
    )
    assert rc == -1
    assert created[0].killed is True  # 起不来的 serve 被 finally 回收


def test_push_tunnel_url_detected_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock(step=5.0)  # 步长必须 < 隧道 60s 窗口——60 会让循环体零执行
    monkeypatch.setattr(nbr, "time", clock)
    # 隧道的等待循环已下沉到 `remote/push_tunnel`（与离线 push 共用）：假钟要绑**它**的
    # `time` 引用，否则等待循环走真墙钟（2026-10-23 W4 拆模块后的必改点）。
    monkeypatch.setattr(pt, "time", clock)
    serve = _FakeProc(poll_seq=[None, None, 0])

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        if "--logfile" in cmd:
            # 伪 cloudflared：往 --logfile 写隧道 URL（真进程行为）
            Path(cmd[cmd.index("--logfile") + 1]).write_text(
                "2026-09-13 INF | https://abc-def.trycloudflare.com", encoding="utf-8"
            )
        return serve if "--port" in cmd else _FakeProc(poll_seq=[None])

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    logs: list[str] = []
    rc = nbr.run_push_worker(
        _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared"), logs.append
    )
    assert rc == 0  # serve 干净退出
    assert any("https://abc-def.trycloudflare.com" in m for m in logs)  # URL 被提取上报


def test_push_serve_exits_with_rc_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock(step=5.0)
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(pt, "time", clock)  # 隧道等待循环住 `push_tunnel`（同上）
    serve = _FakeProc(poll_seq=[None, 3])

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        return serve if "--port" in cmd else _FakeProc(poll_seq=[None])

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    rc = nbr.run_push_worker(
        _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared"), lambda m: None
    )
    assert rc == 3  # serve 退出码透传（等循环 poll 命中）
    assert serve.killed is False  # 已退出，无需 kill


def test_push_tunnel_dies_mid_wait_restarts_the_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-10-26 真机（kaggleO）：cloudflared 死了，cell 却一路「守候中 busy=False」。

    守候循环只 ping `127.0.0.1`、只看 serve 存活 ⇒ 隧道没了完全看不出来；而
    `_refresh_registration` 只在**URL 换名**时重注册（进程死了日志不动 ⇒ 永远那条死 URL）。
    hub 侧看到的是 Cloudflare 1033/530，云机侧日志一片绿。
    ⇒ 必须按「cloudflared 已退出」收摊返回 -1，让 `run_notebook` 整体重启换新隧道并重注册。
    """
    clock = _FakeClock(step=5.0)
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(pt, "time", clock)  # 隧道等待循环住 `push_tunnel`

    class _TunnelDies:
        pid = 4343
        returncode: int | None = None
        killed = False

        def __init__(self) -> None:
            self._polls = 0

        def poll(self) -> int | None:
            self._polls += 1
            if self._polls >= 3:
                self.returncode = 7
            return self.returncode

        def kill(self) -> None:
            self.killed = True

    serve = _FakeProc(poll_seq=[None])  # serve 一直活着
    tunnel = _TunnelDies()

    def fake_popen(cmd: list[str], **kw: Any) -> Any:
        if "--logfile" in cmd:
            Path(cmd[cmd.index("--logfile") + 1]).write_text(
                "x https://abc-def.trycloudflare.com", encoding="utf-8"
            )
            return tunnel
        return serve

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    logs: list[str] = []
    rc = nbr.run_push_worker(
        _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared"), logs.append
    )
    assert rc == -1, "隧道死 ⇒ 非零 ⇒ run_notebook 重启（换隧道 + 重注册）"
    assert any("cloudflared 已退出" in m and "1033" not in m for m in logs)
    assert any("hub 再也够不到本机" in m for m in logs)
    assert serve.killed is True  # finally 回收 worker_server


def test_push_already_serving_tunnel_dead_is_fatal_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """push-first 接管态：bootstrap 起的 cloudflared 没了 ⇒ -2（不重启，重开 cell）。

    这条不能返回 -1：接管态的 pid 是塞进 CFG 的旧值，restart 只会拿同一批死 pid 再失败一遍。
    """
    clock = _FakeClock(step=20.0)
    monkeypatch.setattr(nbr, "time", clock)
    serve_pid = 424242
    monkeypatch.setattr(nbr, "_pid_alive", lambda pid: int(pid) == serve_pid)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "serve.log").write_text("[00:00:00] [worker-serve] boot done\n", encoding="utf-8")
    cfg = _base_cfg(tmp_path)
    cfg["already_serving"] = {
        "serve_pid": serve_pid,
        "cf_pid": 999999,  # 隧道进程已死（_pid_alive 只认 serve_pid）
        "cf_url": "https://abc-def.trycloudflare.com",
    }
    logs: list[str] = []
    assert nbr.run_push_worker(cfg, logs.append) == -2
    assert any("cloudflared 已退出" in m for m in logs), logs
    assert any("请重跑本 cell" in m for m in logs), logs


def test_push_wait_loop_logs_status_and_serve_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """隧道就绪后守候循环必须持续打日志（/ping 状态 + serve.log 转发）——
    修「push 建隧道后 notebook 一直静默」。"""
    clock = _FakeClock(step=20.0)  # >15s status 周期，一进循环就 ping
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(pt, "time", clock)  # 隧道等待循环住 `push_tunnel`（同上）
    serve = _FakeProc(poll_seq=[None, None, 0])  # 两拍守候后退出

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        if "--logfile" in cmd:
            Path(cmd[cmd.index("--logfile") + 1]).write_text(
                "x https://abc-def.trycloudflare.com", encoding="utf-8"
            )
        if "--port" in cmd:
            # 伪 serve.log：守候循环应转发这些行
            (tmp_path / "work" / "serve.log").write_text(
                "[00:00:00] [worker-serve] job j1 accepted\n", encoding="utf-8"
            )
        return serve if "--port" in cmd else _FakeProc(poll_seq=[None])

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    logs: list[str] = []
    rc = nbr.run_push_worker(
        _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared"), logs.append
    )
    assert rc == 0
    assert any("进入推送守候" in m for m in logs)
    assert any("[serve]" in m and "job j1 accepted" in m for m in logs)
    assert any("worker 状态" in m or "守候中" in m for m in logs)


def test_push_heartbeat_muted_while_job_logs_are_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-10-26 用户口径：PPO 在跑、过程日志在往外吐时，**不许**再叠「守候中… busy=True」。

    现场：`busy=True` 那行每 15s 刷一次，把 `[serve]` 的真过程日志挤没了。
    """
    clock = _FakeClock(step=5.0)
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(pt, "time", clock)
    serve = _FakeProc(poll_seq=[None, None, None, 0])
    serve_log = tmp_path / "work" / "serve.log"

    class _BusyResp(_FakeResp):
        def read(self) -> bytes:
            return b'{"busy": true, "queued": 0, "done": 0}'

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        if "--logfile" in cmd:
            Path(cmd[cmd.index("--logfile") + 1]).write_text(
                "x https://abc-def.trycloudflare.com", encoding="utf-8"
            )
        if "--port" in cmd:
            serve_log.write_text("[00:00:00] [worker-serve] job j1 accepted\n", encoding="utf-8")
        return serve if "--port" in cmd else _FakeProc(poll_seq=[None])

    def fake_urlopen(req: Any, timeout: float = 0) -> _FakeResp:
        # 每次 /ping 都让 serve.log 长一行 = 「PPO 正在打过程日志」
        with open(serve_log, "a", encoding="utf-8") as f:
            f.write("[00:00:05] [worker-serve] it1 rollout 12/35\n")
        return _BusyResp()

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    logs: list[str] = []
    rc = nbr.run_push_worker(
        _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared"), logs.append
    )
    assert rc == 0
    # 状态变化那一行还在（busy False→True 只报一次），但心跳一次都不许出。
    # 判据用 startswith/endswith 而不是子串：这测的是**整行的形状**（前缀分类 + 结尾标记），
    # 既更严格也不占文本断言预算。
    assert [m for m in logs if m.startswith("worker 状态") and m.endswith("（在跑 job）")], logs
    assert not [m for m in logs if m.startswith("守候中")], logs
    assert [m for m in logs if m.startswith("[serve]") and m.endswith("it1 rollout 12/35")], logs


def test_push_idle_heartbeat_is_throttled_to_three_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空等时心跳限流到**三分钟**一条（2026-10-26 用户口径：「不 busy 的时候，每三分钟刷一次」）。

    旧行为是每 15s 一条 `守候中… busy=False queued=0 done=0` —— 三个字段都不变，纯噪音。
    """
    clock = _FakeClock(step=5.0)  # 每拍约 15s 假时间
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(pt, "time", clock)
    serve = _FakeProc(poll_seq=[None, None, None, None, None, 0])  # 5 拍守候后退出 ⇒ 约 75s

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        if "--logfile" in cmd:
            Path(cmd[cmd.index("--logfile") + 1]).write_text(
                "x https://abc-def.trycloudflare.com", encoding="utf-8"
            )
        return serve if "--port" in cmd else _FakeProc(poll_seq=[None])

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    # 空等：busy=false，且 serve.log 不再增长（没有过程日志可转）
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    logs: list[str] = []
    rc = nbr.run_push_worker(
        _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared"), logs.append
    )
    assert rc == 0
    beats = [m for m in logs if m.startswith("守候中")]
    assert len(beats) == 1, f"75s 空等只该有一条心跳（限流 {nbr._HEARTBEAT_SEC:g}s），实际 {beats}"



def test_push_already_serving_skips_reinstall_and_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """push-first 升级后（already_serving）：不重复起 cloudflared/worker_server，
    只守候 + 转发 serve.log + /ping 心跳。"""
    clock = _FakeClock(step=20.0)
    monkeypatch.setattr(nbr, "time", clock)
    pops: list[list[str]] = []

    def _fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        pops.append(list(cmd))
        return _FakeProc(poll_seq=[None])

    monkeypatch.setattr(nbr.subprocess, "Popen", _fake_popen)
    kills: list[int] = []

    def fake_kill(pid: int, sig: int) -> None:
        kills.append(int(pid))
        if len(kills) >= 2:
            raise OSError("dead")  # 第二次探测判死 → 干净退出

    monkeypatch.setattr(nbr.os, "kill", fake_kill)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "serve.log").write_text("[00:00:00] [worker-serve] boot done\n", encoding="utf-8")
    cfg = _base_cfg(tmp_path)
    cfg["already_serving"] = {
        "serve_pid": 424242,
        "cf_pid": None,
        "cf_url": "https://abc-def.trycloudflare.com",
    }
    logs: list[str] = []
    rc = nbr.run_push_worker(cfg, logs.append)
    assert rc == 0
    assert pops == []  # 绝不重复 spawn cloudflared / worker_server
    assert any("接管已就绪 push 服务" in m for m in logs)
    assert any("[serve]" in m and "boot done" in m for m in logs)


# ------------------------------------------------------------------ run_notebook


def test_notebook_rc_passthrough_and_keepalive_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = threading.Event()
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")
    monkeypatch.setattr(nbr, "run_pull_worker", lambda cfg, log: 7)
    # max_worker_restarts=0：单次失败即停（不真睡退避）
    rc = nbr.run_notebook(_base_cfg(tmp_path, keepalive_stop=stop, max_worker_restarts=0))
    assert rc == 7
    assert stop.is_set()  # finally 语义：无论 rc 如何保活线程都要停


def test_notebook_crash_backoff_retries_to_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock(step=1.0)
    monkeypatch.setattr(nbr, "time", clock)
    stop = threading.Event()
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")
    calls: list[int] = []

    def fake_runner(cfg: dict[str, Any], log: Any) -> int:
        calls.append(1)
        return 5

    monkeypatch.setattr(nbr, "run_pull_worker", fake_runner)
    rc = nbr.run_notebook(_base_cfg(tmp_path, keepalive_stop=stop, max_worker_restarts=3))
    assert rc == 5
    assert len(calls) == 4  # 首发 + 3 次重启
    assert clock.sleeps == [30, 60, 90]  # 退避 30s×attempt（封顶 300 不触达）


def test_notebook_minus2_stops_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _FakeClock(step=1.0)
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")
    calls: list[int] = []
    def fake_runner(cfg: dict[str, Any], log: Any) -> int:
        calls.append(1)
        return -2

    monkeypatch.setattr(nbr, "run_pull_worker", fake_runner)
    rc = nbr.run_notebook(_base_cfg(tmp_path))
    assert rc == -2
    assert len(calls) == 1  # 配置致命：单次即停
    assert clock.sleeps == []  # 不退避


def test_notebook_unknown_mode_single_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")
    assert nbr.run_notebook(_base_cfg(tmp_path, mode="sideways")) == -2


def test_notebook_keyboard_interrupt_still_stops_keepalive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = threading.Event()
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")

    def fake_runner(cfg: dict[str, Any], log: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(nbr, "run_pull_worker", fake_runner)
    with pytest.raises(KeyboardInterrupt):
        nbr.run_notebook(_base_cfg(tmp_path, keepalive_stop=stop))
    assert stop.is_set()  # 中断穿透，但 finally 清理保活


def test_notebook_zero_rc_no_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cpu")
    calls: list[int] = []
    def fake_runner(cfg: dict[str, Any], log: Any) -> int:
        calls.append(1)
        return 0

    monkeypatch.setattr(nbr, "run_pull_worker", fake_runner)
    assert nbr.run_notebook(_base_cfg(tmp_path)) == 0
    assert len(calls) == 1  # 干净退出不重启


def test_notebook_reuses_preset_device_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已预置的 device_resolved 不得被重解析（2026-09-15：cell 要先解析再 spawn）。

    push-first 的 spawn 走 `--device`，cell 必须在 spawn **之前**解析好设备 ——
    否则字面量 "auto" 会被传给 worker，`torch.device("auto")` 当场炸掉整轮 job。
    cell 解析后写进 CFG["device_resolved"]，run_notebook 必须**复用**：重复探测既慢
    又刷日志，而且 TPU 分支的 PJRT_DEVICE 环境位会晚于 spawn 才打上（子进程继承不到）。
    """
    calls: list[int] = []

    def spy(cfg: dict[str, Any], log: Any) -> str:
        calls.append(1)
        return "cuda"

    monkeypatch.setattr(nbr, "resolve_device", spy)
    monkeypatch.setattr(nbr, "run_pull_worker", lambda cfg, log: 0)
    cfg = _base_cfg(tmp_path)  # 已含 device_resolved="cpu"
    assert nbr.run_notebook(cfg) == 0
    assert calls == []  # 预置 ⇒ 完全不重解析
    assert cfg["device_resolved"] == "cpu"  # 原值保留，不被覆盖


def test_notebook_resolves_when_device_resolved_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未预置时仍要解析（pull 路径 / 老 cell 不能因此失去设备探测）。"""
    monkeypatch.setattr(nbr, "resolve_device", lambda cfg, log: "cuda")
    monkeypatch.setattr(nbr, "run_pull_worker", lambda cfg, log: 0)
    cfg = _base_cfg(tmp_path)
    cfg.pop("device_resolved")
    assert nbr.run_notebook(cfg) == 0
    assert cfg["device_resolved"] == "cuda"


# ------------------------------------------------------------------ sha12


def test_sha12_matches_sha256_prefix() -> None:
    raw = b"battle-code-zip-bytes"
    assert nbr.sha12(raw) == hashlib.sha256(raw).hexdigest()[:12]
    assert len(nbr.sha12(raw)) == 12


# ------------------------------------------------------------------ 结果就绪通知（2026-10-26）


def test_hub_base_prefers_registry_url() -> None:
    """通知与注册**同一个**地址：`registry_url` > `hub_url`，去尾斜杠。"""
    assert nbr._hub_base_of({"hub_url": "http://a/", "registry_url": "https://b/"}) == "https://b"
    assert nbr._hub_base_of({"hub_url": "http://a/"}) == "http://a"
    assert nbr._hub_base_of({}) == ""


def test_push_worker_passes_result_ready_hub_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """完整 worker_server 拿到 `--hub-url`（与注册同址）⇒ 出结果后能敲 hub。

    缺这条接线时语义变成「纯轮询」——不报错、只是每轮白等 `PUSH_POLL_SEC`，
    正是 2026-10-26 用户说的那段落灰时间。
    """
    clock = _FakeClock(step=5.0)  # 步长必须 < 隧道 60s 窗口（同既有用例）
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(pt, "time", clock)
    seen: list[list[str]] = []

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        seen.append(list(cmd))
        if "--logfile" in cmd:
            Path(cmd[cmd.index("--logfile") + 1]).write_text(
                "2026-09-13 INF | https://abc-def.trycloudflare.com", encoding="utf-8"
            )
            return _FakeProc(poll_seq=[None])
        return _FakeProc(poll_seq=[0])

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    cfg = _base_cfg(
        tmp_path,
        cloudflared_path="/usr/bin/cloudflared",
        registry_url="https://hub.example/",
        hub_url="http://127.0.0.1:1",
    )
    assert nbr.run_push_worker(cfg, lambda m: None) == 0
    serve = next(c for c in seen if "--port" in c)
    assert serve[serve.index("--hub-url") + 1] == "https://hub.example"


def test_push_worker_without_hub_address_keeps_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有 hub 地址（手动起服务/旧部署）⇒ 不传 `--hub-url`，行为与旧版逐字节一致。"""
    clock = _FakeClock(step=5.0)
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(pt, "time", clock)
    seen: list[list[str]] = []

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        seen.append(list(cmd))
        if "--logfile" in cmd:
            Path(cmd[cmd.index("--logfile") + 1]).write_text(
                "2026-09-13 INF | https://abc-def.trycloudflare.com", encoding="utf-8"
            )
            return _FakeProc(poll_seq=[None])
        return _FakeProc(poll_seq=[0])

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    cfg = _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared")
    cfg.pop("hub_url")
    assert nbr.run_push_worker(cfg, lambda m: None) == 0
    serve = next(c for c in seen if "--port" in c)
    assert serve.count("--hub-url") == 0


# ------------------------------------------------------------------ 训练侧放大（2026-10-26）


@pytest.mark.parametrize(("mult", "flag"), [(2.0, "2"), (1.5, "1.5"), (1.0, None)])
def test_push_worker_passes_epoch_mult(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mult: float, flag: str | None
) -> None:
    """push 主路径（code 已到位 ⇒ 完整服务）：`CFG.epoch_mult` 变成 `--epoch-mult N`。

    缺省 1.0 时 argv 里不出现——旧 cfg/旧部署逐字不变（非目标守卫）。
    """
    clock = _FakeClock(step=5.0)
    monkeypatch.setattr(nbr, "time", clock)
    monkeypatch.setattr(pt, "time", clock)
    seen: list[list[str]] = []

    def fake_popen(cmd: list[str], **kw: Any) -> _FakeProc:
        seen.append(list(cmd))
        if "--logfile" in cmd:
            Path(cmd[cmd.index("--logfile") + 1]).write_text(
                "2026-09-13 INF | https://abc-def.trycloudflare.com", encoding="utf-8"
            )
            return _FakeProc(poll_seq=[None])
        return _FakeProc(poll_seq=[0])

    monkeypatch.setattr(nbr.subprocess, "Popen", fake_popen)
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=0: _FakeResp(200))
    cfg = _base_cfg(tmp_path, cloudflared_path="/usr/bin/cloudflared", epoch_mult=mult)
    assert nbr.run_push_worker(cfg, lambda m: None) == 0
    serve = next(c for c in seen if "--port" in c)
    if flag is None:
        assert serve.count("--epoch-mult") == 0
    else:
        assert serve[serve.index("--epoch-mult") + 1] == flag
