"""remote/deliver_proc.py —— 补传腿的**进程模式**：`DelivererProcess` + 工厂 + 共同接口。

> 设计：`plan/offline-deliver-isolation.plan.md`（P1）。子进程入口 = `remote/deliver_worker.py`。

为什么另立一个模块（而不是塞在 `remote/offline_deliver.py` 里）：2026-10-07 实测
`tests/test_python_loc_budget.py`（单文件**代码行 < 1000**）在把那两个类放进去之后当场变红 ——
补传腿本来就长，进程那一面不该再往同一个文件里长。于是：

  * `remote/offline_deliver.py`（L1）= 补传本体（`OfflineDeliverer`：记账 / 探活 / 投递 / 线程）；
  * 本模块（L2）= **怎么跑这条腿**：独立子进程（缺省）/ sticky 降级线程 / 同步（老行为）。

依赖方向是单向的：本模块向下依赖 `offline_deliver`（L1）与 `common.protocol`（L0）——
`plan_handoff`(L5) 与 `run_loop`(L7) 站在本模块上面拿 `make_deliverer`。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO, Protocol

from common.protocol import sanitize_run_id
from remote.offline_deliver import (
    DRAIN_FLUSH_SEC,
    DRAIN_TICK_SEC,
    HUB_LEASE_ENV,
    OfflineDeliverer,
    _log_default,
)

#: 补传腿的**实现选择**（`make_deliverer(mode=…)` / 环境变量 `NN_DELIVER_MODE`）：
#: `process` = 独立子进程（缺省；2026-10-06 事故的隔离面）、`thread` = 进程内后台线程（降级面）、
#: `sync` = 老同步行为（单步调试/测试）。
DELIVER_MODE_ENV = "NN_DELIVER_MODE"
#: 子进程入口模块名（测试注入一个不存在的名字即可模拟「在飞的包里没有它」）。
DELIVER_WORKER_MODULE = "remote.deliver_worker"
#: `Popen` 之后等 `boot ok` 的上限（秒）：超时/非零退出 ⇒ **降级**（见 `DelivererProcess`）。
PROC_BOOT_TIMEOUT_SEC = 5.0
#: 收线时给子进程的 flush 预算上限（秒）：父侧 `close(timeout)` 只是输入，不会超过它。
PROC_STOP_BUDGET_MAX = DRAIN_FLUSH_SEC
#: `close()` 等子进程退出的额外余量（秒）：它在自己那一侧按 `stop.budget` 判 deadline。
PROC_CLOSE_GRACE_SEC = 10.0
#: 子进程 idle-timeout 的缺省（秒）：纯兜底（同 `remote.deliver_worker.IDLE_TIMEOUT_SEC`）。
PROC_IDLE_TIMEOUT_SEC = 1800.0


class DelivererLike(Protocol):
    """补传腿的**共同接口**（`plan_handoff` 的类型面，2026-10-07 C7）。

    两个实现：`OfflineDeliverer`（进程内：线程 / 同步）与 `DelivererProcess`（独立子进程 +
    sticky 降级）。`RunContext.deliverer` 只认这个协议 —— 换实现不该动任何调用方
    （`plan_handoff.py` 里原先写死 `OfflineDeliverer | None`，换进程后 mypy 必红）。
    """

    #: 本份产物在 hub 里的归位键（多课程 hub 必需；两个实现都有，调用方直接读）。
    @property
    def course(self) -> str: ...

    def start(self) -> None: ...

    def submit_round(self, it: int) -> None: ...

    def submit_eval_round(self, it: int) -> None: ...

    def submit_final(
        self,
        *,
        it_end: int,
        state: str,
        summary: dict | None = None,
        end_it_reached: bool = False,
    ) -> None: ...

    def close(self, timeout: float = DRAIN_FLUSH_SEC) -> None: ...

    def status(self) -> dict: ...

    def pending(self) -> list[int]: ...


class DelivererProcess:
    """补传腿的**独立进程**代理（父侧；与 `OfflineDeliverer` 同接口，见 `DelivererLike`）。

    为什么要有它（2026-10-06 事故 / 来源 plan §10）：**同进程**这条通道本身就是风险面——
    `_eval_rows_for()` 每投一轮要全量解析 `eval_log.jsonl`（云上实测 33MB）、
    `encode_weights_json` / `encode_opt_tar` 要 gzip+base64 ~1.2MB、`json.dumps` 出一个 ~2MB 的体，
    每一步都压在训练进程的 GIL 上。搬到独立进程 = 这条通道**永久关闭**。

    父侧只做三件事（零共享内存）：**写控制通道**（append-only JSONL；非阻塞、永不抛）、
    **读状态面**（子进程的状态文件 + 磁盘账本）、**有界收线**（`stop`+预算 → terminate → kill）。

    ★降级（C8/C9，本功能在真机上的**主路径**）：`Popen` 成功 ≠ 子进程活着 ≠ 能 import ——
    在飞的 `code.zip` 是导出那一刻的快照，里面**没有** `remote/deliver_worker.py`，所以
    「起不来」不是异常而是预期形态 ⇒ 等不到 `boot ok` 就 **sticky 降级**到进程内线程模式 +
    响亮一行，且**不再重试** `Popen`。训练绝不因此停。
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        run_id: str,
        artifacts_dir: str | Path,
        course: str = "",
        #: 本课租约 token（★M1b/P1-1：随补传体上报，推进 hub 侧的活动起点）。走 env
        #: 传给子进程（`HUB_LEASE_ENV`），**不进 argv**。空 = 不带。
        hub_lease: str = "",
        #: 控制/状态文件的落点（缺省 = 产物目录下的 `work/`，与 `run_loop` 的老缺省一致）。
        work_dir: str | Path | None = None,
        ctl_path: str | Path | None = None,
        status_path: str | Path | None = None,
        log: Callable[[str], None] = _log_default,
        python: str | None = None,
        worker_module: str = DELIVER_WORKER_MODULE,
        idle_timeout: float = PROC_IDLE_TIMEOUT_SEC,
        tick_sec: float = DRAIN_TICK_SEC,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "")
        self._run_id_raw = str(run_id or "")
        self.root = Path(artifacts_dir)
        self.course = str(course or "").strip()
        self.hub_lease = str(hub_lease or "").strip()
        self.work_dir = (
            Path(work_dir)
            if work_dir is not None
            else (self.root / "work")
        )
        self._ctl_path_arg = Path(ctl_path) if ctl_path is not None else None
        self._status_path_arg = Path(status_path) if status_path is not None else None
        self.log = log
        self._python = str(python or sys.executable)
        self._worker_module = str(worker_module)
        self._idle_timeout = float(idle_timeout)
        self._tick_sec = float(tick_sec)
        self._cwd = Path(__file__).resolve().parents[1]  # nn-training 根
        # ⚠ 必须**可重入**：`_append` 持锁调 `_ensure_alive()`，而按需重启那条路（子进程死了、
        # 而 `result_done` 还没落盘）会在锁内**再调一次** `_append` 重述段末摘要（★C1/C15）——
        # `threading.Lock()` 在那里就是同一线程自锁死 ⇒ `submit_round()` 永不返回（训练线程永挂，
        # 而它正是本类要救的「腿半死」形态）。2026-10-09 门禁满载时真踩到（慢层
        # `test_restart_does_not_replay_consumed_commands` 60s 超时，转储栈停在 `_append` 的
        # `with self._lock:`；回归用例 `test_restart_restating_the_final_does_not_self_lock`）。
        # 跨线程互斥语义不变（只有 `_append` 用这把锁）。
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._forwarders: list[threading.Thread] = []
        self._boot = threading.Event()
        self._ctl: Path | None = None
        self._status_path: Path | None = None
        #: 本父进程已写进控制文件的字节数（重启子进程时传下去，**不重放**已消费的请求）。
        self._written = 0
        #: 重启次数（死一次记一次；进 `status()`）。
        self._restarts = 0
        self._final_submitted = False
        self._final_obj: dict | None = None
        self._mode = "process"
        self._degrade_reason = ""
        self._fallback: OfflineDeliverer | None = None
        self._mirror_obj: OfflineDeliverer | None = None

    # ------------------------------------------------------------ 生命周期

    def _enabled(self) -> bool:
        return bool(self.base_url and self.token and self._run_id_raw)

    def _new_state_path(self, prefix: str, suffix: str) -> Path:
        """会话唯一的控制/状态文件名（★C3：`work_dir` **跨会话复用**）。

        为什么必须带会话身份：Colab 上同一门课反复续跑就是**同一个目录** —— 固定名字会让新会话
        一启动就吃到**上一段末尾那条 `{"stop": true}`**、立刻退出 0（整段不投递，且比「子进程死了」
        更难查）。**不用「启动时 truncate」**：可能与还活着的上一个子进程抢读。
        """
        try:
            rid = sanitize_run_id(self._run_id_raw)
        except Exception:
            rid = "run"
        stamp = time.strftime("%Y%m%dT%H%M%S")
        return Path(self.work_dir) / f"{prefix}-{rid}-{stamp}-{os.getpid()}{suffix}"

    def start(self) -> None:
        """建控制文件 + 拉起子进程；**起不来是常态**（见类 docstring）⇒ sticky 降级。"""
        if self._proc is not None or self._fallback is not None:
            return
        ctl = self._ctl_path_arg or self._new_state_path("deliver-ctl", ".jsonl")
        try:
            ctl.parent.mkdir(parents=True, exist_ok=True)
            ctl.write_bytes(b"")
        except OSError as e:
            return self._degrade(f"控制文件建不出来（{type(e).__name__}: {e}）")
        self._ctl = ctl
        self._status_path = self._status_path_arg or self._new_state_path("deliver-status", ".json")
        if not self._enabled():
            self.log("补传未启用（缺 hub_url / token / run_id）——产物只落本地产物目录")
            return
        why = self._spawn(ctl, offset=0)
        if why:
            self._degrade(why)

    def _spawn(self, ctl: Path, *, offset: int) -> str:
        """拉起子进程。返回 `""` = 成功（等到 `boot ok`），否则是**降级理由**。"""
        assert self._status_path is not None
        argv = [
            self._python,
            "-m",
            self._worker_module,
            "--ctl",
            str(ctl),
            "--ctl-offset",
            str(int(offset)),
            "--status",
            str(self._status_path),
            "--artifacts",
            str(self.root),
            "--hub-url",
            self.base_url,
            "--run-id",
            self._run_id_raw,
            "--idle-timeout",
            f"{self._idle_timeout:g}",
            "--tick-sec",
            f"{self._tick_sec:g}",
        ]
        if self.course:
            argv += ["--course", self.course]
        env = os.environ.copy()
        # `sys.path` 可能被离线包解压 / `notebook_runtime` 改过 ⇒ 不继承就会 `No module named`。
        env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
        # token **只走 env**（argv 会在 `/proc/<pid>/cmdline` 上全机可读）；而父侧手里的 token
        # 可能是 inline/文件来的（`run_loop` 的取值顺序 inline > token_file > env）⇒ 必须显式塞。
        env["BATTLE_HUB_TOKEN"] = self.token
        # 租约 token 同理只走 env（★M1b/P1-1）：空值不塞，免得子进程拿到一个空串当 token。
        if self.hub_lease:
            env[HUB_LEASE_ENV] = self.hub_lease
        self._boot = threading.Event()
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(self._cwd),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as e:
            return f"Popen 失败（{type(e).__name__}: {e}）"
        self._proc = proc
        self._forwarders = [
            threading.Thread(
                target=self._forward, args=(proc.stdout,), name="deliver-out", daemon=True
            ),
            threading.Thread(
                target=self._forward, args=(proc.stderr,), name="deliver-err", daemon=True
            ),
        ]
        for t in self._forwarders:
            t.start()
        # 等 `boot ok`：**子进程一退就立刻判失败**（在飞的包里没有这个模块 ⇒ `No module named`
        # 是毫秒级的；等满 5s 只会白白拖慢降级）。
        deadline = time.monotonic() + PROC_BOOT_TIMEOUT_SEC
        while time.monotonic() < deadline:
            if self._boot.wait(0.05):
                return ""
            if proc.poll() is not None:
                break
        if self._boot.is_set():
            return ""
        code = proc.poll()
        self._stop_child(proc, budget=0.0, grace=1.0)
        if code is not None:
            return f"子进程立即退出（退出码 {code}；模块 {self._worker_module} 在不在？）"
        return f"等 `boot ok` 超时（{PROC_BOOT_TIMEOUT_SEC:g}s）"

    def _forward(self, stream: IO[str] | None) -> None:
        """子进程输出**逐行**转发（前缀/时间戳由调用方的日志约定给；同一套 `[deliver]` 行格式）。"""
        if stream is None:
            return
        try:
            for line in stream:
                msg = str(line).rstrip("\r\n")
                if not msg:
                    continue
                if "boot ok" in msg:
                    self._boot.set()
                self.log(msg)
        except Exception:
            pass

    def _stop_child(
        self, proc: subprocess.Popen | None, *, budget: float, grace: float | None = None
    ) -> None:
        """收一个子进程：先给预算 + 余量，超时才 `terminate()` → `kill()`；最后 join 转发线程。

        为什么还要余量：子进程可能**正卡在一次 HTTP 调用里**（探活/上传的 socket 超时最长 60s）
        —— 它得先从那里面出来，才能看到 deadline 已到。余量不够就升级到 `terminate()`。
        """
        if proc is None:
            return
        if grace is None:
            grace = PROC_CLOSE_GRACE_SEC
        try:
            if proc.stdin is not None:
                proc.stdin.close()  # stdin EOF = 第二条「父进程收线了」通道
        except Exception:
            pass
        try:
            proc.wait(timeout=max(0.0, float(budget)) + float(grace))
        except Exception:
            self.log("补传子进程未按预算收线——terminate()")
            try:
                proc.terminate()
                proc.wait(timeout=5.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        for t in self._forwarders:
            t.join(timeout=2.0)
        self._forwarders = []

    def _ensure_alive(self) -> None:
        """子进程**死了就按需重启**（★C1）——否则 `submit_*` 只是在往无人读的文件里写。

        idle-timeout / 子进程自身崩溃（OOM）都不是「腿坏了」的同义词：重启一次就从控制文件的
        已写字节处继续（★C15：不重放已消费的 `repost`），段末摘要则**重述**一遍（hub 侧覆盖写）。
        """
        if self._fallback is not None or self._ctl is None:
            return
        proc = self._proc
        if proc is not None and proc.poll() is None:
            return
        code = None if proc is None else proc.poll()
        self._stop_child(proc, budget=0.0, grace=0.0)
        self._restarts += 1
        self.log(
            f"补传子进程已退出（code={code}）——按需重启（第 {self._restarts} 次；"
            f"从控制文件第 {self._written} 字节继续，不重放已消费的请求）"
        )
        why = self._spawn(self._ctl, offset=self._written)
        if why:
            self._degrade(why)
            return
        if self._final_obj is not None and not self._result_done_from_disk():
            # 段末摘要**重述**一遍：重启后 offset 在那行之后，不重述就没人替它发（hub 侧覆盖写，
            # 重发安全）。已经送达过（`result_done`）就不重发——那是无谓的重复投递。
            self._append(dict(self._final_obj))

    def _degrade(self, why: str) -> None:
        """**sticky 降级**：子进程模式起不来 ⇒ 本段改用进程内线程模式（不再重试 `Popen`）。"""
        self._mode = "thread"
        self._degrade_reason = str(why)
        self.log(
            f"补传子进程不可用（{why}）——**降级为进程内线程模式**"
            "（本段不再重试；训练不受影响，产物照常落本地）"
        )
        self._fallback = OfflineDeliverer(
            base_url=self.base_url,
            token=self.token,
            run_id=self._run_id_raw,
            artifacts_dir=self.root,
            course=self.course,
            hub_lease=self.hub_lease,
            background=True,
            log=self.log,
        )
        self._fallback.start()

    # ------------------------------------------------------------ 与 OfflineDeliverer 同接口

    def submit_round(self, it: int) -> None:
        """一轮落盘后请求补传（**非阻塞**：append 一行即返回，与网络无关）。"""
        fb = self._fallback
        if fb is not None:
            fb.submit_round(it)
            return
        self._append({"round": int(it)})

    def submit_eval_round(self, it: int) -> None:
        """云端评估落账后请求**重投**这一轮（补 `eval_rows`；幂等）。"""
        fb = self._fallback
        if fb is not None:
            fb.submit_eval_round(it)
            return
        self._append({"repost": int(it)})

    def submit_final(
        self,
        *,
        it_end: int,
        state: str,
        summary: dict | None = None,
        end_it_reached: bool = False,
    ) -> None:
        """段末摘要（**必须排在 `stop` 之前**送到子进程；收线时若没到会响亮一行）。"""
        fb = self._fallback
        if fb is not None:
            fb.submit_final(
                it_end=it_end, state=state, summary=summary, end_it_reached=end_it_reached
            )
            return
        self._final_submitted = True
        self._final_obj = {
            "final": {
                "it_end": int(it_end),
                "state": str(state),
                "summary": dict(summary or {}),
                "end_it_reached": bool(end_it_reached),
            }
        }
        self._append(dict(self._final_obj))

    def _append(self, obj: dict) -> None:
        """往控制通道追加一行：**非阻塞、永不抛**（它由训练线程直接调）。"""
        with self._lock:
            self._ensure_alive()
            ctl = self._ctl
            if ctl is None or self._fallback is not None:
                return
            line = (json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
            try:
                with ctl.open("ab") as f:
                    f.write(line)
                self._written += len(line)
            except OSError as e:
                self.log(f"控制行写不进去（{type(e).__name__}: {e}）——本次补传请求丢失")

    def close(self, timeout: float = DRAIN_FLUSH_SEC) -> None:
        """有界收线：`stop` + 预算 → 等 → terminate → kill，然后**响亮核对**（不许静默丢东西）。"""
        fb = self._fallback
        if fb is not None:
            fb.close(timeout)
            return
        proc = self._proc
        if proc is None or self._ctl is None:
            return
        budget = max(0.0, min(float(timeout), PROC_STOP_BUDGET_MAX))
        self._ensure_alive()  # 死了就先拉起来——否则段末摘要与积压会静默留在没人读的文件里
        if self._fallback is None:
            self._append({"stop": True, "budget": budget})
        self._stop_child(proc, budget=budget)
        code = proc.poll()
        if code not in (0, None):
            self.log(f"补传子进程**非零退出**（code={code}）——产物照常落本地，输出已在上方逐行转发")
        left = self._owed_reposts_from_disk()
        if left:
            self.log(
                f"段末仍有 {len(left)} 轮重投未送达（it{left[0]}…it{left[-1]}）——"
                "已记进 delivered.json 的 owed_reposts，下次会话（同一产物目录）会自动补"
            )
        if self._final_submitted and not self._result_done_from_disk():
            self.log("段末摘要未送达（final 已提交但 result_done=False）——留给下次会话")
        self._proc = None

    # ------------------------------------------------------------ 只读面

    def _mirror(self) -> OfflineDeliverer:
        """只读镜像（**只调 `pending()` / `status()`**）：`pending()` = 「磁盘有 − 账本无」这条定义
        （含拒收的指纹核对）只该有一份实现。父侧**绝不**在它上面 `sync()` / `_save_ledger()`：
        `delivered.json` 在进程模式下的**单写者是子进程**（§1-4）。
        """
        if self._mirror_obj is None:
            self._mirror_obj = OfflineDeliverer(
                base_url=self.base_url,
                token=self.token,
                run_id=self._run_id_raw,
                artifacts_dir=self.root,
                course=self.course,
                background=False,
                log=lambda _m: None,
            )
        return self._mirror_obj

    def _reading(self) -> OfflineDeliverer:
        """只读镜像 + **刷新账本**（见 `OfflineDeliverer.refresh_from_ledger` 的为什么）。"""
        mirror = self._mirror()
        mirror.refresh_from_ledger()
        return mirror

    def pending(self) -> list[int]:
        """磁盘上的待投递轮次（与进程内模式逐字同义）。"""
        fb = self._fallback
        if fb is not None:
            return fb.pending()
        return self._reading().pending()

    def _child_status(self) -> dict:
        """子进程的**状态面**（plan §10.2；读不到/半写 = 空字典，不猜）。"""
        p = self._status_path
        if p is None:
            return {}
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _owed_reposts_from_disk(self) -> list[int]:
        """`delivered.json` 里的欠账（子进程写的；`close()` 的响亮核对读它）。"""
        try:
            data = json.loads(self._mirror().ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        rows = data.get("owed_reposts") if isinstance(data, dict) else None
        return [int(it) for it in (rows or []) if isinstance(it, int) and not isinstance(it, bool)]

    def _result_done_from_disk(self) -> bool:
        child = self._child_status()
        if "result_done" in child:
            return bool(child.get("result_done"))
        try:
            data = json.loads(self._mirror().ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return bool(data.get("result_done")) if isinstance(data, dict) else False

    def status(self) -> dict:
        """父侧可见的状态：账本/磁盘 + **子进程状态面** + 重启计数 + 模式。

        `drain_alive = proc.poll() is None`（**不是** `Thread.is_alive()`）；`background = True`
        与进程内后台模式同义（补传与 PPO 并行）—— `plan_handoff` 的启动日志照旧可读。
        """
        fb = self._fallback
        if fb is not None:
            st = dict(fb.status())
            st.update(
                {
                    "mode": "thread",
                    "restarts": self._restarts,
                    "degrade_reason": self._degrade_reason,
                }
            )
            return st
        st = dict(self._reading().status())
        st.update(
            {
                "mode": "process",
                "background": True,
                "restarts": self._restarts,
                "ctl": str(self._ctl or ""),
                "status_file": str(self._status_path or ""),
                "drain_alive": bool(self._proc is not None and self._proc.poll() is None),
            }
        )
        # 子进程的**状态面**整体并入（它才是那条腿的真身）：`disabled_reason`（401/403 自停）、
        # `result_done`（段末摘要到了没）、`reposts_unsent`（欠着的重投）、`ticks`/`idle_spins`
        # （埋点与不变量探针）都在里面；时间戳不入（父侧自己有自己的钟）。
        child = self._child_status()
        for key, val in child.items():
            if key in ("updated_at", "updated_mono"):
                continue
            st[key] = val
        return st


def make_deliverer(
    *,
    hub_url: str,
    hub_token: str,
    run_id: str,
    artifacts_dir: str | Path,
    course: str = "",
    #: 本课租约 token（★M1b/P1-1：可缺——缺了补传照落，只是不推进活动起点）。
    hub_lease: str = "",
    #: 控制/状态文件的落点（只有进程模式用；缺省 = 产物目录下的 `work/`）。
    work_dir: str | Path | None = None,
    #: `""` = 自动（`$NN_DELIVER_MODE` > `"process"`，见模块常量）；
    #: 显式值 `"process" | "thread" | "sync"` 优先（测试/单步调试给后两个）。
    mode: str = "",
    #: 后台并行（只有 `thread`/`sync` 模式用；`process` 时代理由 `DelivererProcess.status()` 报）。
    background: bool = True,
    log: Callable[[str], None] = _log_default,
) -> DelivererLike | None:
    """构造补传器：**缺 hub_url 或 token 就返回 None**（= 这条腿没有补传，不是错误）。

    调用方（`run_loop`）因此只需 `if d is not None`，不必自己判断「参数齐不齐」。
    `course` = 本份产物在 hub 里的归位键（多课程 hub 必需；见 `OfflineDeliverer.__init__`）。
    需要后台并行时调用方还得调一次 `start()`（构造与起进程/线程分开，便于测试注入替身）。

    模式（2026-10-07）：缺省 `process` = 补传腿跑在**独立子进程**里（2026-10-06 事故的隔离面）；
    子进程起不来（在老包里是**常态**：`code.zip` 是导出那一刻的快照）⇒ `DelivererProcess`
    自己 **sticky 降级**到 `thread`，调用方不需要知道这件事。
    """
    if not str(hub_url or "").strip() or not str(hub_token or "").strip():
        return None
    # 解析顺序（§3.6①）：显式参数 > `$NN_DELIVER_MODE` > 缺省 `process`（离线路径的缺省）。
    kind = (
        str(mode or "").strip().lower()
        or os.environ.get(DELIVER_MODE_ENV, "").strip().lower()
        or "process"
    )
    if kind not in ("process", "thread", "sync"):
        log(f"未知的补传模式 {kind!r}（`${DELIVER_MODE_ENV}`）——按线程模式走")
        kind = "thread"
    if kind == "process":
        return DelivererProcess(
            base_url=hub_url,
            token=hub_token,
            run_id=run_id,
            artifacts_dir=artifacts_dir,
            course=course,
            hub_lease=hub_lease,
            work_dir=work_dir,
            log=log,
        )
    return OfflineDeliverer(
        base_url=hub_url,
        token=hub_token,
        run_id=run_id,
        artifacts_dir=artifacts_dir,
        course=course,
        hub_lease=hub_lease,
        background=background if kind == "thread" else False,
        log=log,
    )
