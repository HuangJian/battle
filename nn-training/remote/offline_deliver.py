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

# 备份抽屉的**拷贝策略**（拷哪些件 / 何时跳 / 失败算什么）住在 `remote/backup_drawer`：
# 那里是它的所有者（不抛 · 有界 · 幂等 · 常态零写入），本模块只负责「算哪些轮次待投」
# 与「把读数记到 `status()` 看得见的地方」。
# ⚠ 抽屉目录名（`backup`）不在本模块拼 —— 它由调用方（`open_run_context`）解析后递进来，
# 免得这里再抄一份；命名硬约束见 `remote/artifacts.BACKUP_DIR_NAME`。
import remote.delta_files as delta_files
from common import net_http
from common.cli import house_parser, run_main
from common.logutil import tag_logger
from common.protocol import (
    AUTH_HEADER,
    OFFLINE_ARTIFACT_BODY_MAX,
    OFFLINE_ARTIFACT_PATH,
    OFFLINE_DELIVERED_NAME,
    OFFLINE_RESULT_PATH,
    sanitize_run_id,
)
from remote import deliver_mode, deliver_parts, deliver_wire
from remote.artifacts import ArtifactStore, atomic_write_json
from remote.backup_drawer import BackupDrawer

# `as` 别名 = 显式再导出（测试/operator 脚本一直从本模块拿这个名字；定义已搬到
# `deliver_ledger`，删掉这条会让 `from remote.offline_deliver import …` 断掉）。
from remote.deliver_ledger import REJECTED_MAX_TRIES as REJECTED_MAX_TRIES
from remote.deliver_ledger import (
    REJECTED_RESET_KEY,
    REJECTED_TTL_SEC,
    DeliverLedgerMixin,
    _num_or,
    _prune_reset_markers,
)
from remote.deliver_mode import eta_text, need_bps
from remote.deliver_parts import DeliverMetricsMixin
from remote.deliver_wire import pick_anchors, read_eval_iters

#: 探活结果的缓存秒数（训练一轮动辄几分钟，没必要每轮都探两次：`sync()` 前探一次即可，
#: 只读的 `/ping` 也便宜——但 hub 不可达时每次 `urlopen` 都要等超时，才是真成本）。
PROBE_TTL_SEC = 60.0
#: 探活**失败**时的负缓存秒数（比成功短）：抖动恢复的窗口按秒算，而 60s 的负缓存会让
#: 「隧道刚回来」这段最该补投的时间白白跳过（plan §4.2-1；评审 F2：别把 60s 的平坦重试换成年级退避）。
#: 抽屉（网络盘）的**宽限**（秒）：一轮要在盘上躺这么久还没送出去，才值得备一份。
#: 2026-10-29 用户口径「只在回传失败时才写网络盘」——而「一次探活没应答 / 隧道抖 60 秒」不算
#: 失败：旧口径下每轮都会先写 1.5MB 到网络盘、投递成功后 60 秒再删掉（纯 churn，现场
#: `art` 从 1.1s 抖到 88s 就是它）。判据用**产物年龄**（文件 mtime）而不是内存里的失败计数：
#: 父侧补传器每拍新建一个只读镜像（`deliver_proc._reading`），内存态在它眼里永远是空的。
BACKUP_GRACE_SEC = 300.0
PROBE_FAIL_TTL_SEC = 15.0
#: `_disable`（401/403）后的**冷却**（秒）：到点自动清空停用状态并重探一次。
#: 为什么不是「一停到底」（2026-10-16，评审 F1/C1）：token 轮换 / hub 换实例都会让 401 变成
#: 暂时现象，而 `_sync` 首行 return 会让本会话**零尝试**——现场「27 轮 0 送达」的高相容解释之一。
#: 冷却只影响「自己重试」的节奏，不改「401 立刻停用、当拍不重试」那条口径（D9 闭锁）。
DISABLE_COOLDOWN_SEC = 600.0
#: 单次 `sync()` 最多投递几轮。一个刚连上的积压段可能攒了十几轮，全推会让「落到训练循环里
#: 的那一次调用」耗时不可控（每轮 ~1.9MB + 往返）。少量多次：下轮继续补。
SYNC_CAP = 4
#: 单件上传的**上限**超时（秒）。⚠ 它是 `socket.settimeout` 语义 —— 约束**每一次**
#: 连接/send/recv 的阻塞时长，**不是整请求墙钟预算**（plan/offline-deliver-stall §1.1）：
#: 稳定 5KB/s 传 250KB（50s）不会因为「超过 60s」失败，失败只发生在某一次操作卡住时。
#: 实测体量：非 eval 轮 322KB / 带 400 局评估的轮 1.24MB（2026-10-21）。
HTTP_TIMEOUT_SEC = 60.0
#: 单件尝试超时的**下限**（秒）：1KB 的摘要/账本行件不该等满 60s 才认输。
#: 缩放式 `timeout = clamp(MIN, bytes / RATE_FLOOR, self.timeout)`，见 `_timeout_for`。
MIN_ATTEMPT_TIMEOUT_SEC = 20.0
#: 缩放分母（字节/秒）：按「至少 4KB/s 才值得等」折算单件超时（1KB→20s、154KB→38s、≥240KB→60s）。
ATTEMPT_RATE_FLOOR_BPS = 4000.0
#: 一次 `sync()` 最多吃几次**传输失败**（每次失败已经付掉一个超时窗口：60s 一次，
#: 两次就是这条腿这一拍的预算）。超了就把剩下的留给下一拍 —— 这是「不阻塞」的另一半：
#: 继续推后面的轮次，但**不许**把一拍拖成 4×60s（段末 90s 预算会被吃穿）。
MAX_FAILS_PER_PASS = 2
#: 失败日志的**三触发**节流（plan §4-W1-A-3）：第 1 次、每 N 次、恢复时各打一条。
#: 为什么不是 `LOG_THROTTLE_SEC`：那个 300s 的节流正是现场「6 分钟只看到 2 行」的原因
#: （每 60s 一次阻塞 ⇒ 6 次失败里 4 次被吞），观测上像「腿停了」。
FAIL_LOG_EVERY = 10
#: 同一类不可用日志的最小间隔（秒）——hub 关机时每轮一条就够了。
#: ⚠ 只给 `unreachable` / `probe` 用；`transport` 走三触发（见上）。
LOG_THROTTLE_SEC = 300.0
#: 段末有界 flush 的缺省预算（秒）：`close()` 最多为「把积压推完」等这么久。产物已在本地，
#: 超过它就走（daemon 线程不会挂住进程退出）。
DRAIN_FLUSH_SEC = 90.0
#: 后台线程的空转唤醒间隔（秒）——没有新活时它就这么久醒一次看看有没有 stop。
DRAIN_TICK_SEC = 0.5
#: 一次 drain 里 `sync()` 的最多轮数（纯防御：`pending()` 单调收敛，正常远到不了）。
DRAIN_MAX_PASSES = 256#: 备份抽屉的**开闸门槛**：只有「磁盘上有、账本里没有」的轮次才写网络盘（拍板-3）。
#: 0 轮待投 ⇒ **一个字节都不写**（拍板-1 的全部意义：常态路径上网络盘出现 0 次）。
#: 拷哪些件、何时跳过、失败算什么 → `remote/backup_drawer`（那里的 `BACKUP_ANCHOR_NAMES`）。
#: ⚠ 抽屉不得以 `it-` 开头（见 `remote/artifacts.BACKUP_DIR_NAME`）。




#: 同一轮**连续**投递异常多少次之后把它当内容问题（记进 `_rejected` 并销账）。
#: 为什么需要：`_repost` 是持久集 ⇒ 一个永远投不出去的轮次（文件被删/盘抖动/解析炸）会在每次
#: 唤醒时重抛一次、并让整趟 `_push_backlog` 中止 —— 既不响亮也不收敛（2026-10-07 补，见 plan §10.6-1）。
POST_FAILURE_LIMIT = 3
_log_default = tag_logger("deliver", lambda: time)


def _is_timeout(exc: BaseException) -> bool:
    """这个异常是不是**超时**（而不是连接被拒/DNS/TLS）。

    为什么要分开记：`elapsed` 的判读靠它（plan §1.1）——≈60s 且是超时 ⇒ 客户端 socket 超时；
    ≈100s ⇒ Cloudflare 的 100s；≈20s ⇒ 探活那档。HTTPError 走的是状态码分支，不会到这里。
    """
    if isinstance(exc, TimeoutError):
        return True
    return "timed out" in str(exc).lower() or "timeout" in str(exc).lower()


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


