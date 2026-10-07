"""remote/offline_deliver.py —— 产物**补传**（中途能连上 hub 就自动恢复在线回传）。

用户需求（2026-09-17）后半句：任务包搬上云机后**全程自主完成训练**，产物以官方方式
（Kaggle output / Colab Drive）打包下载；「如果训练中途发现可以联通 hub，云机也能自动
恢复产物在线回传」。

**为什么是「补传」而不是「改回在线」**：全离线/半离线的契约是「产物落节点本地目录即
交付完成」（`remote/artifacts.py`）。补传是**第二份拷贝**——节点每轮落盘之后顺手问一句
「hub 在不在」，在就把这一轮推上去。三条硬性质，缺一条这个功能都会变成负担：

  1. **训练永不因网络停摆**：所有异常在模块内消化（`sync()` / `deliver_result()` 永不
     抛），连不上就静默跳过、下轮再试。没有重试预算、没有退避等待、没有阻塞调用。
  2. **重启续投**：已投递项记在产物目录的 `delivered.json`（原子写）——新会话带着同一个
     目录接上时，**积压的轮次会自动补上**（`pending()` 是「磁盘上有、记账里没有」），
     不依赖任何内存状态。
  3. **幂等且首写锁定**：hub 侧按 `(run_id, it)` 落盘，重复投递返回 duplicate；同一轮
     的权重是**不可变**的（同一 it 重算会得到不同权重——那是另一条腿，不是覆盖）。

**后台并行**（`background=True`，2026-09-22 用户指令）：补传必须与 PPO **并行**，而旧实现是
在 `_checkpoint()` 里**内联同步**跑的（`sync()` 最多 4 趟 POST、每趟 ~1.9MB/60s 超时）——
那等于每轮拿网络往返给下一轮 PPO 收税，而且这段等待与训练**没有任何数据依赖**。现在：
训练线程只 `submit_round()` **入队**（非阻塞），一个 daemon 线程独自拥有全部补传状态
（`_delivered` / `delivered.json` / `_reachable`）并慢慢推；段末 `close(timeout)` 做**有界**
flush（默认 `DRAIN_FLUSH_SEC`，超时就放手——产物已在本地，不值得为一个旧会话挂死进程）。
单写者因此是**构造性质**：除 `submit_*`/`close`（只碰 Condition 与标志位）外，所有状态
读写都发生在那个线程里。

**几个刻意的判定**（都有代价，写在这里免得后来者「顺手改回去」）：

  * *探活走带 token 的 `GET /ping`，不新增无鉴权 `/health`*：探针要回答的是「**我能不能
    用**这条链」。只证可达的探针会让「token 错」在第一次上传 ~1.9MB 体之后才暴露，还多
    一个对公网泄露「hub 在线」的端点。
  * *401/403 → **本会话停用**补传*（响亮记一行），不重试：hub 的 D9 闭锁是「同 IP 五次
    无效鉴权封 3600s」，拿错 token 每轮重试等于亲手把自己封掉；而且 token 错是配置问题，
    重试一百次也不会对。
  * *400/413 内容拒收 → **只跳过这一轮**（本会话不再重试它），其余轮次照推*：
    2026-09-22 事故——it1 的账本行被拒（旧口径「本会话停用补传」）⇒ **这一整段再没回传过
    一轮**，而真因是本模块自己取错了账本行（`metrics.jsonl` 跨会话追加，同名 it 的历史行
    被当成当前行；见 `_row_for`），本可以只丢一轮。现在：拒收→记进会话内 `_rejected`
    →下拍从下一轮继续，响亮记一行「本轮跳过，其余照推（重启会话会再试一次）」。
  * *5xx / 网络异常 → 只记日志、下轮再试*（按 key 节流，不给日志刷屏）。
  * *传输编码复用 `encode_weights_json`/`encode_opt_tar`（gzip+base64）*：比裸容器多 33%
    体量（~0.5s/轮 @3.5Mbps 实测隧道），换来的是**同一个已被两端测过的编解码器**，且
    hub 落盘路径与 result 链路逐字同形。这是 best-effort 的旁路，不值得为它再引入一种
    容器格式（备选与代价见 DECISIONS §2026-09-17-goalnn-offline-reconnect-delivery · 全文 → docs/nn/remote-transport.md §32）。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from common import net_http
from common.logutil import log_line
from common.protocol import (
    AUTH_HEADER,
    OFFLINE_ARTIFACT_BODY_MAX,
    OFFLINE_ARTIFACT_PATH,
    OFFLINE_DELIVERED_NAME,
    OFFLINE_RESULT_BODY_MAX,
    OFFLINE_RESULT_PATH,
    encode_opt_tar,
    encode_weights_json,
    sanitize_run_id,
)
from remote.artifacts import ArtifactStore, atomic_write_json, sha256_bytes

#: 探活结果的缓存秒数（训练一轮动辄几分钟，没必要每轮都探两次：`sync()` 前探一次即可，
#: 只读的 `/ping` 也便宜——但 hub 不可达时每次 `urlopen` 都要等超时，才是真成本）。
PROBE_TTL_SEC = 60.0
#: 单次 `sync()` 最多投递几轮。一个刚连上的积压段可能攒了十几轮，全推会让「落到训练循环里
#: 的那一次调用」耗时不可控（每轮 ~1.9MB + 往返）。少量多次：下轮继续补。
SYNC_CAP = 4
#: 单次 HTTP 超时（秒）。上传体 ~1.9MB，60s 对 3.5Mbps 隧道（~4.4s）极宽裕。
HTTP_TIMEOUT_SEC = 60.0
#: 同一类不可用日志的最小间隔（秒）——hub 关机时每轮一条就够了。
LOG_THROTTLE_SEC = 300.0
#: 段末有界 flush 的缺省预算（秒）：`close()` 最多为「把积压推完」等这么久。产物已在本地，
#: 超过它就走（daemon 线程不会挂住进程退出）。
DRAIN_FLUSH_SEC = 90.0
#: 后台线程的空转唤醒间隔（秒）——没有新活时它就这么久醒一次看看有没有 stop。
DRAIN_TICK_SEC = 0.5
#: 一次 drain 里 `sync()` 的最多轮数（纯防御：`pending()` 单调收敛，正常远到不了）。
DRAIN_MAX_PASSES = 256
#: 同一轮**连续**投递异常多少次之后把它当内容问题（记进 `_rejected` 并销账）。
#: 为什么需要：`_repost` 是持久集 ⇒ 一个永远投不出去的轮次（文件被删/盘抖动/解析炸）会在每次
#: 唤醒时重抛一次、并让整趟 `_push_backlog` 中止 —— 既不响亮也不收敛（2026-10-07 补，见 plan §10.6-1）。
POST_FAILURE_LIMIT = 3
def _log_default(msg: str) -> None:
    """默认日志（tag=`deliver`）——行格式见 `common.logutil`（`clock=time` 保可注入）。"""
    log_line("deliver", msg, clock=time)


def _cpu_seconds() -> float:
    """本进程累计 CPU 秒（user+sys）。

    为什么用 `os.times()` 而不是 `resource`：后者是 Unix-only，而这条腿要在 Windows 上跑测试
    （2026-10-06 事故的埋点判据：**本进程 CPU 吃满 ⇒ H6a（进程内）**；本进程正常而同机被吃满
    ⇒ H6b（`cloudflared` 等外部）**）。永不抛——埋点坏了不该影响补传。
    """
    try:
        t = os.times()
        return float(t.user) + float(t.system)
    except Exception:
        return 0.0


def _urllib_opener(url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    """默认 HTTP（POST；`data=None` 即 GET）。返回 (status, body)；**不抛** HTTPError。"""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        # 回环（本机 hub）绕开环境代理；隧道/公网 URL 保持 urllib 默认。
        with net_http.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        return int(e.code), e.read()


class OfflineDeliverer:
    """一段自主训练的产物补传器（best-effort；每个公开方法都**不会抛**）。

    只读产物目录、只写一个 `delivered.json`——它不参与训练的任何判定（不是调度器的一部分），
    所以「补传坏了」永远只意味着「产物停在节点本地」，而那是本来的交付面。
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        run_id: str,
        artifacts_dir: str | Path,
        course: str = "",
        timeout: float = HTTP_TIMEOUT_SEC,
        probe_ttl: float = PROBE_TTL_SEC,
        sync_cap: int = SYNC_CAP,
        #: 后台并行模式（见模块 docstring）：True = 训练线程只入队，另一个线程推。
        #: 缺省 False（同步老行为）——调用方明确要并行时开（`make_deliverer` 缺省已开）。
        background: bool = False,
        opener: Callable[[str, bytes, dict, float], tuple[int, bytes]] | None = None,
        now_fn: Callable[[], float] | None = None,
        log: Callable[[str], None] = _log_default,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "")
        #: run_id 同时是 hub 侧目录名：**在本端就先校验**（错在这里 = 一行明确日志，
        #: 而不是每轮一个 400）。
        try:
            self.run_id = sanitize_run_id(run_id)
        except Exception:
            self.run_id = ""
        self.root = Path(artifacts_dir)
        #: 本份产物属于 hub 的**哪门课**（多课程 hub 的归位键）。
        #:
        #: 为什么必须带上：hub 的 `/offline/artifact` 要在多门课里定位这条腿（
        #: `locate_offline_course`），而补传体里**没有**可用的课程身份——`course_name`
        #: 是课程文件的 `name` 字段（`bc-c4-v3` 的 name 是 `bc-c4-v3-distill`），与 hub 侧
        #: 的课程键（`<traj>/<课>/` 目录名）不是一回事。值来源两处：领来的 job（hub 在
        #: 轮询面里下发）或 `--hub-course`（全离线包那条腿，由 notebook 的 CFG 给）。
        #: 空 = 单课程 hub（那门课的键就是空串）：此时**不带这个键**（带空串与不带等价，
        #: 但不带更贴近旧字节行为）。
        self.course = str(course or "").strip()
        self.timeout = float(timeout)
        self.probe_ttl = float(probe_ttl)
        self.sync_cap = int(sync_cap)
        self._opener = opener or _urllib_opener
        self._now = now_fn or time.time
        self.log = log
        self.enabled = bool(self.base_url and self.token and self.run_id)
        #: 本会话停用（bad token / 体被拒）——一次性配置问题，重试无意义（见模块 docstring）。
        self.disabled_reason = ""
        self._reachable: bool | None = None
        self._probed_at = 0.0
        self._last_log: dict[str, float] = {}
        self._delivered: set[int] = set()
        #: 本会话被 hub **内容拒收**（400/413/422）的轮次 → 产物仍在本地产物目录里，但本会话
        #: 不再重试它们（重试同一个体不会变对），**其余轮次照推**。刻意只住内存：新会话
        #: 再试一次（那天可能已经换了代码/修好了行），而持久化就等于「一轮被拒＝永久不回传」。
        self._rejected: dict[int, str] = {}
        self._result_done = False
        self._manifest_cache: dict | None = None
        # ---- 后台并行（单写者线程；除 submit_*/close 外所有状态读写都在它里面）----
        self.background = bool(background)
        self._cv = threading.Condition()
        self._want_sync = False
        #: 要**重投**的已投递轮次（云机评估落账后补读数；见 `submit_eval_round`）。
        self._repost: set[int] = set()
        #: 段末三元组 + `end_it_reached`（T6：本段是否跑到计划终点；控制台据此转交
        #: `run_complete`——hub 只转交不判决，判决在云机这一侧）。
        self._final: tuple[int, str, dict, bool] | None = None
        self._stopping = False
        self._drain_until = 0.0
        self._thread: threading.Thread | None = None
        #: `submit_final` 来过没有（段末核对的输入之一：final 提交了而 `result_done` 仍假 = 摘要丢了）。
        self._final_submitted = False
        # ---- 埋点与不变量探针（§2.4 / §2.3；零成本：两个 int + 一次 `os.times`）----
        #: 外层循环**真正走到循环体**的圈数（内层 `wait` 超时不算）——忙等时每秒涨几万次。
        #: 云上它是 H6a 的读数；本地它只是埋点（**判据**是下面的 `_idle_spins`）。
        self._drain_ticks = 0
        #: 不变量探针：**只在「这一圈既没干活、也没 wait、也没在收线」时 +1**。
        #: 2026-10-06 事故（530 + 待重投 ⇒ 纯转圈烧一个核）就是这条不变量被破的形态；
        #: 修好后该分支不可达 ⇒ 断言 `== 0` 是**确定性**的（无窗口、无容差、无 sleep）。
        self._idle_spins = 0
        self._cpu_prev = _cpu_seconds()
        #: 已被 hub **内容拒收**的轮次的权重指纹（`it → sha256`）：只为把拒收跨会话带过去时
        #: 能对账「这一轮还是不是那一轮」（见 `_load_ledger` / `_save_ledger`）。
        self._rejected_fp: dict[int, str] = {}
        #: 同一 `it` 连续投递异常的次数（毒丸项识别；见 `_post_artifact` 的调用点）。
        self._post_failures: dict[int, int] = {}
        self._load_ledger()
        if not self.enabled and not self.disabled_reason:
            missing = []
            if not self.base_url:
                missing.append("hub_url")
            if not self.token:
                missing.append("token")
            if not self.run_id:
                missing.append(f"合法 run_id（收到 {str(run_id)[:40]!r}）")
            self.log(f"补传未启用（缺 {' / '.join(missing)}）——产物只落本地产物目录")

    # ------------------------------------------------------------ 后台并行（submit / close）

    def start(self) -> None:
        """起后台补传线程（幂等；`background=False` 或未启用时什么也不做）。

        未启用（没有 hub/token/run_id）时**不起线程**：那是「这条腿没有补传」，不是「失败了」。
        """
        if not self.background or not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._drain_loop, name="offline-deliver", daemon=True
        )
        self._thread.start()

    def submit_round(self, it: int) -> None:
        """一轮落盘后请求补传（**非阻塞**）：后台模式下只置位，立刻返回给训练循环。"""
        if not self.background:
            self.sync()
            return
        if self._thread is None:
            return
        with self._cv:
            self._want_sync = True
            self._cv.notify_all()

    def submit_final(
        self,
        *,
        it_end: int,
        state: str,
        summary: dict | None = None,
        end_it_reached: bool = False,
    ) -> None:
        """段末：先推积压，再推段末摘要（非阻塞；段末摘要不可变不了，只能最新有效）。"""
        self._final_submitted = True
        if not self.background:
            self.sync()
            self.deliver_result(
                it_end=int(it_end),
                state=str(state),
                summary=summary,
                end_it_reached=end_it_reached,
            )
            return
        if self._thread is None:
            return
        with self._cv:
            self._final = (int(it_end), str(state), dict(summary or {}), bool(end_it_reached))
            self._cv.notify_all()

    def close(self, timeout: float = DRAIN_FLUSH_SEC) -> None:
        """段末**有界** flush：给后台线程最多 `timeout` 秒把积压推完，然后收线。

        为什么是有界：产物已经在本地目录里（那才是交付面），补传只是第二份拷贝——
        为一个慢隧道挂住整个会话不划算。线程是 daemon，超时也不会阻塞进程退出。
        """
        if self._thread is None:
            return
        budget = max(0.0, float(timeout))
        if self._thread is None:
            # 同步/子进程模式没有后台线程可等：段末核对照样要做（响亮，不静默）。
            self._report_close()
            return
        with self._cv:
            self._stopping = True
            self._drain_until = self._now() + budget
            # 唤醒条件必须与 `_drain_loop` 的一致 —— 漏了 `_repost` 就会把「只欠重投」的段末丢掉
            # （`pending()` 跳过已投递项，而重投的对象**正是**已投递的那一轮）。
            self._want_sync = self._want_sync or bool(self.pending()) or bool(self._repost)
            self._cv.notify_all()
        # join 给内部预算之外的一点余量（线程要在自己那一侧判 deadline 并收尾）
        self._thread.join(timeout=budget + 5.0)
        self._report_close()

    def _report_close(self) -> None:
        """段末收线后的**响亮核对**（不许静默丢东西）。

        两种要响的情况：

          * 还有没送达的重投 —— 那一轮**已在账本里**，`pending()` 永远不再含它，欠账只能靠
            `delivered.json` 的 `owed_reposts` 在**下一次会话**续投（丢一次 = hub 侧该轮
            `eval_rows` 永久缺失：这正是 `submit_eval_round` 存在的唯一理由）；
          * `final` 提交过而 `result_done` 仍为假 = 段末摘要没送出去。
        """
        left = self.unsent_reposts()
        if left:
            self.log(
                f"段末仍有 {len(left)} 轮重投未送达（it{left[0]}…it{left[-1]}）——"
                "已记进 delivered.json 的 owed_reposts，下次会话（同一产物目录）会自动补"
            )
        if self._final_submitted and not self._result_done:
            self.log("段末摘要未送达（final 已提交但 result_done=False）——留给下次会话")

    def _drain_loop(self) -> None:
        """后台线程主体：串行消费「该推一轮了」/「段末」两种请求。**永不退出到异常**。

        每个请求各自兜异常（一个坏请求不得让整条补传腿永久哑掉）。
        """
        while True:
            self._drain_ticks += 1
            with self._cv:
                # ⚠ 等待条件**不含** `_repost`（2026-10-06 事故）：它是唯一「只唤醒、不消费」的标志，
                # 一旦把它当唤醒源，探活失败把它放回后就会变成「既不 wait 也不干活」的纯转圈
                # （2026-10-07 实测：0.6s 空转 50085 圈 = 一个核烧满）。它是**工作项**，不是唤醒源：
                # `_repost` 由同一次唤醒里的 `_push_backlog` → `_sync` 消费（`submit_eval_round`
                # 自己会置 `_want_sync`，所以登记时会当场试一次）。
                while not (
                    self._want_sync
                    or self._final is not None
                    or self._stopping
                ):
                    self._cv.wait(timeout=DRAIN_TICK_SEC)
                stopping = self._stopping
                want_sync = self._want_sync
                final = self._final
                self._want_sync = False
                self._final = None
                deadline = self._drain_until
            worked = False
            if want_sync or final is not None:
                worked = True
                # 收线时强制探一次（绕过负结果 TTL）——否则整段最后一次 flush 会被上一次失败静默吞掉
                self._guard(self._push_backlog, deadline, stopping)
                self._guard(self._log_diag, "段末" if final is not None else "轮次")
            if final is not None:
                it_end, state, summary, end_it_reached = final
                self._guard(
                    self.deliver_result,
                    it_end=it_end,
                    state=state,
                    summary=summary,
                    end_it_reached=end_it_reached,
                )
            elif stopping:
                # 收线：本次 flush 已按预算跑完（推不完也走——产物在本地目录里）。
                # 段末核对统一在 `close()` 里做（一次，不重复刷日志）。
                return
            elif not worked:
                # 不变量探针：走到这里 = 这一圈**既没干活、也没 wait、也没在收线** ⇒ 纯转圈。
                # 不变量：*「每一次唤醒，要么干活，要么 `wait`；禁止既不 wait 也不干活的回圈。」*
                self._idle_spins += 1

    def _push_backlog(self, deadline: float, force_probe: bool = False) -> int:
        """把积压尽量推完（每次 `sync()` 最多 sync_cap 轮，直到推空/推不动/超预算）。"""
        total = 0
        for _ in range(DRAIN_MAX_PASSES):
            n = self._sync(force_probe=force_probe and total == 0)
            if n <= 0:
                return total
            total += n
            if deadline and self._now() >= deadline:
                return total
            if not self.pending():
                return total
        return total

    def _log_diag(self, kind: str) -> None:
        """每轮一条埋点（§2.4 最便宜那档）：进程 CPU 秒增量 + 圈数 + 积压。

        判读（2026-10-06 事故）：**本进程 CPU 吃满 ⇒ H6a（进程内）**；**本进程正常而同机被吃满
        ⇒ H6b（`cloudflared` 等外部）**。它的第二职能是不变量探针的读数（`idle` 必须恒为 0；
        2026-10-06 那段忙等就是 `idle` 每秒涨几万的形态）。
        """
        now = _cpu_seconds()
        dt = max(0.0, now - self._cpu_prev)
        self._cpu_prev = now
        self.log(
            f"{kind}补传检查：cpu=+{dt:.2f}s ticks={self._drain_ticks} idle={self._idle_spins} "
            f"pending={len(self.pending())} repost={len(self._repost)}"
        )

    def _guard(self, fn: Callable[..., object], *a: object, **kw: object) -> None:
        """后台线程里执行一件补传工作：任何异常只记一笔（线程必须活到下次提交）。"""
        try:
            fn(*a, **kw)
        except BaseException as e:  # 后台腿不得因任何异常静默死掉（含 KeyboardInterrupt 类）
            try:
                self.log(f"补传后台异常（忽略，线程继续）：{type(e).__name__}: {e}")
            except Exception:
                pass

    # ------------------------------------------------------------ 记账（delivered.json）

    @property
    def ledger_path(self) -> Path:
        return self.root / OFFLINE_DELIVERED_NAME

    def _load_ledger(self) -> None:
        """读已投递项（缺失/损坏 → 空：那只会导致重复投递，hub 侧幂等兜住）。"""
        p = self.ledger_path
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("run_id") != self.run_id:
            return  # 别的 run 的记账（同一个目录被复用）——不认
        for it in data.get("artifacts") or []:
            if isinstance(it, int) and not isinstance(it, bool):
                self._delivered.add(it)
        self._result_done = bool(data.get("result_done"))
        # 跨会话的两笔欠账（必须落盘；§10.3）：
        #   * `owed_reposts` —— 上一段没送出去的重投，**本会话直接接手**（那些轮次早已记账，
        #     `pending()` 不会再现它们 ⇒ 不读回就是丢一次补读数）；
        #   * `rejected` —— 被 hub 内容拒收的轮次（按**权重指纹**记账）：核对得上才认，
        #     指纹不符（同一 it 换了内容）⇒ 不认，新会话照常再试一次。
        for it in data.get("owed_reposts") or []:
            if isinstance(it, int) and not isinstance(it, bool):
                self._repost.add(it)
                self._rejected.pop(it, None)
        if self._repost:
            self.log(f"上个会话欠 {len(self._repost)} 轮重投（补评估读数）——本会话继续补")
        for row in data.get("rejected") or []:
            if not isinstance(row, dict):
                continue
            it = row.get("it")
            fp = str(row.get("weights_fp", "") or "")
            if not isinstance(it, int) or isinstance(it, bool):
                continue
            if not fp or not self._weights_fp_matches(it, fp):
                continue  # 没证据 / 内容换了 ⇒ 当它没被拒过（宁多试一次，不静默丢一轮）
            self._rejected[it] = str(row.get("why", "") or "")
            self._rejected_fp[it] = fp

    def refresh_from_ledger(self) -> None:
        """重读 `delivered.json`（**只读镜像用**：进程模式下账本的写者是子进程，父侧那份缓存
        不刷新就会骗人：明明三轮回传都到了，`pending()` 还列着它们）。

        与 `_load_ledger` 的差别只有一个：先把上一份读面清掉（否则删除的记账永远删不掉）。
        """
        self._delivered.clear()
        self._rejected.clear()
        self._rejected_fp.clear()
        self._result_done = False
        self._load_ledger()

    def _weights_fp_matches(self, it: int, fp: str) -> bool:
        """这一轮的权重文件是不是**就是**上次被拒收的那份（只在加载时，对「被拒过的」那几轮算）。"""
        try:
            wj = ArtifactStore(self.root, run_id=self.run_id).weights_path(it).read_bytes()
        except OSError:
            return False
        return sha256_bytes(wj) == fp

    def _save_ledger(self) -> None:
        with self._cv:
            owed = sorted(self._repost)
        atomic_write_json(
            self.ledger_path,
            {
                "run_id": self.run_id,
                "hub_url": self.base_url,
                "artifacts": sorted(self._delivered),
                "result_done": self._result_done,
                # 欠账（§10.3）：`owed_reposts` = 还没送达的重投（送达即销账）；`rejected` = 内容
                # 拒收过的轮次 + 当时的权重指纹。两者都是**磁盘状态**：_repost 只住内存时，
                # 段末或子进程重启丢掉的那一次就永远补不回来了。
                "owed_reposts": owed,
                "rejected": [
                    {"it": it, "weights_fp": self._rejected_fp.get(it, ""), "why": why}
                    for it, why in sorted(self._rejected.items())
                ],
                "updated_at": self._now(),
            },
        )

    # ------------------------------------------------------------ 探活

    def probe(self, *, force: bool = False) -> bool:
        """hub 可达且**可用**（token 有效）？结果按 TTL 缓存。永不抛。"""
        if not self.enabled:
            return False
        now = self._now()
        if not force and self._reachable is not None and now - self._probed_at < self.probe_ttl:
            return self._reachable
        self._probed_at = now
        try:
            status, _body = self._opener(
                f"{self.base_url}/ping", b"", self._headers(), min(self.timeout, 20.0)
            )
        except Exception as e:  # 网络层任何异常（DNS/连接被拒/超时/TLS）= 不可达
            self._reachable = False
            self._throttled_log(
                "unreachable", f"hub 不可达（{type(e).__name__}: {e}）——本轮跳过补传"
            )
            return False
        if status == 200:
            self._reachable = True
            return True
        self._reachable = False
        if status in (401, 403):
            # 停用而不是重试：见模块 docstring（D9 闭锁 + 配置问题重试无意义）。
            self._disable(f"鉴权被拒（HTTP {status}）——token 不对/过期；本会话已停用补传")
        else:
            self._throttled_log("probe", f"hub 探活返回 HTTP {status}——本轮跳过补传")
        return False

    def _headers(self) -> dict[str, str]:
        return {AUTH_HEADER: f"Bearer {self.token}", "Content-Type": "application/octet-stream"}

    def _disable(self, why: str) -> None:
        if not self.disabled_reason:
            self.disabled_reason = why
            self.log(f"停用补传：{why}（产物照常落本地，训练不受影响）")

    def _throttled_log(self, key: str, msg: str) -> None:
        now = self._now()
        if now - self._last_log.get(key, 0.0) < LOG_THROTTLE_SEC:
            return
        self._last_log[key] = now
        self.log(msg)

    # ------------------------------------------------------------ 待投递集

    def pending(self) -> list[int]:
        """磁盘上有、记账里没有的轮次（升序）。**这是「重启续投」的全部实现**。"""
        out: list[int] = []
        try:
            dirs = sorted(self.root.glob(f"{ArtifactStore.IT_PREFIX}*"))
        except OSError:
            return out
        for d in dirs:
            if not d.is_dir():
                continue
            name = d.name[len(ArtifactStore.IT_PREFIX) :]
            if not name.isdigit():
                continue
            it = int(name)
            if it in self._delivered or it in self._rejected:
                continue
            if (d / "weights.json").exists():
                out.append(it)
        return out

    def _manifest(self) -> dict:
        """产物目录里的 manifest 快照（血缘字段随每次投递带上；读失败 → 空）。"""
        if self._manifest_cache is None:
            p = self.root / ArtifactStore.MANIFEST_NAME
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                self._manifest_cache = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                self._manifest_cache = {}
        return self._manifest_cache

    def _row_for(self, it: int, weights_fp: str) -> dict | None:
        """账本里**与本轮权重字节相符**的那一行（同名 it 有多行时取**最后一个**）。

        2026-09-22 事故的两个关键点（旧的「返回第一条同 it 行」两者都错）：

          * `metrics.jsonl` 是**跨会话追加**的：同一个目录续跑/重开时，同一个 it 会再落一行
            （`ArtifactStore.checkpoint` 追加，不重写）。旧实现返回**第一条**⇒ 拿上一会话的
            行去配这一会话的权重，hub 侧「账本行与权重不符」直接 400 拒收（现场：
            `行记 bff93df1… 实得 5f099e55…`）。这与控制台「同 iter 取最后一条 = 最新对账结果」
            是同一条口径（`dashboard/src/server/iters.ts`）；
          * 只有 `weights_fp` **对得上本轮字节**的行才是这一轮的行——配不上就宁可不发：
            一个指错轮次的行比没有行危险得多（读数看起来完全正常）。

        配不上时记一笔 WARN（两个前缀摆在一起，一眼看出是拿错了行还是文件被动过）。
        """
        p = self.root / ArtifactStore.METRICS_NAME
        if not p.exists():
            return None
        cands: list[dict] = []
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and int(row.get("it", -1)) == int(it):
                    cands.append(row)
        except OSError:
            return None
        for row in reversed(cands):  # 新→旧：最新的那一行最先被认
            if str(row.get("weights_fp", "") or "") == weights_fp:
                return row
        if cands:
            self.log(
                f"补传 it{it}: 账本行与权重不符（行记 {str(cands[-1].get('weights_fp', ''))[:16]}… "
                f"实得 {weights_fp[:16]}…）——本轮**不发 row**（宁少不错：指错轮次的行比缺行危险）"
            )
        return None

    # ------------------------------------------------------------ 投递

    def sync(self, *, force_probe: bool = False) -> int:
        """把待投递轮次尽量推上去，返回本轮投递成功数。**永不抛**。

        `force_probe` = 绕过探活的**负结果 TTL**：段末 / 子进程收线时用——那是最后一次机会，
        不能被上一次失败静默吞掉（否则整段最该送出去的那几轮会“什么都没做”地结束）。
        """
        try:
            return self._sync(force_probe=force_probe)
        except Exception as e:  # 兜底：补传的任何意外都不得波及训练
            self.log(f"补传异常（忽略，训练继续）：{type(e).__name__}: {e}")
            return 0

    def submit_eval_round(self, it: int) -> None:
        """云机评估落账后请求**重投**这一轮（幂等；非后台模式 = 空操作，见 `mark_repost`）。

        为什么需要重投而非常规积压：产物 POST 发生在落盘之后而评估还在飞（两者刻意并行，
        见 `remote/offline_eval.CloudEvalRunner`）⇒ 第一次投递时这一轮的 `eval_rows` 还不存在。
        hub 侧对重复投递走幂等分支（权重早已收下、不重写）但**仍然并账** eval 行
        （`_post_offline_artifact` → `merge_eval_rows`）⇒ 重投一次就把读数补齐。

        非后台（同步）模式不做：那条路上评估本来就在投递之前跑完（`_close_eval` 的时序）。
        子进程那条腿用它自己的循环调 `mark_repost`（同一个登记面，不依赖线程）。
        """
        if not self.background or self._thread is None:
            return
        self.mark_repost(it)

    def mark_repost(self, it: int) -> None:
        """登记一轮「要重投」（评估行迟到）。**与模式无关**：线程模式走 `submit_eval_round`，
        子进程（`background=False`）直接用这个。

        三件事缺一不可：① 进持久集；② **落盘**（`owed_reposts`）—— 段末/重启丢掉的那一次，
        只有磁盘知道；③ 唤醒线程（`submit_round` 之后的那次唤醒也会把它带上）。
        重试节奏：由**下一次唤醒**承担（不是自动重试；轮间隔 30–700s ⇒ 最坏等一个轮间隔），
        而跨会话由 `owed_reposts` 兜住。
        """
        it = int(it)
        with self._cv:
            self._repost.add(it)
            self._rejected.pop(it, None)  # 重投是「补读数」，与被内容拒收无关
            self._want_sync = True
            self._cv.notify_all()
        self._save_ledger()

    def unsent_reposts(self) -> list[int]:
        """还没送达的重投（升序）——段末核对与子进程状态面都读它。"""
        with self._cv:
            return sorted(self._repost)

    def _sync(self, *, force_probe: bool = False) -> int:
        """`force_probe` = 绕过探活的负结果 TTL。

        为什么段末必须绕过：`probe()` 把「不可达」缓存 `PROBE_TTL_SEC` 秒，而段末收线是
        **最后一次机会**——若最后一次探活在 60s 内失败过，不强制就会让整个段末 flush
        静默什么都不做（最该送出去的那份摘要就此丢掉）。

        排队顺序：真积压本轮的在前、重投（语评估行）的在后——重投那一轮的权重早在 hub 上，
        它只是「把新的读数补上」，不该挡在真正的待投递轮次前面（代价：backlog ≥ `sync_cap` 时
        重投会饿着，而 backlog 总会清空 ⇒ 接受的饥饿，与本次改动前同语义）。

        `_repost` 是**持久集**（2026-10-07）：不再「先取走、失败再放回」——那样会留下一个
        「只唤醒、不消费」的入口，正是 2026-10-06 忙等事故的成因。只有**真正被 `_post_artifact`
        处理过**且返回 `ok`/`skip` 的轮次才销账；被 `sync_cap` 截断在外的、返回 `stop` 的一律留着。
        """
        if not self.enabled or self.disabled_reason:
            return 0
        with self._cv:
            repost = sorted(self._repost)
        todo = self.pending()
        todo += [it for it in repost if it not in todo]
        if not todo:
            return 0
        if not self.probe(force=force_probe):
            return 0  # 还没送到：重投留在 `_repost` 里（下一拍 / 下一次会话再试）
        done = 0
        sent: list[int] = []
        changed = False
        for it in todo[: self.sync_cap]:
            outcome = self._post_artifact_guarded(it)
            if outcome == "stop":
                break  # 传输坏了/毒丸未达上限——别接着打，剩下的下轮再补
            if outcome in ("ok", "skip"):
                with self._cv:
                    if it in self._repost:
                        self._repost.discard(it)
                        changed = True
                self._post_failures.pop(it, None)
            if outcome == "ok":
                done += 1
                sent.append(it)
            # "skip" = 这一轮被内容拒收（已记进 `_rejected`）：跳过它，后面的照推
        if done or changed:
            self._save_ledger()
        if done:
            left = len(self.pending())
            self.log(
                f"补传 {done} 轮（it{sent[0]}…it{sent[-1]}）→ {self.base_url}"
                + (f"；仍积压 {left} 轮，下轮继续" if left else "")
            )
        return done

    def _post_artifact_guarded(self, it: int) -> str:
        """`_post_artifact` 的**毒丸防护**：同一轮连续 `POST_FAILURE_LIMIT` 次异常 ⇒ 当内容问题处理。

        为什么需要：`_repost` 是持久集 ⇒ 一个**永远投不出去**的轮次（权重文件被删/盘抖动/编码炸）
        会在每次唤醒时重抛一次，并让整趟 `_push_backlog` 中止 —— 既不响亮也不收敛。
        """
        try:
            return self._post_artifact(it)
        except Exception as e:
            n = self._post_failures.get(it, 0) + 1
            self._post_failures[it] = n
            why = f"{type(e).__name__}: {e}"
            if n >= POST_FAILURE_LIMIT:
                return self._reject_round(it, f"连续 {n} 次投递异常（{why}）")
            self.log(f"补传 it{it} 投递异常（第 {n} 次，{why}）——本轮放弃，下轮再试")
            return "stop"

    def _reject_round(self, it: int, reason: str, weights_fp: str = "") -> str:
        """把这一轮记进拒收集（`pending()` 从下拍起不再带它），返回 "skip"。

        与 401/403 的「停用整条腿」严格区分：内容问题（体形状/账本行）只毁一轮，而停用
        会把这一整段剩下的几十轮全部拦下（2026-09-22 事故：it1 一次 400 之后整段再没回过
        一轮）；而鉴权问题每轮重试会把本 IP 封掉（D9）——两者代价完全不同，不能共用一个反应。

        跨会话（2026-10-07）：这条记录随 `delivered.json` 带上**权重指纹**——子进程重启/idle-timeout
        会把「会话」缩到分钟级，只住内存的话同一轮会反复重投 ~1.9MB；而新会话读回时先核对指纹，
        同一 `it` 换了内容就不认（照常再试一次）。
        """
        self._rejected[int(it)] = reason
        if weights_fp:
            self._rejected_fp[int(it)] = str(weights_fp)
        self.log(
            f"补传 it{it} **本轮跳过**（{reason}）——产物照常落本地；"
            f"其余轮次照推（本会话不再重试这一轮，新会话会再试一次）"
        )
        return "skip"

    def _post_artifact(self, it: int) -> str:
        """投递一轮产物（权重 + opt + 账本行）。返回：

          * ``"ok"`` —— 这一轮可以记为已投递（含 hub 409「已有这一轮」的幂等分支）；
          * ``"skip"`` —— 这一轮被**内容**拒收（已记进 `_rejected`），继续推后面的轮次；
          * ``"stop"`` —— 链路问题（没送达 / 5xx / 鉴权），这一拍到此为止、下轮再试。
        """
        d = ArtifactStore(self.root, run_id=self.run_id)
        wp = d.weights_path(it)
        try:
            wj = wp.read_bytes()
        except OSError as e:
            # 扫描时还在、读时没了（会话被杀/人删了）＝瞬时状态：**不标记已投递**，
            # 下轮重扫时它已经不在 `pending()` 里了（`pending` 只认存在的权重文件）。
            self.log(f"补传 it{it} 本轮跳过：读不到权重 {wp}（{e}）")
            return "stop"
        if not wj:
            self.log(f"补传 it{it} 本轮跳过：权重文件是空的（{wp}）")
            return "stop"
        opt = b""
        op = d.opt_path(it)
        if op.exists():
            try:
                opt = op.read_bytes()
            except OSError:
                opt = b""  # 动量缺失的代价是「hub 侧续跑 Adam 归零」，不是投递失败
        m = self._manifest()
        wfp = sha256_bytes(wj)
        body = {
            "run_id": self.run_id,
            "it": int(it),
            "weights_fp": wfp,
            "weights_json": encode_weights_json(wj),
            "opt_fp": sha256_bytes(opt) if opt else "",
            "opt_tar_b64": encode_opt_tar(opt) if opt else "",
            # 账本行**随本轮一起走**（hub 侧要拿它画曲线/对账）：只取**与本轮字节相符**的那一
            # 行（同名 it 的历史行一律不取——见 `_row_for`）；找不到就只发权重（不含 row）。
            "row": self._row_for(it, wfp),
            # 云机 A 层评估的就地读数（`eval_on_cloud`）：与权重同一趟回去，hub 侧并进
            # 课程账本（`_HubQueue.merge_eval_rows`）——否则控制台要等整段结束才知道读
            # 数，而「一条跑偏的腿」正是这条腿要尽早看见的东西。空列表 = 本轮没评。
            "eval_rows": self._eval_rows_for(it),
            # 血缘（hub 侧据此把这条腿接回某个 run；全是 manifest 里的原值）
            "plan_sha256": str(m.get("plan_sha256", "") or ""),
            "course_fp": str(m.get("course_fp", "") or ""),
            "commit": str(m.get("commit", "") or ""),
            "source_dir": str(self.root)[-300:],
            "ts": self._now(),
        }
        if self.course:
            body["course"] = self.course
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > OFFLINE_ARTIFACT_BODY_MAX:
            return self._reject_round(
                it, f"补传体 {len(raw)}B 超上限 {OFFLINE_ARTIFACT_BODY_MAX}B", wfp
            )
        status, resp = self._post(OFFLINE_ARTIFACT_PATH, raw)
        if not status:  # 没送达（_post 已记一笔）
            return "stop"
        if status in (200, 201, 409):
            self._delivered.add(it)
            if status == 409:
                self.log(f"补传 it{it}：hub 已有这一轮（幂等，记为已投递）")
            return "ok"
        if status in (401, 403):
            self._disable(f"补传 it{it} 被拒（HTTP {status}）——本会话停用补传")
            return "stop"
        if status in (400, 413, 422):
            return self._reject_round(it, f"HTTP {status}: {_err_text(resp)}", wfp)
        self._reachable = False
        self._throttled_log(
            "post", f"补传 it{it} 失败（HTTP {status}: {_err_text(resp)}）——本轮放弃，下轮再试"
        )
        return "stop"

    #: 单轮补传携带的评估行上界（防体超限；正常一轮 A 层语料 = 关数×种子数，双轨 100）。
    EVAL_ROWS_CAP = 400

    def _eval_rows_for(self, it: int) -> list[dict]:
        """产物目录里本轮的评估读数（`eval_log.jsonl`；没评过 = 空列表，永不抛）。

        逐局行：只取 `iter == it` 且没有 `source` 的（`source` 是 B/C evalboard 行的标记，
        它们不是这条腿的读数）。上界 `EVAL_ROWS_CAP`：超过就只发前 N 条并记一笔
        （宁少不错——体超限会让整个补传被拒，连权重一起丢）。

        **summary 行也一起发**（每个 `(iter,wver)` 至多一行，不占上界）：它是控制台指标表
        eval 列 / eval 弹窗 / 开课回执 / 门判据唯一认的读数键（2026-09-23：只发逐局行
        ⇒ 云腿整段的评估在控制台上不可见），而云机自己的账本就是它唯一的产地——
        本地没有这条腿的循环，没人替它算。
        """
        p = Path(self.root) / ArtifactStore.EVAL_LOG_NAME
        rows: list[dict] = []
        summary: list[dict] = []
        try:
            if not p.exists():
                return rows
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(r, dict) or "source" in r:
                    continue
                if r.get("iter") != int(it):
                    continue
                if r.get("event") == "eval_summary":
                    summary.append(r)
                    continue
                if r.get("event") != "eval":
                    continue
                rows.append(r)
        except OSError:
            return []
        if len(rows) > self.EVAL_ROWS_CAP:
            self.log(
                f"补传 it{it}: 评估行 {len(rows)} 条 > 上界 {self.EVAL_ROWS_CAP}"
                f"——只发前 {self.EVAL_ROWS_CAP} 条（其余随 artifacts zip 回去）"
            )
            rows = rows[: self.EVAL_ROWS_CAP]
        return rows + summary

    def deliver_result(
        self,
        *,
        it_end: int,
        state: str,
        summary: dict | None = None,
        end_it_reached: bool = False,
    ) -> bool:
        """段末摘要（跑到哪、什么状态、失败原因）。best-effort；**永不抛**。

        与逐轮产物的差别：摘要是**会变的**（同一 run 续跑后 `it_end` 更大），hub 侧覆盖写
        ——它不是不可变快照，而「这条腿现在到哪了」的最新答案。

        `end_it_reached`（T6）：本段是否跑到计划终点（由调用方 `plan_run._drive` 用
        `_end_it_reached` 判定，只有 `complete`/`noop` 且 `it_end >= plan.end_it` 才是）。
        """
        try:
            if not self.enabled or self.disabled_reason:
                return False
            if not self.probe():
                return False
            m = self._manifest()
            body = {
                "run_id": self.run_id,
                "it_end": int(it_end),
                "state": str(state),
                "end_it_reached": bool(end_it_reached),
                "delivered": len(self._delivered),
                "summary": dict(summary or {}),
                "plan_sha256": str(m.get("plan_sha256", "") or ""),
                "course_fp": str(m.get("course_fp", "") or ""),
                "commit": str(m.get("commit", "") or ""),
                "source_dir": str(self.root)[-300:],
                "ts": self._now(),
            }
            if self.course:
                body["course"] = self.course
            raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(raw) > OFFLINE_RESULT_BODY_MAX:
                raw = json.dumps(
                    {**body, "summary": {"truncated": True}}, ensure_ascii=False
                ).encode("utf-8")
            status, _resp = self._post(OFFLINE_RESULT_PATH, raw)
            if not status:
                return False  # 没送达（_post 已记一笔）
            if status in (200, 201):
                if not self._result_done:
                    self._result_done = True
                    self._save_ledger()
                self.log(f"段末摘要已补传：it{it_end} {state} → {self.base_url}")
                return True
            if status in (401, 403):
                self._disable(f"段末摘要被拒（HTTP {status}）——本会话停用补传")
            else:
                self._reachable = False
                self._throttled_log("result", f"段末摘要补传失败（HTTP {status}）——留给下次会话")
            return False
        except Exception as e:
            self.log(f"段末摘要补传异常（忽略）：{type(e).__name__}: {e}")
            return False

    def _post(self, path: str, raw: bytes) -> tuple[int, bytes]:
        """POST 一趟。**永不抛**：传输层异常 → `(0, b"")`（状态 0 只在此出现 = 没送达）。

        为什么要在这儿收口而不是在 `sync()` 外层兜：走外层兜会让「本轮已投递的几轮」
        留在内存里却没落 `delivered.json`（异常把 `_save_ledger` 跳过了）——下次会话重传
        一遍。全程可重入的代价不该由记账来承担。
        """
        try:
            return self._opener(f"{self.base_url}{path}", raw, self._headers(), self.timeout)
        except Exception as e:
            self._reachable = False
            self._throttled_log(
                "transport", f"补传传输异常（{type(e).__name__}: {e}）——本轮跳过，下轮再试"
            )
            return 0, b""

    # ------------------------------------------------------------ 状态（日志/自检用）

    def status(self) -> dict:
        """一行式状态（给启动日志与 operator 看：补传在不在、投了几轮、卡在哪）。"""
        return {
            "enabled": self.enabled,
            "hub_url": self.base_url,
            "run_id": self.run_id,
            "course": self.course,
            "delivered": len(self._delivered),
            "pending": len(self.pending()),
            "rejected": dict(self._rejected),
            #: 还没送达的重投（§10.3）：段末核对与子进程状态面（`delivered.json` 的 `owed_reposts`）
            "reposts_unsent": len(self._repost),
            #: 埋点/探针读数（§2.4）：`idle_spins` **恒应为 0**（非 0 = 有人在空转）
            "ticks": self._drain_ticks,
            "idle_spins": self._idle_spins,
            "result_done": self._result_done,
            "disabled_reason": self.disabled_reason,
            "background": self.background,
            "drain_alive": bool(self._thread is not None and self._thread.is_alive()),
        }


#: 供调用方一致性检查（测试与 operator 脚本读它，避免抄第二份路径常量）。
DELIVERED_NAME = OFFLINE_DELIVERED_NAME


def _err_text(resp: bytes, max_chars: int = 300) -> str:
    """把 hub 的错误体压成一行（远端文本不可信：截断 + 去换行）。"""
    try:
        s = resp.decode("utf-8", "replace")
    except Exception:
        return ""
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and obj.get("error"):
            s = str(obj["error"])
    except ValueError:
        pass
    return " ".join(s.split())[:max_chars]


#: 本模块的出口（进程模式那一面在 `remote.deliver_proc.py`）。
__all__ = ["DELIVERED_NAME", "OfflineDeliverer"]