class OfflineDeliverer(DeliverLedgerMixin, DeliverMetricsMixin):
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
        hub_lease: str = "",
        #: **可读名**（`kaggle-o`）= 面板上的列名（plan/offline-leg-ppo-attribution）。与 `hub_lease`
        #: 不同，它**不参与任何判定**，只是「这轮是谁推的」的人读面；空 = 体里不带那个键。
        #: 名字在**构造期**由调用方算（见 `WORKER_NAME_ENV`）：本模块在 `worker_tag` 所在层下面。
        worker_name: str = "",
        #: 网络侧**备份抽屉**的落点（`<网络产物根>/backup`；空 = 不做备份 —— 纯离线腿 / 老调用方）。
        #: 拍板-3：**回传失败的那些轮**才写这里（每轮观测 + 段末 sweep，幂等、有界、绝不抛）。
        backup_root: str | Path | None = None,
        #: 抽屉宽限（秒，见 `BACKUP_GRACE_SEC`）；参数化只为让用例把窗口拨短。
        backup_grace: float = BACKUP_GRACE_SEC,
        timeout: float = HTTP_TIMEOUT_SEC,
        probe_ttl: float = PROBE_TTL_SEC,
        #: 探活**失败**的负缓存（只对**失败**生效，且被 `probe_ttl` 封顶 ⇒ 只会更短、绝不更长）。
        #: 参数化只为一件事：用例要能把这条常量扳回旧的平坦口径（60s）来反探针——否则
        #: 「抖动 60s 后恢复」这个主场景就没有任何一条用例真在量它（plan §4.2-1 / DoD-3a）。
        probe_fail_ttl: float = PROBE_FAIL_TTL_SEC,
        sync_cap: int = SYNC_CAP,
        #: ★ 2026-10-23（`plan/offline-deliver-full-and-gaps` §3.5）：课程的 `eval_every`，
        #: 只用于 `anchors` 档「没有 A 层读数时按每 N 轮兜底」。
        #: **缺省 0 = 旧行为**（退回全量）：直接构造 `OfflineDeliverer` 的历史调用点不该被
        #: 静默改语义；真机腿由 `make_deliverer`（→ `DelivererProcess`/`deliver_worker`）显式给。
        eval_every: int = 0,
        #: ★ 2026-10-23（§3.3/S5）：**统计批**是否启用（`?parts=metrics`，把漏轮的 row 单独送上去）。
        #: 缺省 False = **旧行为**（老 hub 没有这个入口，硬发只会 404 并污染探活）；
        #: 由 `make_deliverer`（真机腿的构造入口）显式开 True——那才是要发统计的那条路。
        metrics_parts: bool = False,
        #: ★ 2026-10-23（§3.0）：**档位自适应**。开 ⇒ 每一拍按「实测吞吐 × 本档每轮字节」
        #: 选档：好档 `full`（每轮整份）/ 中档 `bulk`（每轮 delta）/ 差档或断连 `anchors`
        #: （只发评估轮 + 统计批）。关 ⇒ 永远 `anchors`（= 旧行为，只发有 A 层读数的轮）。
        #: 缺省 False 的理由：直接构造 `OfflineDeliverer` 的历史调用点（测试/老腿）不该被
        #: 静默改语义；真机腿由 `make_deliverer` 显式开（那里默认 True）。
        adaptive: bool = False,
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
        #: 本课租约 token（`--hub-lease-file`）：随补传体一并上报，是「这份产物能推进活动
        #: 起点」的凭据（★P1-1）。**空值不是错误**：补传照落（镜像/归档/账本），只是 hub
        #: 不把这一轮当成「当前进度」——旧端/手动腿的旧行为因此逐字保留。
        self.hub_lease = str(hub_lease or "").strip()
        self.worker_name = str(worker_name or "").strip()
        self.backup_root = Path(backup_root) if backup_root else None
        self.backup_grace = float(backup_grace)
        #: 课程 `eval_every`（`anchors` 档锚点兜底的步长；见 `__init__` 的同一条注释）。
        self.eval_every = int(eval_every or 0)
        #: 统计批开关（见 `__init__`）；`_metrics_unsupported` = 探测到 hub 不支持后本会话关掉。
        self.metrics_parts = bool(metrics_parts)
        self._metrics_unsupported = False
        #: ★ 档位机（S0/S7）：自适应开关（状态本身在下面 `_now` 就绪后才造）。
        self.adaptive = bool(adaptive)
        #: 本会话的备份统计（只读面/日志用）：`(轮次, 字节, 失败, 账本未变跳过)`。
        self._backup_stat: tuple[int, int, int, int] = (0, 0, 0, 0)
        #: 抽屉字节读数 `(总字节, 两账本字节, 单轮均字节)`：由**写方**在 `backup_pending()` 里顺手量
        #: —— `status()` 每拍被调（`deliver_worker._write_status`），而抽屉在网络盘上，读方必须零 IO。
        self._drawer_stat: tuple[int, int, int] = (0, 0, 0)
        #: 抽屉**清理**读数 `(删了几轮, 删失败, 孤儿保留, 不删的原因)`（D3）。
        self._prune_stat: tuple[int, int, int, str] = (0, 0, 0, "")
        #: 上次清理时「确认送达」有几轮：一样就不必再扫抽屉（只读内存，零 IO）。
        self._pruned_mark: int = -1
        #: 段末强制扫一次（`close()` → `backup_pending()`）。
        self._prune_forced: bool = True
        #: 抽屉写方（惰性建；`None` = 还没建/这条腿不做备份）。它的 `_last` 就是上面那份读数。
        self._drawer: BackupDrawer | None = None
        self.timeout = float(timeout)
        self.probe_ttl = float(probe_ttl)
        self.probe_fail_ttl = float(probe_fail_ttl)
        self.sync_cap = int(sync_cap)
        self._opener = opener or _urllib_opener
        self._now = now_fn or time.time
        self.log = log
        #: 档位状态（必须晚于 `_now`/`log`：ModeTracker 拿它们当时钟与日志）。
        self._modes = deliver_mode.ModeTracker(
            adaptive=self.adaptive, now_fn=self._now, log=self.log
        )
        self.enabled = bool(self.base_url and self.token and self.run_id)
        #: 本会话停用（bad token / 体被拒）——一次性配置问题，重试无意义（见模块 docstring）。
        #: ⚠ 不是死刑：`_maybe_revive()` 在 `DISABLE_COOLDOWN_SEC` 后自动解封并重探。
        self.disabled_reason = ""
        self._disabled_at = 0.0
        #: 重试节奏的只读面（plan §4.2-1）：连续失败次数 / 最后一次失败原因 / 下一次可重试的时刻。
        #: 只给人看，**不参与判决** —— 退避与否由 hub 的租约与 `_disable` 冷决定。
        self._retry_n = 0
        self._last_error = ""
        self._next_try = 0.0
        self._reachable: bool | None = None
        self._probed_at = 0.0
        #: 探活**成功缓存作废**（plan §4-W1-A-2，评审 P1-1）：数据面失败只说明「这一件没过去」，
        #: 不该把「hub 可达」判死（那会让下一拍先付一次探活的等待，且 15s 负缓存会把恢复窗砍掉一截）。
        #: 置真 ⇒ 下一拍**立刻**重探一次（≤20s），探通了就接着推。`_reachable` 的语义一个字没动。
        self._probe_dirty = False
        #: 传输失败的观测面（W5）：累计次数 / 起始时刻 / 最后一类 / 最后一次的耗时与体量。
        #: `elapsed` 是 plan §1.1 的判读依据（60.0 / 100 / 20 三档），**不是**判决依据。
        self._fail_n = 0
        self._fail_since = 0.0
        self._last_fail_kind = ""
        self._last_elapsed = 0.0
        self._last_body_bytes = 0
        #: 本次会话里「带评估读数的大体没过去」的轮次（plan §4-W1-B′ 的降级档）：
        #: 下一拍对它们只发权重/opt/账本行，评估读数靠 `_repost`（`owed_reposts`）另投。
        #: **只住内存**：子进程重启后若该轮仍未送达，会重新带全量试一次（自愈，不会永久降级）。
        self._degraded: set[int] = set()
        #: 本次尝试是不是**降级体**（不含评估读数）。`_sync` 靠它决定要不要销「重投」欠账：
        #: 降级送达只是把权重送到岸，评估读数还欠着（销了它就真的丢了）。
        self._attempt_degraded = False
        self._last_log: dict[str, float] = {}
        self._delivered: set[int] = set()
        #: ★ 2026-10-23：统计件已送达的轮次（与 `_delivered` 分开记账，见 `deliver_ledger`）。
        self._metrics_sent: set[int] = set()
        #: 权重到了、但体里没有 row 的轮次（评审 P1-7）：统计欠账，随 `delivered.json` 跨会话。
        self._metrics_owed: set[int] = set()
        #: 统计批的读数 `(本批轮数, 本批字节)`（S7 观测用）。
        self._metrics_stat: tuple[int, int] = (0, 0)
        #: 上一趟权重体里**真的**带了账本行吗（评审 P1-7：只有带了才销 `_metrics_sent`）。
        self._last_round_had_row = False
        # ★ 2026-10-23（`plan/anchor-full-rounds` S4）：**轮形态**读数（`(形态, 累计省字节,
        #   整份参照字节)`）。累加与判据在 `deliver_wire.note_opt_wire`（L0 纯函数，单元可测）；
        #   本类是唯一持有它的人，`status()` 原样转出。
        self._opt_wire: tuple[str, int, int] = deliver_wire.OPT_WIRE_ZERO
        #: `delivered.json` 读出来了吗（存在 ∧ 解析得开 ∧ `run_id` 是本次的）。
        #: 它是**抽屉删除**的硬门槛（D3/DoD-3c）：账本不可读 ⇒ 本会话一份送达证据都没有，
        #: 而抽屉是跨会话的唯一第二副本 ⇒ 一个字节都不许删。
        self._ledger_readable = False
        #: 本会话被 hub **内容拒收**（400/413/422）的轮次 → 产物仍在本地产物目录里，但本会话
        #: 不再重试它们（重试同一个体不会变对），**其余轮次照推**。记录会随 `delivered.json`
        #: 带上权重指纹与**年龄**（`at`/`tries`）：到 `REJECTED_TTL_SEC`（或记满
        #: `REJECTED_MAX_TRIES` 次）就自动复活，免得一次误判变成「一轮永久不回传」（D6）。
        self._rejected: dict[int, str] = {}
        #: 每条拒收的年龄面：`it → (记录时刻, 已记次数)`（不落盘也没关系，落盘是为了跨会话 TTL）。
        self._rejected_at: dict[int, float] = {}
        self._rejected_tries: dict[int, int] = {}
        self._result_done = False
        self._manifest_cache: dict | None = None
        #: 账本轮次集（`_ledger_its()`）的惰性缓存：`((size, mtime_ns), its)`。
        #: 为什么需要：`pending()` 每拍被调（`_log_diag` 也调），而 `metrics.jsonl` 可达 MB 级。
        self._ledger_cache: tuple[tuple[int, int] | None, set[int]] = (None, set())
        #: 评估轮集合（`_eval_iters()`，锚点判据）的惰性缓存：`((size, mtime_ns), its)`。
        #: 另一份缓存而不是复用 `_ledger_cache`：来源文件不同（`eval_log.jsonl` vs
        #: `metrics.jsonl`），失效键也不同——共用一份会让「账本追加了」把评估集合一起打掉。
        self._eval_iters_cache: tuple[tuple[int, int] | None, set[int]] = (None, set())
        #: 上一轮实时体里被摘掉的逐局明细局数（Q2 的人读面；`status()` 读它，零额外 IO）。
        self._last_pergame_games = 0
        #: 上一轮评估行白名单投影前后的字节（`status()` 的 `eval_rows_bytes`）：
        #: 两个数一起看才是读数——只有一个数时无法区分「没评」与「瘦到 0」。
        self._eval_rows_raw_bytes = 0
        self._eval_rows_wire_bytes = 0
        # ---- 后台并行（单写者线程；除 submit_*/close 外所有状态读写都在它里面）----
        self.background = bool(background)
        self._cv = threading.Condition()
        self._want_sync = False
        #: 要**重投**的已投递轮次（云机评估落账后补读数；见 `submit_eval_round`）。
        self._repost: set[int] = set()
        #: 段末三元组 + `end_it_reached` + `seg_end_kind`（T6：本段是否跑到计划终点；
        #: 控制台据此转交 `run_complete`——hub 只转交不判决，判决在云机这一侧）。
        #: 第五格 `seg_end_kind`（J2 三态）是 v2 新增：hub 用它区分「本段完成」与「课末」。
        self._final: tuple[int, str, dict, bool, str] | None = None
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
        seg_end_kind: str = "",
    ) -> None:
        """段末：先推积压，再推段末摘要（非阻塞；段末摘要不可变不了，只能最新有效）。

        `seg_end_kind`（J2）：`""` = 非段末 / 未知（旧调用方不传也合法 —— 容忍读那条
        纪律同样适用于本参数：缺席回落到 hub 的两态旧行为）。
        """
        self._final_submitted = True
        kind = str(seg_end_kind or "")
        if not self.background:
            self.sync()
            self.deliver_result(
                it_end=int(it_end),
                state=str(state),
                summary=summary,
                end_it_reached=end_it_reached,
                seg_end_kind=kind,
            )
            return
        if self._thread is None:
            return
        with self._cv:
            self._final = (
                int(it_end),
                str(state),
                dict(summary or {}),
                bool(end_it_reached),
                kind,
            )
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
        # 段末**强制 sweep**（拍板-3 ②）：线程可能已在收线路上返回，而它是最后一次备份机会。
        #: 备份与**清理**同拍做（`backup_pending` 里）；段末强制扫一次抽屉（D3）。
        self._prune_forced = True
        self.backup_pending()
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
                it_end, state, summary, end_it_reached, seg_end_kind = final
                self._guard(
                    self.deliver_result,
                    it_end=it_end,
                    state=state,
                    summary=summary,
                    end_it_reached=end_it_reached,
                    seg_end_kind=seg_end_kind,
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
        """把积压尽量推完（每次 `sync()` 最多 sync_cap 轮，直到推空/推不动/超预算），
        然后**观测一次账本**决定要不要把没送出去的轮次备进抽屉（拍板-3 的每轮那一档）。

        `deadline`（`self._now()` 时基）在这里**逐拍折算成硬预算**传给 `_sync`：一次失败
        阻塞一整个超时窗口，不计预算就会把「段末 90s 收线」拖成 4×60s。
        """
        total = 0
        for _ in range(DRAIN_MAX_PASSES):
            budget = max(0.0, float(deadline) - self._now()) if deadline else None
            n = self._sync(
                force_probe=force_probe and total == 0, budget_sec=budget
            )
            if n <= 0:
                break
            total += n
            if deadline and self._now() >= deadline:
                break
            # 推空判据必须跟**当前档位的候选集**一致：`every` 档下非锚点轮也在候选集里，
            # 只判锚点会让积压每拍只前进 `sync_cap` 轮（评审 P1-5：D10 的补发上限就丢在这）。
            # `anchors` 档下没有锚点/重投时，下一圈 `_sync` 自己会返回 0（n <= 0 提前 break），
            # 不会多打任何 POST。
            if not self.pending() and not self._repost:
                break
        # 备份跑在自己的有界里（它不属「推积压」的预算）：推不完/推不动都要把账本看一遍
        self._guard(self.backup_pending)
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
        pending_n = len(self.pending())
        avail = self._modes.avail_bps
        self.log(
            f"{kind}补传检查：cpu=+{dt:.2f}s ticks={self._drain_ticks} idle={self._idle_spins} "
            f"pending={pending_n} anchor={len(self.pending_anchors())} "
            f"repost={len(self._repost)} mode={self._modes.mode} "
            f"avail={'-' if avail is None else f'{avail / 1024:.0f}K'}B/s "
            f"period={self._modes.round_period_sec:.0f}s "
            f"backlog={pending_n} "
            f"eta={eta_text(pending_n, mode=self._modes.mode, avail_bps=avail)}"
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

    # ------------------------------------------------------------ 探活

    def probe(self, *, force: bool = False) -> bool:
        """hub 可达且**可用**（token 有效）？结果按 TTL 缓存（**失败**用更短的 `PROBE_FAIL_TTL_SEC`）。永不抛。"""
        self._maybe_revive()
        if not self.enabled or self.disabled_reason:
            return False
        now = self._now()
        ttl = float(self.probe_ttl)
        if self._reachable is False:
            ttl = min(ttl, self.probe_fail_ttl)  # 失败只可能更短（旧口径的 60s 平坦缓存不许回来）
        cached = self._reachable
        if (
            not force
            and not self._probe_dirty
            and cached is not None
            and now - self._probed_at < ttl
        ):
            return cached
        self._probed_at = now
        self._probe_dirty = False
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
            self._disabled_at = self._now()
            self.log(
                f"停用补传：{why}（产物照常落本地，训练不受影响；"
                f"{DISABLE_COOLDOWN_SEC:.0f}s 后自动重探一次）"
            )

    def _maybe_revive(self) -> None:
        """停用冷却到点 ⇒ 解封并逼一次真探活。**只在冷却窗之后**（当拍仍不重试）。

        为什么要有它（2026-10-16，评审 F1/C1）：`_sync` 首行 return 让停用变成「本会话零尝试」，
        而 401/403 很可能是暂时的（token 轮换 / hub 换实例 / 本机时钟）；现场「27 轮 0 送达」
        与「打点同期拿到 200」并存，正是这种「一条腿静默死掉」的形状。
        """
        if not self.disabled_reason:
            return
        if self._now() - float(self._disabled_at) < DISABLE_COOLDOWN_SEC:
            return
        why = self.disabled_reason
        self.disabled_reason = ""
        self._disabled_at = 0.0
        self._reachable = None  # 逼下一次探活真的出门（不吃旧负缓存）
        self.log(f"补传腿冷却结束（{DISABLE_COOLDOWN_SEC:.0f}s）——重新探活；原停用原因：{why}")

    def _throttled_log(self, key: str, msg: str) -> None:
        now = self._now()
        if now - self._last_log.get(key, 0.0) < LOG_THROTTLE_SEC:
            return
        self._last_log[key] = now
        self.log(msg)

    def _timeout_for(self, nbytes: int) -> float:
        """这一件该等多久（**按件缩放**，plan §4-W2）：`clamp(20s, bytes/4000, self.timeout)`。

        为什么不是「体积 ÷ 超时 = 需要速率」（评审 P0-3）：`timeout` 是**每次 socket 操作**的
        stall 超时，不是整请求预算 —— 稳定慢链路根本不会超时。这里只做一件事：把**小件**
        失败后的等待从 60s 压到 20–40s（失败得越早，恢复窗口越短），大件仍用 60s 上限。
        """
        n = max(0, int(nbytes))
        return max(
            MIN_ATTEMPT_TIMEOUT_SEC,
            min(float(self.timeout), float(n) / ATTEMPT_RATE_FLOOR_BPS),
        )

    def _note_failure(self, kind: str, detail: str) -> None:
        """记一次传输失败：**三触发**日志（第 1 次 / 每 `FAIL_LOG_EVERY` 次 / 恢复时）。

        为什么不用 `_throttled_log`（plan §4-W1-A-3）：300s 的节流配上「每 60s 失败一次」的节奏，
        现场 6 次失败只看见 2 行 —— 观测上像「这条腿停了」，而它其实在拼命重试。三触发让
        「第一次」与「第 10 次」都能被看见，又不按轮刷屏。
        """
        now = self._now()
        if not self._fail_n:
            self._fail_since = now
        self._fail_n += 1
        self._last_fail_kind = kind
        if self._fail_n == 1 or self._fail_n % FAIL_LOG_EVERY == 0:
            self.log(
                f"补传失败 x{self._fail_n}（{kind}｜{self._last_elapsed:.1f}s｜"
                f"{self._last_body_bytes / 1024:.0f}KB）: {detail}"
            )

    def _note_recovery(self) -> None:
        """一次真送达 → 如果之前连续失败过，打一行「链路恢复」（失败次数 / 持续秒数）。"""
        if not self._fail_n:
            return
        n = self._fail_n
        dur = max(0.0, self._now() - self._fail_since)
        self._fail_n = 0
        self._fail_since = 0.0
        self._last_fail_kind = ""
        self.log(f"链路恢复（失败 {n} 次 / {dur:.0f}s）——补传继续")

    # ------------------------------------------------------------ 待投递集

    def _ledger_its(self) -> set[int]:
        """账本（`metrics.jsonl`）里有行的轮次集合。**这就是「真轮次」的判据**。

        ★ 2026-10-11 事故（x24-stack5）：起点被当成 it1 推上 hub，hub 首写锁定 ⇒ 真 it1（权重与
        读数）永不到岸。起点与真轮次的**唯一结构差别就是「记不记账」**：
          * `ArtifactStore.checkpoint(row=None)` 是起点专用（`remote/plan_handoff.py:586` —— 全仓
            **唯一**省略 `row` 的调用点），`remote/artifacts.py:268` 的 `if row is not None:` 才追
            加行 ⇒ **有行 ⟺ 真轮次**；
          * 所以不需要指纹（「取哪一行」是 `_row_for` 的事，它配不上时只意味着不发 row，不意味着
            这一轮不该投），也不需要 `plan.start_it` 或 `plan.json`（`it <= start_it` 反而更宽：
            会把跨腿复用目录里 `start_it` **之前**的合法轮次一起压掉，而回溯它们正是本函数的本职）。

        为什么缓存：`pending()` 每拍被调，而账本行带 `perGame` 时可以到 MB 级。该文件是
        append-only ⇒ 按 `(size, mtime_ns)` 失效是安全的（对不上就重解析，永远不会读到旧集合）。
        """
        p = self.root / ArtifactStore.METRICS_NAME
        try:
            st = p.stat()
            key: tuple[int, int] | None = (int(st.st_size), int(st.st_mtime_ns))
        except OSError:
            self._ledger_cache = (None, set())
            return set()
        cached_key, cached = self._ledger_cache
        if cached_key == key:
            return cached
        its: set[int] = set()
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # 半截行（被 kill）——与 `ArtifactStore.rows` 同口径地跳过
            if isinstance(row, dict):
                it = row.get("it")
                if isinstance(it, int) and not isinstance(it, bool):
                    its.add(it)
        self._ledger_cache = (key, its)
        return its

    def pending(self) -> list[int]:
        """磁盘上有、记账里没有、**且是训练轮**的轮次（升序）。**这是「重启续投」的全部实现**。

        「是训练轮」= 账本里有它自己的行（见 `_ledger_its`）⇒ **起点永不回传**（D1：起点不是
        一轮训练，hub 拿到也无用；它只是上一次交接的产物）。2026-10-11 事故的根因就在这里：
        只认「目录名 `it-*` + 有 `weights.json`」的话，起点与真轮次完全不可区分。

        代价：撞上「权重已落盘、行还没写」的纳秒级窗口时，这一拍不投（下一拍投）——**延迟，
        不是丢**；而发一个无行的体上去会让 hub 把它锁成裸行（首写锁定）。
        """
        out: list[int] = []
        try:
            dirs = sorted(self.root.glob(f"{ArtifactStore.IT_PREFIX}*"))
        except OSError:
            return out
        rounds = self._ledger_its()
        for d in dirs:
            if not d.is_dir():
                continue
            name = d.name[len(ArtifactStore.IT_PREFIX) :]
            if not name.isdigit():
                continue
            it = int(name)
            if it in self._delivered or it in self._rejected:
                continue
            if it not in rounds:
                self._throttled_log(
                    f"no-row:{it}",
                    f"补传跳过 it{it}：账本里没有这一轮的行 ⇒ 不是训练轮"
                    "（起点/半截写入）——不上行",
                )
                continue
            # 名字两态都认（`weights.json`/`weights.delta`，2026-10-15 后缀约定）：产物目录里的
            # 那一份可能是压缩版（离线腿回传的就是容器，`remote.delta_files.named_for` 按内容定名）。
            if delta_files.find_existing(d, "weights.json") is not None:
                out.append(it)
        return out

    def pending_anchors(self, *, with_newest: bool = False) -> list[int]:
        """`pending()` 里**这一拍该发**的锚点轮（选法与代价 → `deliver_wire.pick_anchors`）。

        为什么这样切（plan §4-W1-C/D）：实时链路只负责「把有读数的轮次送回去」，其余轮次的
        权重与账本行走抽屉 + 离线包（那两条腿本来就整份带走）⇒ 带宽压在每 `eval_every` 轮一次。
        ★ `pending()` **一个字没窄**（D-3）：抽屉 / `status()` / 重投判据仍吃全量，所以
        「非锚点轮从安全网里消失」这个后果**不存在**——它们只是不进实时体。
        """
        # 锚点判据（`read_eval_iters`）与选法（`pick_anchors`）都在 `deliver_wire`：本类只留
        # 「缓存住哪」与「什么时候调」——缓存形状 `(size, mtime_ns)` 是读过它的类的事。
        key, its = read_eval_iters(self.root / ArtifactStore.EVAL_LOG_NAME, self._eval_iters_cache)
        self._eval_iters_cache = (key, its)
        # ★ 2026-10-23（plan §3.5 / D-2）：无 A 层读数时**不再退回全量**，按 `eval_every` 兜底，
        #   并把这个事实说出来（旧行为是静默全量发，等于「网络好时反而发更多、网络差时也不降档」）。
        if not its and self.eval_every > 0:
            self._throttled_log(
                f"anchor-fallback:{self.eval_every}",
                f"补传锚点兜底：{ArtifactStore.EVAL_LOG_NAME} 里没有 A 层读数 ⇒ "
                f"锚点按每 {self.eval_every} 轮取（plan D-2）",
            )
        return pick_anchors(self.pending(), its, with_newest=with_newest, every=self.eval_every)

    # ------------------------------------------------------------ 备份抽屉（拍板-3）

    def _drawer_for_write(self) -> BackupDrawer | None:
        """抽屉写方（`backup_root` 空 = 这条腿不做备份）。**惰性建**，因为父侧镜像
        （`deliver_proc.backup_pending`）是在构造**之后**才把 `backup_root` 补上去的 ——
        在 `__init__` 里建好就是在那个调用点上静默不备份（比不实现更糟：它看起来在工作）。
        日志取**调用时**的 `self.log`（同一个理由：父侧会现换）。
        """
        if self._drawer is None and self.backup_root:
            self._drawer = BackupDrawer(
                source_root=self.root,
                backup_root=self.backup_root,
                log=self.log,
                throttled=self._throttled_log,
            )
        return self._drawer

    def backup_pending(self, *, force: bool = False) -> int:
        """把「磁盘上有、账本里没有、**且卡过宽限期**」的轮次各备份一份到网络侧抽屉。

        `force=True` = 段末 sweep（最后一次机会）：欠着的全备，不看年龄。

        为什么是这条判据（`pending()`）：它就是「回传失败」的**持久化**定义（重启会话照样成立），
        不需要新状态源。拷贝策略（拷哪些件 / 何时跳 / 失败算什么）在 `remote/backup_drawer`
        ——那边是它的所有者（不抛 · 有界 · 幂等 · 常态零写入）；这里只管两件事：
        「算哪些轮次待投」（本类的定义）与「把读数记到 `status()` 看得见的地方」。
        """
        drawer = self._drawer_for_write()
        if drawer is None:
            return 0
        try:
            rounds = self.pending()
        except Exception as e:  # 判据面坏了也只记一笔
            self._throttled_log("backup-pending", f"备份跳过：算不出待投轮次（{e}）")
            return 0
        if not force:
            # ★ 2026-10-29：宽限判据（见 `BACKUP_GRACE_SEC`）——**送达中的轮次一个字节都不碰**。
            rounds = [it for it in rounds if self._round_age(it) >= self.backup_grace]
        if rounds:
            r = drawer.backup_rounds(rounds)
            self._backup_stat = (r.made, r.bytes, r.failed, r.skipped)
            self._drawer_stat = (r.total, r.ledger, r.round_avg)
            self.log(
                f"备份={len(rounds)} 轮待投（本轮新写 {r.made}｜字节 {r.bytes}｜失败 {r.failed}"
                f"｜账本未变跳过 {r.skipped}）｜抽屉 {drawer.root}（合计 {r.total} 字节）"
            )
        self._prune_drawer(drawer)
        return self._backup_stat[0]

    def _round_age(self, it: int) -> float:
        """这一轮在盘上躺了多久（秒）。读不到 ⇒ 0（当「刚写下」，绝不抢先备份）。"""
        d = self.root / f"{ArtifactStore.IT_PREFIX}{int(it):03d}"
        f = delta_files.find_existing(d, "weights.json")
        return 0.0 if f is None else max(0.0, self._now() - f.stat().st_mtime)

    def _prune_drawer(self, drawer: BackupDrawer) -> None:
        """判据化删除抽屉里**已确认送达**的轮次（D3：回传成功即删）。

        顺序铁律（plan §4.2-3）：`POST 200 → _save_ledger()` **之后**才允许删 —— 本函数只读内存里的
        `_delivered`，而它是在账本落盘之后才追加的；别另开一条「重读磁盘」的路（那会把顺序反过来）。

        每拍都扫抽屉是**网络盘的目录列表**，太贵 ⇒ 只在「确认集变过」（有新送达）或段末（`close()`
        经 `backup_pending`）扫一次。读数进 `status()`：`backup_pruned`/`prune_failed`/`backup_orphans`。
        """
        if len(self._delivered) == self._pruned_mark and not self._prune_forced:
            return
        try:
            rounds = set(self._ledger_its())  # 本地**真轮次**（内容对账用；不读网络盘）
        except Exception:
            rounds = set()
        p = drawer.prune_delivered(
            confirmed=set(self._delivered),
            local_rounds=rounds,
            ledger_ok=self._ledger_readable,
            now=self._now(),
        )
        self._prune_stat = (p.pruned, p.failed, p.orphans, p.skipped)
        self._pruned_mark = len(self._delivered)
        self._prune_forced = False
        if p.skipped:
            self._throttled_log("prune-skip", f"抽屉不删：{p.skipped}")

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
        """账本里与本轮权重字节相符的那一行（判据全在 `deliver_parts.row_for`，W3 抽出共用）。"""
        return deliver_parts.row_for(self.root, int(it), weights_fp, log=self.log)

    # ------------------------------------------------------------ 投递

    def sync(self, *, force_probe: bool = False, budget_sec: float | None = None) -> int:
        """把待投递轮次尽量推上去，返回本轮投递成功数。**永不抛**。

        `force_probe` = 绕过探活的**负结果 TTL**：段末 / 子进程收线时用——那是最后一次机会，
        不能被上一次失败静默吞掉（否则整段最该送出去的那几轮会“什么都没做”地结束）。
        同时它也是**段末语义**：待投集里最新那一轮排最前（D-1 末轮强制补发）。

        `budget_sec` = 这一拍的**硬预算**（秒；`None` = 不限）。段末收线时由子进程传剩余预算：
        `_sync` 在**起跑前**判剩余量，并把单件超时夹到剩余量上 ⇒ 90s 的 flush 不会被 4×60s
        的连续超时吃穿（那样 `deliver_proc._stop_child` 会直接 `terminate()` 子进程，
        段末那次补发当场失效）。

        同步模式（`background=False`）的 `submit_round` 走这里 ⇒ 备份观测也在这里（与后台腿
        `_push_backlog` 同口径；两处都只调 `backup_pending`，幂等）。
        """
        try:
            n = self._sync(force_probe=force_probe, budget_sec=budget_sec)
        except Exception as e:  # 兜底：补传的任何意外都不得波及训练
            self.log(f"补传异常（忽略，训练继续）：{type(e).__name__}: {e}")
            n = 0
        self.backup_pending()
        return n

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

    def _owe_repost(self, it: int) -> bool:
        """登记一次「欠评估读数重投」，**不**唤醒线程（`_sync` 自己会把账本置脏并落盘）。

        与 `mark_repost` 的差别只有两条：不 `notify`（降级档发生在 `_sync` 里面，下一拍就是同一拍
        的下一轮，额外唤醒只会多绕一圈），也不单独落盘（`_sync` 收尾那次 `_save_ledger` 会带上它）。
        返回「本次真的新登记了」（用于 `changed` 置脏，不置脏就会让 `owed_reposts` 停在旧水位）。
        """
        with self._cv:
            if int(it) in self._repost:
                return False
            self._repost.add(int(it))
            self._want_sync = True
            return True

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

    def _sync(self, *, force_probe: bool = False, budget_sec: float | None = None) -> int:
        """`force_probe` = 绕过探活的负结果 TTL；`budget_sec` = 本拍硬预算（见 `sync`）。

        为什么段末必须绕过：`probe()` 把「不可达」缓存 `PROBE_TTL_SEC` 秒，而段末收线是
        **最后一次机会**——若最后一次探活在 60s 内失败过，不强制就会让整个段末 flush
        静默什么都不做（最该送出去的那份摘要就此丢掉）。

        排队顺序：真积压本轮的在前、重投（语评估行）的在后——重投那一轮的权重早在 hub 上，
        它只是「把新的读数补上」，不该挡在真正的待投递轮次前面（代价：backlog ≥ `sync_cap` 时
        重投会饿着，而 backlog 总会清空 ⇒ 接受的饥饿，与本次改动前同语义）。

        `_repost` 是**持久集**（2026-10-07）：不再「先取走、失败再放回」——那样会留下一个
        「只唤醒、不消费」的入口，正是 2026-10-06 忙等事故的成因。只有**真正被 `_post_artifact`
        处理过**且返回 `ok`/`skip` 的轮次才销账；被 `sync_cap` 截断在外的、返回 `stop` 的一律留着。
        ⚠ 例外（2026-10-21 降级档）：**降级体**的 `ok` 不销账 —— 那一趟只把权重送到岸，
        评估读数还欠着（销了它就真的丢了）。

        失败**不 `break`**（G2）：返回 `stop`/`deferred` 就续推下一轮，直到 `MAX_FAILS_PER_PASS`——
        每次失败已经付了一个超时窗口，所以「续推」与「每拍有界」必须同时成立。
        """
        if not self.enabled:
            return 0
        self._maybe_revive()
        if self.disabled_reason:
            self._retry_n += 1
            self._last_error = self.disabled_reason
            self._next_try = self._now() + DISABLE_COOLDOWN_SEC
            return 0
        with self._cv:
            repost = sorted(self._repost)
        # 候选集 = **锚点轮**（有 A 层评估读数的轮次）+ 重投；段末带上最新那一轮（D-1）。
        # 为什么不是 `pending()`（plan §4-W1-C/D）：实时链路只负责「把有读数的轮次送回去」，
        # 其余轮次的权重与账本行走抽屉 + 离线包（两条腿都是整份）。`pending()` 本身没收窄
        # （D-3）——抽屉 / `status()` / 重投判据照吃全量，安全网一格没少。
        # ★ 2026-10-24（评审 P0-1）：档位只剩两档——`every`（每一轮都发；形态在写盘时定，
        #   锚点整份/其余 delta）与 `anchors`（只发有 A 层读数的轮）。`full`/`bulk` 已退场：
        #   它们发的是同一批字节，留着只会让日志谎报「每轮整份」。
        mode = self._modes.tick_mode()
        todo = self.pending_anchors(with_newest=force_probe) if mode == "anchors" else self.pending()
        # 重投只服务**真轮次**（纵深防御：旧账本/旧会话里的起点或半截轮次不该被重投带出去）。
        rounds = self._ledger_its()
        todo += [it for it in repost if it not in todo and it in rounds]
        # ★ 2026-10-24（评审 P0-3）：统计批**不能**被「没有锚点待发」的早退吃掉。
        #   抖动期最常见的形状就是「锚点都发完了、非锚点轮还没发」——那几轮的 row 只能走统计批。
        #   所以先算 gap，只有「权重也没活 + 统计也没活」才早退。
        gap = (
            self._metrics_gap()
            if (self.metrics_parts and not self._metrics_unsupported)
            else []
        )
        if mode != "anchors":
            # `every` 档下权重体自带 row：这一拍就要发权重的轮次**不再单独发统计**，
            # 否则同一轮既走统计批（~24 KB）又走权重体（row 内嵌），还会在课程账本里
            # 落两条 `iteration`（一条 provisional、一条真权重）。抖动档相反：锚点轮的
            # 权重可能发不出去，那几轮的统计必须先走（宁可重复一次，也不能等权重）。
            todo_set = set(todo)
            gap = [it for it in gap if it not in todo_set]
        if not todo and not gap:
            self._retry_n = 0
            self._last_error = ""
            self._next_try = 0.0
            return 0
        if not self.probe(force=force_probe):
            # 还没送到：重投留在 `_repost` 里（下一拍 / 下一次会话再试）。
            # 读数只**给人看**（plan §4.2-1：不做退避判决）—— 下一拍的可试时刻就是探活负缓存的长度
            # （连不上时 `PROBE_FAIL_TTL_SEC` 那个短窗，抖动恢复不用等一整轮）。
            self._retry_n += 1
            self._last_error = self.disabled_reason or "hub 不可达"
            self._next_try = self._now() + (
                LOG_THROTTLE_SEC if self.disabled_reason else float(self.probe_fail_ttl)
            )
            return 0
        done = 0
        sent: list[int] = []
        changed = False
        fails = 0
        #: 预算从**这一拍开头**算，统计批花掉的时间不能从下一件里再借一遍（评审 P1-9）。
        deadline = (self._now() + float(budget_sec)) if budget_sec is not None else None
        # ★ S5（2026-10-23）：**统计批先走**（小件优先）。权重发不动时读数也要到（D11）。
        #   失败不销账 ⇒ 下一拍照旧再试。
        if gap and (deadline is None or self._now() < deadline):
            remaining = (
                max(0.0, deadline - self._now()) if deadline is not None else budget_sec
            )
            if self._post_metrics_batch(gap, budget_sec=remaining):
                changed = True
        if not todo:
            # 这一拍只有统计可发：送达/失败都要把账落盘；没送达就留一句可观测的失败原因。
            if changed:
                self._save_ledger()
                self._retry_n = 0
                self._last_error = ""
                self._next_try = 0.0
            else:
                self._retry_n += 1
                self._last_error = "统计批没有送达（下拍再试）"
                self._next_try = self._now() + LOG_THROTTLE_SEC
            return 0
        # 段末（`force_probe`）：最新那一轮排最前 —— D-1「末轮强制补发」的落地形态。
        # 为什么不是「全部降序」：升序才是续跑进度的自然顺序，只有最后那一轮值是**整段**的终点。
        if force_probe and len(todo) > 1:
            newest = max(todo)
            todo = [newest] + [it for it in todo if it != newest]
        for it in todo[: self.sync_cap]:
            if deadline is not None and self._now() >= deadline:
                break  # 预算用尽：**不**再起一件（起跑就可能越界，段末会被 terminate）
            outcome = self._post_artifact_guarded(it, budget_sec=budget_sec)
            if outcome in ("stop", "deferred"):
                # ★ 2026-10-21（评审 P0-4 / G2）：**不再 break**。
                # `stop` 只说明「这一件这次没过去」，不该让后面的轮次一次都不被尝试——
                # 积压 14 轮时 `break` 等于永远只赌队列第 1 轮（现场 it69…it81 从未被 POST 过）。
                # 但也不能无限接着打：每次失败已经付了一个超时窗口，所以有上限。
                if outcome == "deferred":
                    changed = True  # 降级档：`owed_reposts` 多了这一轮，必须落盘
                fails += 1
                if fails >= MAX_FAILS_PER_PASS:
                    break
                continue
            if outcome in ("ok", "skip"):
                with self._cv:
                    # 降级体送达**不销**重投：那一趟只把权重送到岸，评估读数还欠着。
                    # （非降级趟 / 内容拒收照旧销账——否则 `_repost` 会变成一个永不熄灭的热循环。）
                    if it in self._repost and not (outcome == "ok" and self._attempt_degraded):
                        self._repost.discard(it)
                        changed = True
                self._post_failures.pop(it, None)
            if outcome == "ok":
                done += 1
                sent.append(it)
                # `row` **真的**随这一趟到达才销统计账（评审 P1-7）：权重体可能因为账本行与
                # 权重指纹不符而没有 `row`（`build_round_body` 宁可不带），那一轮必须留给
                # 统计批再试——否则「权重到了、统计永久丢」且账本显示已送。
                if self._last_round_had_row:
                    if it not in self._metrics_sent:
                        self._metrics_sent.add(it)
                        changed = True
                    if it in self._metrics_owed:
                        self._metrics_owed.discard(it)
                        changed = True
                elif it not in self._metrics_owed:
                    # 这一轮已进 `_delivered`，`pending()` 不会再列它 ⇒ 统计欠账必须单独记，
                    # 否则下一拍 gap 看不见它，权重成功反而把统计静默丢定。
                    self._metrics_owed.add(it)
                    changed = True
            # "skip" = 这一轮被内容拒收（已记进 `_rejected`）：跳过它，后面的照推。
            # 拒收**本身也要落盘**（D6）：TTL/上限的年龄面就住在那条记录里，而「本拍只有拒收、
            # 没有成功投递」的组合只会让 `done=0` ⇒ 不置脏就会连旧记录一起停在上一次的水位。
            elif outcome == "skip":
                changed = True
        if done or changed:
            self._save_ledger()
        if done:
            self._retry_n = 0
            self._last_error = ""
            self._next_try = 0.0
            # 成功投递**不打日志**（每轮一句、纯刷屏）；失败那条（投递异常 / 连丢）才响。
        elif not changed:
            # 这一拍一轮都没送出去（且没有别的脏东西要落盘）⇒ 记进可观测面：连续失败 + 原因。
            self._retry_n += 1
            self._last_error = "这一拍没有轮次送达（传输失败/被拦；下拍再试）"
            self._next_try = self._now() + LOG_THROTTLE_SEC
        return done

    def _post_artifact_guarded(self, it: int, *, budget_sec: float | None = None) -> str:
        """`_post_artifact` 的**毒丸防护**：同一轮连续 `POST_FAILURE_LIMIT` 次异常 ⇒ 当内容问题处理。

        为什么需要：`_repost` 是持久集 ⇒ 一个**永远投不出去**的轮次（权重文件被删/盘抖动/编码炸）
        会在每次唤醒时重抛一次，并让整趟 `_push_backlog` 中止 —— 既不响亮也不收敛。
        """
        try:
            return self._post_artifact(it, budget_sec=budget_sec)
        except Exception as e:
            n = self._post_failures.get(it, 0) + 1
            self._post_failures[it] = n
            why = f"{type(e).__name__}: {e}"
            if n >= POST_FAILURE_LIMIT:
                return self._reject_round(it, f"连续 {n} 次投递异常（{why}）")
            self.log(f"补传 it{it} 投递异常（第 {n} 次，{why}）——本轮放弃，下轮再试")
            return "stop"

    def _full_parts_for(self, it: int, *, mode: str) -> tuple[bytes, bytes, str]:
        """这一轮要发的 `(权重字节, opt字节, wire_form)`——档位决定发整份还是原件（装配见
        `deliver_parts.full_parts_for`；腿本体只留「怎么发」）。

        ⚠ **没人调它**（生产路径走 `_post_artifact` 的「盘上是什么发什么」）。留着是为了
        `plan/offline-deliver-full-and-gaps` 的档位语义可读；**别接上**——理由在
        `deliver_parts.full_parts_for` 的 docstring（会把 `plan/anchor-full-rounds` 的
        写盘形态重新展开，省量归零）。"""
        from remote import opt_standard, weights_standard  # L1：L2 才持有这条边；full 档才付

        return deliver_parts.full_parts_for(
            self.root,
            self.run_id,
            int(it),
            mode=mode,
            expand_weights=lambda p: weights_standard.standard_weights_copy(
                p, self.root, course=self.course, log=self.log
            ),
            expand_opt=lambda p: opt_standard.standard_opt_copy(p, self.root, log=self.log),
            log=self.log,
        )

    def _post_artifact(self, it: int, *, budget_sec: float | None = None) -> str:
        """投递一轮产物（权重 + opt + 账本行 + 评估读数）。返回：

          * ``"ok"`` —— 这一轮可以记为已投递（含 hub 409「已有这一轮」的幂等分支）；
          * ``"skip"`` —— 这一轮被**内容**拒收（已记进 `_rejected`），继续推后面的轮次；
          * ``"stop"`` —— 这一件这次没过去（没送达 / 5xx / 鉴权），**后面的轮次照推**；
          * ``"deferred"`` —— 带评估读数的大体没过去，已降级成「只发权重」并登记了一次重投。
        """
        # 先归零：本轮若在读权重之前就早退（文件没了/空），别把**上一轮**的降级/销账标记
        # 留给这一轮的判据。
        self._attempt_degraded = False
        self._last_round_had_row = False
        # 降级档（§4-W1-B′）：这一轮**还在本会话降级过**且权重尚未送达 ⇒ 先只发「不评估读数」的小体。
        # 一旦权重到岸（`_delivered`），同一轮的重投（补评估读数）仍然带全量——两份职责分开。
        self._attempt_degraded = it in self._degraded and it not in self._delivered
        eval_rows = [] if self._attempt_degraded else self._eval_rows_for(it)
        # ★ 2026-10-23（`plan/anchor-full-rounds` S6）：**补传体不摘逐局明细**（`pergame=True`）。
        #   这条腿是云机逐局明细的**唯一**实时通道：权重体送出过的轮次会被记进 `_metrics_sent`
        #   （下面的销账分支），于是它们**不会**再走统计批 ⇒ 这里摘掉就是永久丢（现场 y31-null
        #   28 轮全走权重体、0 条 `offline_metrics`，控制台的耗时/击杀/承伤/残血/道具全空）。
        #   体积代价 4.3 KB/轮（实测 94 局 23.3KB 原 → 4.3KB 线上）。Q2 的摘键仍留在
        #   `wire_row` 的默认值上（在线实时体若恢复，那条面照旧只带聚合列）。
        # ★ 2026-10-23（W3）：**装配只此一处**——本腿与本地 outbox（`LocalOutboxDeliverer`）
        #   都调 `deliver_parts.build_round_body`，字节一致是构造性质而不是测出来的巧合。
        #   读权重失败/空文件 ⇒ `None`：扫描时还在、读时没了是瞬时状态，**不标记已投递**。
        built = deliver_parts.build_round_body(
            self.root,
            self.run_id,
            int(it),
            course=self.course,
            hub_lease=self.hub_lease,
            worker_name=self.worker_name,
            eval_rows=eval_rows,
            row_for=self._row_for,
            lineage=self._manifest(),
            pergame=True,
            now=self._now(),
            log=self.log,
        )
        if built is None:
            return "stop"
        body, raw, wfp, opt_form, self._last_pergame_games = built
        #: 这一趟的体里是否真的带了账本行（只有带了的轮次才能销统计账；见 `_sync`）。
        row_obj = body.get("row")
        self._last_round_had_row = isinstance(row_obj, dict) and bool(row_obj)
        if self._last_round_had_row and isinstance(row_obj, dict):
            # 轮周期读数（plan §7.1）：只认真投递里的 `wall_sec`，不另扫账本。
            self._modes.note_round_period(row_obj.get("wall_sec"))
        # ★ 形态读数（S4）：这一轮盘上 opt 是整份还是容器（记账在体建好之后）。
        self._opt_wire = deliver_wire.note_opt_wire(
            self._opt_wire, opt_form, len(str(body.get("opt_tar_b64") or ""))
        )
        if len(raw) > OFFLINE_ARTIFACT_BODY_MAX:
            return self._reject_round(
                it, f"补传体 {len(raw)}B 超上限 {OFFLINE_ARTIFACT_BODY_MAX}B", wfp
            )
        t0 = self._now()
        status, resp = self._post(OFFLINE_ARTIFACT_PATH, raw, budget_sec=budget_sec)
        if not status:  # 没送达（_post 已记读数）
            self._modes.note_disconnect()
            if eval_rows and it not in self._degraded:
                # 降级档（§4-W1-B′）：带评估读数的大体（现场 1.24MB）没过去 —— 下一拍
                # 先用「只发权重/opt/账本行」的体（现场 322KB）把**不可再生**的那三件送到；
                # 评估读数登记成一次重投（`owed_reposts` 落盘，跨会话不丢），等链路好一些再补。
                # 为什么这比「拆四件」便宜：hub 的 `duplicate` 分支**本来**就会把重投里的
                # `eval_rows` 并进课程账本（`merge_eval_rows`），所以零契约改动、零按件记账。
                self._degraded.add(it)
                self._owe_repost(it)
                self.log(
                    f"补传 it{it}：带 {len(eval_rows)} 条评估读数的体（{len(raw) / 1024:.0f}KB）没过去"
                    f" ⇒ 下一拍只发权重/opt/账本行，评估读数另欠一次（`owed_reposts`）"
                )
                return "deferred"
            return "stop"
        if status in (200, 201):
            self._delivered.add(it)
            # ★ 档位机的吞吐输入：这一趟真过去了（体大小 / 耗时）。
            self._modes.note_delivery(
                int(self._last_body_bytes), max(1e-6, self._now() - t0)
            )
            self._note_recovery()
            return "ok"
        if status == 409:
            # ★ 2026-10-11 事故：409 曾经与 200 同一个分支（`_delivered.add(it)` + "ok"）——
            # 于是 hub 说「同一轮已有**不同**字节」时，这一轮被记成「投递成功」、本会话永不重试，
            # 日志还写「幂等」。hub 侧 duplicate 仍回 200（`hub/offline.py::_post_offline_artifact`），
            # 这条腿上的 409 只有一个来源（`store_offline_artifact` 的 conflict）⇒ 当成内容拒收：
            # 只丢这一轮、其余照推、新会话会再试一次（指纹记账）。
            return self._reject_round(
                it,
                "hub 冲突：本轮在 hub 上已有**不同**权重，不覆盖"
                f"（{_err_text(resp)}）——需人裁决后清理 hub 侧那一份",
                wfp,
            )
        if status in (401, 403):
            self._disable(f"补传 it{it} 被拒（HTTP {status}）——本会话停用补传")
            return "stop"
        if status in (400, 413, 422):
            return self._reject_round(it, f"HTTP {status}: {_err_text(resp)}", wfp)
        # 5xx / 网关错 = 链路问题（内容错已在上面拒收，不算断连）⇒ 记进断连滑窗。
        self._modes.note_disconnect()
        self._reachable = False
        self._note_failure("http", f"it{it} HTTP {status}: {_err_text(resp)}")
        self._throttled_log(
            "post", f"补传 it{it} 失败（HTTP {status}: {_err_text(resp)}）——本轮放弃，下轮再试"
        )
        return "stop"

    #: 单轮补传携带的评估行上界（防体超限；正常一轮 A 层语料 = 关数×种子数，双轨 100）。
    EVAL_ROWS_CAP = 400

    def _eval_rows_for(self, it: int) -> list[dict]:
        """本轮评估读数（实现住 `deliver_parts.eval_rows_for`；判据一字不改）。"""
        rows, raw_b, wire_b = deliver_parts.eval_rows_for(
            self.root, int(it), cap=self.EVAL_ROWS_CAP, log=self.log, throttled_log=self._throttled_log
        )
        self._eval_rows_raw_bytes, self._eval_rows_wire_bytes = raw_b, wire_b
        return rows

    def deliver_result(
        self,
        *,
        it_end: int,
        state: str,
        summary: dict | None = None,
        end_it_reached: bool = False,
        seg_end_kind: str = "",
    ) -> bool:
        """段末摘要（跑到哪、什么状态、失败原因）。best-effort；**永不抛**。

        与逐轮产物的差别：摘要是**会变的**（同一 run 续跑后 `it_end` 更大），hub 侧覆盖写
        ——它不是不可变快照，而「这条腿现在到哪了」的最新答案。

        `end_it_reached`（T6）：本段是否跑到计划终点（由调用方 `plan_run._drive` 用
        `_end_it_reached` 判定，只有 `complete`/`noop` 且 `it_end >= plan.end_it` 才是）。
        `seg_end_kind`（J2，三态 `open`/`course`/`segment`；`""` = 非段末或未知）：hub
        据此决定封课还是触发下一段（判据在 `plan_run._seg_end_kind`）。
        """
        try:
            if not self.enabled:
                return False
            self._maybe_revive()
            if self.disabled_reason:
                return False
            if not self.probe():
                return False
            # ★ 2026-10-23（W3）：体装配搬到 `deliver_parts.build_result_body`（唯一实现）——
            #   本地 outbox 的「进程内降级」要用**同一条**装配才能保证与 HTTP 腿同字节。
            _body, raw = deliver_parts.build_result_body(
                run_id=self.run_id,
                it_end=int(it_end),
                state=state,
                summary=summary,
                end_it_reached=end_it_reached,
                seg_end_kind=seg_end_kind,
                delivered=len(self._delivered),
                lineage=self._manifest(),
                source_dir=str(self.root),
                course=self.course,
                now=self._now(),
            )
            status, _resp = self._post(OFFLINE_RESULT_PATH, raw)
            if not status:
                return False  # 没送达（_post 已记一笔）
            if status in (200, 201):
                self._note_recovery()
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

    def _post(
        self, path: str, raw: bytes, *, budget_sec: float | None = None
    ) -> tuple[int, bytes]:
        """POST 一趟。**永不抛**：传输层异常 → `(0, b"")`（状态 0 只在此出现 = 没送达）。

        为什么要在这儿收口而不是在 `sync()` 外层兜：走外层兜会让「本轮已投递的几轮」
        留在内存里却没落 `delivered.json`（异常把 `_save_ledger` 跳过了）——下次会话重传
        一遍。全程可重入的代价不该由记账来承担。

        三条 2026-10-21 的纪律（plan §4-W1-A / W2）：

          * 超时**按件缩放**（`_timeout_for`），段末再夹到**剩余预算**上 ⇒ 90s 的 flush
            不会被 4×60s 吃穿（`deliver_proc._stop_child` 会直接 terminate）；
          * 传输失败**不置 `_reachable=False`**（那会把「hub 可达」也判死），只作废探活的成功缓存；
          * 每次尝试都记 **elapsed** —— 它是「60s socket / 100s CF / 20s 探活」三档的唯一判据。
        """
        timeout = self._timeout_for(len(raw))
        if budget_sec is not None:
            # 下限 5s：太小会把「能过的件」也判死，而 5s 已经够建连 + 走完一个小体。
            timeout = max(5.0, min(timeout, float(budget_sec)))
        self._last_body_bytes = len(raw)
        t0 = time.monotonic()
        try:
            status, body = self._opener(
                f"{self.base_url}{path}", raw, self._headers(), timeout
            )
        except Exception as e:
            self._last_elapsed = max(0.0, time.monotonic() - t0)
            # ★ 只作废**探活成功缓存**：数据面失败与「hub 不可达」是两件事（评审 P1-1）。
            # 下一拍会立刻重探一次（≤20s），探通了就接着推；`_reachable` 的语义与负缓存不变。
            self._probe_dirty = True
            kind = "timeout" if _is_timeout(e) else "net"
            self._note_failure(kind, f"{type(e).__name__}: {e}")
            return 0, b""
        self._last_elapsed = max(0.0, time.monotonic() - t0)
        return status, body

    # ------------------------------------------------------------ 状态（日志/自检用）


    def status(self) -> dict:
        """一行式状态（给启动日志与 operator 看：补传在不在、投了几轮、卡在哪）。"""
        pending = self.pending()  # 两处读数都用它（每拍被调，别算两遍）
        return {
            "enabled": self.enabled,
            "hub_url": self.base_url,
            "run_id": self.run_id,
            "course": self.course,
            "lease": bool(self.hub_lease),
            "delivered": len(self._delivered),
            "pending": len(pending),
            #: 这一拍**真会发**的轮次（锚点轮；`pending()` 的子集）。两个读数必须一起读：
            #: `pending - anchors` = 只走抽屉/离线包的那几轮（不是“卡住”）。
            "pending_anchors": len(self.pending_anchors()),
            #: 上一轮实时体里被摘掉的逐局明细局数（Q2）：>0 = 该轮控制台逐局弹窗等包。
            "pergame_deferred": int(self._last_pergame_games),
            #: 轮形态（`plan/anchor-full-rounds` S4）：上一次发出去的 opt 是什么形态 / 本会话
            #: 累计省下的线上字节（参照 = 最近一份整份的 `opt_tar_b64` 长度）。
            "opt_form": self._opt_wire[0],
            "opt_saved_bytes": int(self._opt_wire[1]),
            # ★ 2026-10-23（S7）：档位机读数。`mode` = 这一拍的档（`adaptive=False` ⇒ 恒
            # `anchors`）；`avail_bps` = 实测吞吐（`None` = 还没成功投过）；`backlog` =
            # 还没发权重的轮数；`eta` = 按当前档把 backlog 发完还要多久（`-`/`>1h`）。
            "mode": self._modes.mode,
            "adaptive": self.adaptive,
            "avail_bps": self._modes.avail_bps,
            "disconnects": self._modes.disconnects,
            #: 判档用的轮周期（最近 5 轮 `row.wall_sec` 中位数；冷启动 15s）与这一档的需求。
            "round_period": round(float(self._modes.round_period_sec), 2),
            "need_bps": round(
                need_bps(self._modes.mode, round_period_sec=self._modes.round_period_sec), 1
            ),
            "backlog": len(pending),
            "eta": eta_text(len(pending), mode=self._modes.mode, avail_bps=self._modes.avail_bps),
            # ★ 统计批（S5）：还剩多少轮的 `row` 没单独上行；`metrics_sent` = 已上行的轮数。
            "metrics_backlog": len(self._metrics_gap()),
            "metrics_sent": len(self._metrics_sent),
            #: 权重已到岸但体里没 row 的轮次（统计欠账；`metrics_backlog` 的子集）。
            "metrics_owed": len(self._metrics_owed),
            "metrics_unsupported": self._metrics_unsupported,
            "metrics_last": dict(zip(("rounds", "bytes"), self._metrics_stat, strict=True)),
            "rejected": dict(self._rejected),
            #: 拒收记录的年龄面（D6）：`it → (记了几次, 几秒前记的)`。到 TTL/上限会自动复活，
            #: 人也可以 `reset_rejected()`（或控制台的同名单个动作）现在就把某一环摆回台面。
            "rejected_age": {
                it: (int(self._rejected_tries.get(it, 1)), self._now() - self._rejected_at.get(it, 0.0))
                for it in self._rejected
            },
            "rejected_ttl_sec": float(REJECTED_TTL_SEC),
            #: 还没送达的重投（§10.3）：段末核对与子进程状态面（`delivered.json` 的 `owed_reposts`）
            "reposts_unsent": len(self._repost),
            #: 埋点/探针读数（§2.4）：`idle_spins` **恒应为 0**（非 0 = 有人在空转）
            "ticks": self._drain_ticks,
            "idle_spins": self._idle_spins,
            "result_done": self._result_done,
            "disabled_reason": self.disabled_reason,
            #: 停用了多久（秒；0 = 没停用）。冷却到 `DISABLE_COOLDOWN_SEC` 会自动解封（审查 F1/C1）。
            "disabled_sec": (self._now() - self._disabled_at) if self.disabled_reason else 0.0,
            "backed_up": int(self._backup_stat[0]),
            "backup_bytes": int(self._backup_stat[1]),
            "backup_failed": int(self._backup_stat[2]),
            #: 「变化才拷」跳过的账本件数（D8）：连做 3 拍时后两拍应为 2（`metrics`/`eval_log` 都没变）。
            "backup_skipped": int(self._backup_stat[3]),
            #: 抽屉**清理**（D3 判据化删除）：删了几轮 / 删失败 / **孤儿保留**（账本里没证据且本地
            #: 也没同一份 —— 绝不默默不删）/ 这一趟不删的原因。
            "backup_pruned": int(self._prune_stat[0]),
            "prune_failed": int(self._prune_stat[1]),
            "backup_orphans": int(self._prune_stat[2]),
            "prune_skipped": str(self._prune_stat[3]),
            #: 重试节奏的**只读面**（plan §4.2-1：给人看，**不做退避判决**）：
            #: 连续失败次数 / 最后一次失败原因 / 距离下一次可重试还有几秒。
            "retry_n": int(self._retry_n),
            "last_error": str(self._last_error),
            "next_try_sec": max(0.0, float(self._next_try) - self._now()) if self._next_try else 0.0,
            #: 传输失败的读数面（W5 / plan §1.1）：最后一次尝试的**体量**与**耗时**——
            #: elapsed 是「60s socket 超时 / 100s Cloudflare / 20s 探活」三档的唯一判据。
            #: `fail_n` 连续失败次数（送达即归零），`fail_for_sec` 这一串失败持续了多久。
            "last_body_kb": round(self._last_body_bytes / 1024.0, 1),
            "last_elapsed_sec": round(float(self._last_elapsed), 2),
            "last_fail_kind": str(self._last_fail_kind),
            "fail_n": int(self._fail_n),
            "fail_for_sec": (
                round(max(0.0, self._now() - self._fail_since), 1) if self._fail_n else 0.0
            ),
            #: 降级档（§4-W1-B′）：本会话里「带评估读数的大体没过去 ⇒ 先只发权重」的轮次。
            "degraded": sorted(self._degraded),
            #: 本轮评估行白名单投影前后的字节数（0/0 = 本轮没评估行；不相等 = 真瘦了）。
            "eval_rows_bytes": (int(self._eval_rows_raw_bytes), int(self._eval_rows_wire_bytes)),
            #: 探活成功缓存是否已被数据面失败作废（下一拍会真的重探）。
            "probe_dirty": bool(self._probe_dirty),
            #: 抽屉字节读数（§3.3 DoD-2c + §14 容量行）：总 / 两账本 / 单轮均字节。
            #: `drawer_budget_bytes` = 下一步大概还要往网络盘写多少 = `pending × 均字节 + 两账本`。
            "drawer_bytes": int(self._drawer_stat[0]),
            "drawer_ledger_bytes": int(self._drawer_stat[1]),
            "drawer_round_avg_bytes": int(self._drawer_stat[2]),
            "drawer_budget_bytes": (
                len(pending) * int(self._drawer_stat[2]) + int(self._drawer_stat[1])
            ),
            "background": self.background,
            "drain_alive": bool(self._thread is not None and self._thread.is_alive()),
        }


#: 供调用方一致性检查（测试与 operator 脚本读它，避免抄第二份路径常量）。
DELIVERED_NAME = OFFLINE_DELIVERED_NAME

#: 租约 token 传给**子进程**用的 env 名（★M1b/P1-1「送信端」）：与 `BATTLE_HUB_TOKEN` 同一条
#: 规矩——**只走 env**，argv 会出现在 `/proc/<pid>/cmdline` 上（全机可读）。空 = 不带。
HUB_LEASE_ENV = "BATTLE_HUB_LEASE"

#: 可读 worker 名（`kaggle-o`）传给**子进程**用的 env 名：同上，**只走 env**（argv 全机可读）。
#: 谁算的：装配点 `remote/plan_handoff.open_run_context`（`worker_tag(offline=True, hub_url=…)`）——
#: 本模块与 `deliver_proc` 都在 `job_lifecycle`(L3) **下面**，自己算就是向上依赖。
#: 空 = 不带（体里那个键整个缺席，不是空串：hub 把「带没带」当判据）。
WORKER_NAME_ENV = "NN_WORKER_NAME"

#: 段末三态（J2，`open`/`course`/`segment`）传给**子进程**用的 env 名。
#: 为什么另带一份 env（而不只走控制文件那位一行）：段末摘要会在子进程重启后**重述**
#: （`deliver_proc._ensure_alive`），而那时控制文件里那一行可能已被上一个子进程消费 ——
#: 重启 spawn 时把父侧记着的那份塞回 env，重启后的子进程才拿得到同一种段末。
#: 老快照子进程两个都不认 ⇒ hub 侧从包里的 `plan.json` 反推（`pack_seg_end_kind`）。
SEG_END_KIND_ENV = "NN_SEG_END_KIND"


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
__all__ = ["DELIVERED_NAME", "OfflineDeliverer", "main", "reset_rejected"]


def reset_rejected(
    artifacts_dir: str | Path,
    *,
    run_id: str = "",
    it: int | None = None,
    now: float | None = None,
    log=lambda _m: None,
) -> int:
    """把 `delivered.json` 里的**内容拒收**记录清掉（下一次 `sync()` 会再试这些轮次）。返回清掉的条数。

    为什么需要（D6 / plan §15）：拒收记录跨会话活着（按权重指纹认领）⇒ 一次**误判**（hub 侧的
    409 被读成内容问题、账本行取错）会让那一轮**永久不再回传**，而它的权重一直好好地躺在本地
    盘上。TTL（`REJECTED_TTL_SEC`）是最坏情况的自动出口，本函数是「现在就要它再试一次」的人工
    入口（控制台那个「重置该轮拒收」按钮应该走这里，不要各写一份）。

    两件事做全了才算真的生效：
      * 抹掉该 `it` 的记录行；
      * 在文件里记下**重置水位**（`it → 时刻`）——否则活着的补传子进程在下一次落盘时会把
        内存里那份原样写回来，运维看到的「清掉了」会在几分钟后自己复原。

    只改这份记账文件：不出网、不碰轮次目录、不抛（拒接/坏文件都只记一行）。``it=None`` = 全清。
    """
    p = Path(artifacts_dir) / OFFLINE_DELIVERED_NAME
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    if run_id and str(data.get("run_id", "")) != str(run_id):
        log(f"拒收重置跳过：{p} 是 run {data.get('run_id')!r} 的账本，不是 {run_id!r}")
        return 0
    rows = [r for r in (data.get("rejected") or []) if isinstance(r, dict)]
    keep = [r for r in rows if it is not None and r.get("it") != it]
    dropped = [r for r in rows if it is None or r.get("it") == it]
    if not dropped:
        return 0
    raw = data.get(REJECTED_RESET_KEY)
    markers = (
        {str(k): _num_or(v, 0.0) for k, v in raw.items()} if isinstance(raw, dict) else {}
    )
    at = float(time.time() if now is None else now)
    for r in dropped:
        markers[str(r.get("it"))] = at
    data["rejected"] = keep
    data[REJECTED_RESET_KEY] = _prune_reset_markers(markers, now=at)
    try:
        atomic_write_json(p, data)
    except OSError as e:
        log(f"拒收重置失败：{p} 写不进去（{e}）")
        return 0
    which = "全部" if it is None else f"it{it}"
    log(f"已重置 {len(dropped)} 轮的内容拒收（{which}）——下一次补传会重新试它们｜账本 {p}")
    return len(dropped)


def main(argv: list[str] | None = None) -> int:
    """`python -m remote.offline_deliver --artifacts <产物根> [--it N]`：重置内容拒收（D6）。

    为什么不给「重置」单开一个脚本：它改的就是本模块的记账文件（`delivered.json`），路径、
    权重指纹、水位口径都在这里 —— 另开一份就是把同一语义再实现一遍（plan §19 的收口纪律）。
    """
    ap = house_parser(description="产物补传腿：重置内容拒收记录（让这些轮次再试一次）")
    ap.add_argument("--artifacts", required=True, help="产物根目录（`delivered.json` 住在这里）")
    ap.add_argument("--run-id", default="", help="核对账本归属（给错目录时不改任何东西）")
    ap.add_argument("--it", type=int, default=None, help="只重置这一轮（不给 = 全清）")
    ap.add_argument("--json-only", action="store_true", help="只打机器可读那一行")
    args = ap.parse_args(argv)
    log = (lambda _m: None) if args.json_only else (lambda m: print(m, flush=True))
    n = reset_rejected(args.artifacts, run_id=args.run_id, it=args.it, log=log)
    print(json.dumps({"reset": n, "it": args.it}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    run_main(lambda: main())
