"""hub/admin.py —— HubHandler 的 **admin 控制面**（2026-09-23 S4 第三步，从 `hub/server.py` 拆出）。

运维/控制面路由：云端停机与恢复 · 课程表热切 · 队列与状态快照 · GPU 推送 worker 清单 ·
net-probe（隧道 A/B 的确定性载荷）· 采样节点自动注册（`/admin/nodes*`，写 rl-config `nodes[]`）。它们与数据面（claim / result / blob / task-pack …）
职责分明，却和 `HubHandler` 其余 40 个方法挤在同一个 1343 行的类里。

## 依赖方向：混入（调用者依赖被调用者）

`hub/server.py` 里是 `class HubHandler(AdminRoutes, BaseHTTPRequestHandler)` —— 这一组
路由由 `do_GET` / `do_POST` 的派发表调用，所以「组合类依赖混入」就是这个方向。本模块是**混入**：
运行时状态（`self.hub` / `self.push`）与 4 个通用助手（`_auth_ok` / `_bytes` / `_json` /
`_query_course`）由**组合类**提供，在此只声明类型供 mypy/阅读（混入的常态，同
`trainer/loop_remote.py` 与 `trainer/loop_steps.py` 的约定）。

## 为什么 net-probe 的两个支撑名字**随本组一起搬**

`NET_PROBE_MAX` 与 `_deterministic_fill` 原先定义在 `hub_server.py` 顶层，且**只被本组使用**
（全仓无其它读者，已 grep 证实）。若把它们留在 `hub_server` 而由本模块 import，就会与
「`hub_server` import `admin` 拿混入」构成**双向环** ⇒ 必须随迁。搬来后**无需门面**
re-export（没有别的读者）。

## 为什么这一组是 `hub_server` 里最安全的第一刀（plan §5.3.2 实测）

① `HubHandler` 只有 3 个类属性（`hub` / `push` / `_blocked_logged`）⇒ 本组方法近乎无状态，
搬迁不改语义；② 本组只往外调 4 个通用助手，反向只有派发表调用；③ **测试接缝为零**——全仓对
`hub.server` 的 patch 只有一处（`SEND_TIMEOUT_SEC`，不在本组），`tests/` 从它取的名字
全是 `_JobStore` / `_HubQueue` / `as_hub` / `make_server`，本组一个都没被外部 import。
"""

from __future__ import annotations

import json
import random
import re
import time
import urllib.parse
from email.message import Message
from io import BufferedIOBase
from pathlib import Path
from typing import Any

from common import distribution
from common.fs import atomic_write_json
from common.protocol import ProtocolError

# ------------------------------------------------------------------ net-probe（M0）

#: /admin/net-probe 响应体上限（与前端探针脚本约定；2MB 腿只需 2_097_152）。
NET_PROBE_MAX = 16 * 1024 * 1024
#: /admin/worker-prefetch 请求体上限（plan/dashboard-ppo-live-rows）：体里只有 worker id 与
#: 两组 jid 列表（16 字节 × 预取深度）—— 64KB 已宽裕两个数量级，超限 413 而不是读进内存。
WORKER_PREFETCH_BODY_MAX = 64 * 1024
#: 确定性填充块（固定种子，绝不用随机——同一 bytes=N 每次必须逐字节相同，
#: 这样隧道 A/B 的差异只可能来自协议，不可能来自载荷）。
#:
#: 2026-09-29（§48）：**首次使用时才建**。原来在模块顶层跑那 65536 次
#: `Random.getrandbits(8)`，实测 **0.53s/进程**；而 `hub.admin` 被
#: `hub_server → hub.boot → http_face → admin` 这条链拖进**每个 import hub 的进程**
#: （真 hub 进程、每个 rollout worker、以及 12 个 pytest worker）⇒ 白付 0.53s×N。
#: 构造只服务 `/admin/net-probe`（`_deterministic_fill`），延迟不改任何语义；
#: 内容逐字节不变（同一种子、同一生成式、同一长度）。
_PROBE_BLOCK: bytes | None = None


def _probe_block() -> bytes:
    """取（惰性构造的）64KiB 确定性填充块。"""
    global _PROBE_BLOCK
    if _PROBE_BLOCK is None:
        _PROBE_BLOCK = bytes(random.Random(0x5EED).getrandbits(8) for _ in range(65536))
    return _PROBE_BLOCK


def _deterministic_fill(n: int) -> bytes:
    """生成 n 字节确定性填充（重复 64KiB 固定块，省 CPU）。"""
    block = _probe_block()
    q, rem = divmod(n, len(block))
    return block * q + block[:rem]


# ---------------------------------------------------------- 采样节点自动注册（2026-10-10）
#
# plan/rollout-node-auto-register v2：云机（采样节点）自报 {id,url,authKey} 到 hub，hub 是
# rl-config.json 的**第二作者**（控制台之外唯一持久写者）。约定（S3 用例钉住）：
#   * 只动 `nodes[]` 一项 + 原子写 + 无变化不写盘；
#   * `enabled` 归控制台：新条目 true；既有 managed 条目只在「上一轮干净收工」（带
#     `unregistered_at`）时恢复 true —— 云机**不顶**控制台在会话进行中的停用；
#   * `concurrency` 只在**新条目**且 body 给值时才写（F2：缺省不写键 = 派发按 `ping.cpus`）；
#   * ping 门：`common.distribution.node_ping` 不通 ⇒ 422 不写盘（`skipPing=true` 可绕过，日志响亮）。

#: 节点 id 合法域（与 `dashboard/src/web/view/course-overview.ts::validWorkerId` 同域：
#: 1-40 位 `[A-Za-z0-9._-]`；它会进日志与 rl-config，含空格会让「谁在跑」读不出来）。
_NODE_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,40}")
#: label（env-link 口径，如 `colab-t`）：同字符集，放宽到 64。
_NODE_LABEL_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
#: 自报并发槽位的合法域（按核数自报；上限留足余量，挡住手抖的 99999）。
_NODE_CONC_MAX = 1024


def _nodes_config_path() -> Path:
    """rl-config 路径的唯一来源（env `BCITY_RL_CONFIG` > `nn-training/rl-config.json`）。"""
    return Path(distribution.rl_config_path())


def _read_rl_config(path: Path) -> dict | None:
    """读整份 rl-config（utf-8-sig 容忍 BOM，与 `load_dist_config` 同口径）；读不到 ⇒ None。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _merge_managed_node(prev: dict | None, payload: dict) -> dict:
    """register 的 upsert 合并（F1 语义，唯一权威）。

    * 新条目：`enabled=True`（此后归控制台）；
    * 既有 managed：只改 `url`/`authKey`/`label`；`concurrency` **永不改**（控制台手工值不被顶）；
      `enabled` 只在上一轮干净收工（带 `unregistered_at`）时恢复 True，否则原样保留
      （会话进行中的控制台停用不被云机顶掉）。
    """
    if prev is None:
        node: dict = {
            "id": payload["id"],
            "url": payload["url"],
            "authKey": payload["authKey"],
            "managed": True,
            "enabled": True,
        }
        if "concurrency" in payload:
            node["concurrency"] = payload["concurrency"]
        if "label" in payload:
            node["label"] = payload["label"]
        return node
    node = dict(prev)
    node["url"] = payload["url"]
    node["authKey"] = payload["authKey"]
    if "label" in payload:
        node["label"] = payload["label"]
    if node.pop("unregistered_at", None) is not None:
        node["enabled"] = True  # 上轮干净收工 ⇒ 本轮回来即恢复启用
    node["managed"] = True
    return node


class AdminRoutes:
    """admin 控制面路由 mixin（`HubHandler(AdminRoutes, BaseHTTPRequestHandler)`）。"""

    # 由组合类提供的运行时状态与通用助手（声明类型供 mypy/阅读，实际赋值/实现在 HubHandler）。
    hub: Any
    push: Any
    # `BaseHTTPRequestHandler` / `StreamRequestHandler` 的属性——混入不继承它们，故需自行声明
    # （否则 mypy 报 attr-defined）。
    # ⚠ 类型必须与 typeshed **逐字一致**（`headers: email.message.Message`、
    # `rfile: BufferedIOBase`、`path: str`）：本混入在 MRO 里**早于** `BaseHTTPRequestHandler`，
    # 声明成 `Any` 会把组合类里 `self.headers.get(...)` / `self.rfile.read(n)` 的推断拓成 Any，
    # 进而让 `hub_server` 里做 `-> str` / `-> bytes | None` 的方法报 no-any-return。
    headers: Message
    path: str
    rfile: BufferedIOBase
    _auth_ok: Any
    _bytes: Any
    _json: Any
    _query_course: Any
    _read_raw_body: Any
    client_address: tuple[str, int]

    # ---- 云端停机 / 恢复（§386：停机=发"停机命令"随任务同发；云机先试停机停不掉照常干活） ----
    # 用法：console 在 TrainingLoop 死亡/设计内停车时 GET /admin/workers/halt 置停机态，
    # 停机条件消失（恢复训练）GET /admin/workers/resume。仅 Bearer 鉴权（同 worker），volatile。
    def _admin_halt(self, halt: bool, course: str = "") -> None:
        """置/解停机达令。`?course=X` = 只动那一门课；无课程 = 全课程（旧语义）。

        返回体：无课程时**恒为 `{"halt": ...}`**（既有用例与 console 读它）；带课程时
        追加 `course` 字段。未知课程 → 400（不猜、不静默改写全局）。
        """
        if not self._auth_ok():
            return
        if not self.hub.set_halt(halt, course):
            self._json(
                {"error": f"未知 course（本 hub 的课程：{self.hub.courses()}）", "course": course},
                400,
            )
            return
        body: dict = {"halt": self.hub.halt_of(course) if course else self.hub.all_halted()}
        if course:
            body["course"] = course
        self._json(body, 200)

    def _admin_status(self) -> None:
        # 体形状不动（console 的 set_cloud_halt / clear_halt_on_startup 读它）：只回停机态，
        # 不往这里叠字段。
        # `?course=` = 只看那一门课的停机态（多课程下「全局」几乎没有信息量）。
        if not self._auth_ok():
            return
        course = self._query_course()
        halt = self.hub.halt_of(course) if course else self.hub.all_halted()
        self._json({"halt": halt}, 200)

    # ---- 多课程观测面（2026-09-18）：/admin/queue + /admin/courses ----
    def _admin_queue(self) -> None:
        """`GET /admin/queue`：每课程队列深度/在飞/心跳 + 竞速两个判据数（只读）。

        为什么单开一个面：多课程下「为什么某门课在饿着」只能靠它回答（轮转游标、
        避让记录、离线标记都在里面），而 `/admin/status` 的体形状被 console 读着，
        不能往里叠字段。
        """
        if not self._auth_ok():
            return
        self._json(self.hub.queue_state(), 200)

    def _post_worker_prefetch(self) -> None:
        """`POST /admin/worker-prefetch`：worker 上报**软持有预取状态**（纯观测）。

        body：`{"worker": "<id>", "held": ["<jid>"…], "dl": ["<jid>"…]}`。
        读面在 `GET /admin/queue` 的 `worker_prefetch`（同一次 TTL 过滤 + jid→(course, it) 解析）。

        三条口径：
          · **缺 `worker` ⇒ 400**：无归属的上报没有可渲染的行，与其记成占位名不如响亮拒绝；
          · 体上限 `WORKER_PREFETCH_BODY_MAX` ⇒ 413（不把任意大的体读进内存）；
          · 鉴权用与 job 面**同一枚** token（worker 手里本来就有），失败即丢——调用方
            best-effort 不重试，否则配错 token 的盘会被自己的观测腿喂满鉴权计数（5 次封 3600s）。
        """
        if not self._auth_ok():
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "Content-Length 非法"}, 400)
            return
        if n < 0 or n > WORKER_PREFETCH_BODY_MAX:
            self._json({"error": f"请求体越界（0..{WORKER_PREFETCH_BODY_MAX}），收到 {n}"}, 413)
            return
        try:
            raw = self.rfile.read(n)
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, OSError) as e:
            self._json({"error": f"bad json: {e}"}, 400)
            return
        if not isinstance(body, dict):
            self._json({"error": "体必须是对象"}, 400)
            return
        worker = str(body.get("worker") or "").strip()
        if not worker:
            self._json({"error": "需要 worker=<worker_id>"}, 400)
            return
        self.hub.note_worker_prefetch(worker, body.get("held"), body.get("dl"))
        self._json({"ok": True, "worker": worker}, 200)

    def _admin_offline(self) -> None:
        """`GET /admin/offline`：已收到的离线段进度（只读，逐课程 × 逐 run）。

        段内进度**只能**从补传产物看出来（hub 不跑那几轮，课程账本里没有它们的行），
        而控制台要在长段期间回答「它在跑还是挂了」——唯一能回答的就是最近一轮的时间戳。
        """
        if not self._auth_ok():
            return
        self._json(
            {
                "progress": self.hub.offline_progress(),
                "leases": self.hub.offline_leases(),
                # 段末摘要（T6 / §3.6）：控制台按 `run_id` 对齐导入产物，把 `end_it_reached`
                # 转交给 python 导入器落 `run_complete`（灰横幅）——半段导入不得亮横幅。
                "results": self.hub.offline_results(),
                # 停滞告警面（§3.9 / T8）：`running` 无进度、或已翻 offline 无人跑。
                # 自动交接的固有代价 —— 必须显式付（不靠「人总会看到」）。
                "stalled": self.hub.offline_stalled(),
            },
            200,
        )

    def _admin_courses(self, post: bool = False) -> None:
        """`GET /admin/courses` 看课程表；`POST ?course=X&release_hold=1` 强制解除接管。

        ★M4b：模式热切（`?mode=&pin=&drop_jobs=`）**已退役** —— 课程不再区分在线/离线，
        接管（hold）才是「谁在跑这门课」的唯一真源。带 `mode=` 的 POST 响亮 400
        （旧控制台/老脚本会看到「模式已退役」而不是静默失败）。
        """
        if not self._auth_ok():
            return
        if not post:
            self._json(
                {
                    "courses": [
                        {
                            "course": c,
                            # 「在训」= 开课标记在（唯一 opt-out = 停课）。
                            "training": self.hub.course_open(c),
                            # ★M1b：接管读数（与 `/admin/queue` 同一口径；`token` 不外露）
                            "held": str(self.hub.hold_of(c).get("state") or "") == "live",
                            "holder": str(self.hub.hold_of(c).get("worker_id") or ""),
                            "pending_export": bool(self.hub.pending_export_of(c)),
                            # 启动参数里带过模式段（`--course a=offline`；P1-3 的兼容标记）。
                            "mode_ignored": self.hub.mode_ignored(c),
                        }
                        for c in self.hub.courses()
                    ]
                },
                200,
            )
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        course = (qs.get("course") or [""])[0]
        # ★M1b：`release_hold=1` = **强制解除接管**（立墓碑 ⇒ 下一次 claim 直接覆盖）。
        # 为什么走 revoke 而不是「直接清 hold」：墓碑让现场看得见「有人把它踢下来了」
        # （`holder_info` 照返 tombstone 形状），而不是一个凭空消失的 owner。
        raw_release = (qs.get("release_hold") or [""])[0].strip().lower()
        if raw_release in ("1", "true", "yes", "on"):
            ok = self.hub.revoke_offline_lease(course, "admin release_hold=1")
            self._json({"course": course, "released": ok}, 200 if ok else 409)
            return
        # ★M4b：模式的三个旧参数（mode / pin / drop_jobs）反而是**退役信号**。
        if (qs.get("mode") or [""])[0] or "pin" in qs or "drop_jobs" in qs:
            self._json(
                {
                    "error": (
                        "课程模式已退役（课程不再区分在线/离线，接管 hold 才是唯一真源）"
                        "——要解除接管请用 release_hold=1"
                    ),
                    "course": course,
                },
                400,
            )
            return
        self._json(
            {
                "error": "未知动作：POST /admin/courses 只认 release_hold=1（模式已退役）",
                "course": course,
            },
            400,
        )

    def _admin_unfreeze(self) -> None:
        """`POST /admin/unfreeze?job_id=<jid>`：人工解冻一个被熔断（§4.1）的 job。

        为什么必须有这个口：熔断的价值在于「停下来问人」，那“人”就得有个能做事的把手；
        没有它，冻结就是不可逆死亡（与「重发不清冻结」合起来看更明显：重发不清、又无
        解冻口 ⇒ 那份 job 永远烂在列表里）。解冻即回池（清计数），下一次重领从头计数。

        job 不明 / 不属于本 hub → 404（响亮，不静默造一个无归属状态）；本来就未冻结 →
        409（“没冻可解”要说出来，否则操作员会以为解冻失败）。
        """
        if not self._auth_ok():
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        jid = (qs.get("job_id") or qs.get("job") or [""])[0].strip()
        if not jid:
            self._json({"error": "需要 job_id=<jid>"}, 400)
            return
        if not (self.hub._job_dir(jid) / "manifest.json").exists():
            self._json({"error": f"未知 job {jid}"}, 404)
            return
        info = self.hub.unfreeze(jid)
        if info is None:
            self._json({"job_id": jid, "unfrozen": False, "error": "该 job 未被冻结"}, 409)
            return
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] 人工解冻：job={jid} "
            f"（冻于 {info.get('reclaims')} 次零回传后）——已回池可重领",
            flush=True,
        )
        self._json(
            {"job_id": jid, "unfrozen": True, "reclaims": info.get("reclaims", 0)}, 200
        )

    # ---- push worker 登记表（2026-09-18；控制台 worker 登记入口的服务面）----
    def _admin_push_workers(self, set_action: bool = False) -> None:
        """`GET /admin/push-workers` 看登记表 + 派发器状态；`POST` 增/删/热重载。

        控制台登记 worker 的正常路径是**回写 rl-config**（hub 按 mtime 热重载，写完不必
        重启）；本端点另开两个理由：① 写完配置要 hub **立刻**拾取（不等下一拍）；②
        冒烟/排障时临时挂一台（volatile，重启即回到配置）。

        body（JSON）：`{"action": "add"|"remove"|"reload", id?, url?, authKey?, concurrency?}`。
        未启用 push 派发（无 `--push`）时 409 —— 响亮好过默默什么都没发生。
        """
        if not self._auth_ok():
            return
        disp = self.push
        if disp is None:
            self._json({"error": "push 派发未启用（hub-server 需 --push）"}, 409)
            return
        if not set_action:
            self._json({"dispatcher": disp.state(), "registry": disp.workers.state()}, 200)
            return
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, OSError) as e:
            self._json({"error": f"bad json: {e}"}, 400)
            return
        if not isinstance(body, dict):
            self._json({"error": "体必须是对象"}, 400)
            return
        action = str(body.get("action") or "reload")
        if action == "reload":
            changed = disp.workers.reload(force=True)
            self._json(
                {"action": action, "changed": changed, "workers": disp.workers.snapshot()}, 200
            )
            return
        if action == "add":
            try:
                w = disp.workers.add(body)
            except ProtocolError as e:
                self._json({"error": str(e)}, 400)
                return
            print(
                f"[{time.strftime('%H:%M:%S')}] [hub-server] push worker 登记（运行时）："
                f"{w['id']} -> {w['url']}",
                flush=True,
            )
            self._json({"action": action, "worker": w}, 200)
            return
        if action == "remove":
            wid = str(body.get("id") or "")
            if not wid:
                self._json({"error": "remove 需要 id"}, 400)
                return
            hit = disp.workers.remove(wid)
            self._json({"action": action, "id": wid, "removed": hit}, 200)
            return
        self._json({"error": f"未知 action {action!r}（add|remove|reload）"}, 400)

    # ---- GET /admin/net-probe?bytes=N（M0：隧道吞吐 A/B 探针）----
    def _admin_net_probe(self) -> None:
        """回 N 字节确定性填充（固定种子）——同一条隧道双向各传 2MB 量真实吞吐。

        ⚠ 鉴权：必须携正确 Bearer token（同其余端点），**绝不能在循环里重试错误
        token**（D9：同 IP 连败 5 次封 3600s，而 cloudflared 回源会把隧道流量全归
        成 127.0.0.1 ⇒ 误伤本机组件）。探针脚本只在自身自检时打一次，见验收 harness。
        """
        if not self._auth_ok():
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        raw = (qs.get("bytes") or ["0"])[0]
        try:
            n = int(raw)
        except ValueError:
            self._json({"error": f"bytes 必须是整数，收到 {raw!r}"}, 400)
            return
        if n < 0 or n > NET_PROBE_MAX:
            self._json({"error": f"bytes 越界（0..{NET_PROBE_MAX}），收到 {n}"}, 400)
            return
        self._bytes(_deterministic_fill(n))

    def _admin_net_probe_upload(self) -> None:
        """POST /admin/net-probe —— 读掉请求体并回 {"bytes": n}（上行方向的腿）。

        为什么需要：push 模式的真实流量里**上行是大头**（job 体），只量下行会把
        A/B 的结论押在次要方向上。体上限用同一 NET_PROBE_MAX，超限 413 而不是把
        N GB 读进内存（探针也会被误用）。
        """
        if not self._auth_ok():
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "Content-Length 非法"}, 400)
            return
        if n < 0 or n > NET_PROBE_MAX:
            self._json({"error": f"请求体越界（0..{NET_PROBE_MAX}），收到 {n}"}, 413)
            return
        got = 0
        while got < n:  # 分块读掉，绝不整体入内存（探针不是文件接收器）
            chunk = self.rfile.read(min(65536, n - got))
            if not chunk:
                break
            got += len(chunk)
        if got != n:
            self._json({"error": f"请求体截断（声明 {n}，实收 {got}）"}, 400)
            return
        self._json({"bytes": got})

    # ---- 采样节点自动注册（2026-10-10；plan/rollout-node-auto-register v2）----

    def _admin_nodes_list(self) -> None:
        """GET /admin/nodes —— managed 条目清单（**不回 authKey**：够排障、不多泄露）。

        `last_seen` 有意不在读面里（评审 F4：无合法落点；台账看 hub 日志 + 条目自带的
        `unregistered_at`）。
        """
        if not self._auth_ok():
            return
        cfg = _read_rl_config(_nodes_config_path())
        nodes: list[dict] = []
        if cfg is not None and isinstance(cfg.get("nodes"), list):
            keep = ("id", "url", "label", "enabled", "concurrency", "unregistered_at")
            nodes = [
                {k: n[k] for k in keep if k in n}
                for n in cfg["nodes"]
                if isinstance(n, dict) and n.get("managed")
            ]
        self._json({"nodes": nodes, "count": len(nodes)}, 200)

    def _admin_nodes_register(self) -> None:
        """POST /admin/nodes/register —— 云机自报 upsert（语义见本文件上方「采样节点自动注册」块）。"""
        if not self._auth_ok():
            return
        raw = self._read_raw_body()
        if raw is None:
            return
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError as e:
            self._json({"error": f"bad json: {e}"}, 400)
            return
        if not isinstance(body, dict):
            self._json({"error": "体必须是对象"}, 400)
            return
        nid = str(body.get("id") or "").strip()
        if not _NODE_ID_RE.fullmatch(nid):
            self._json({"error": f"id 非法: {nid!r}（只接受 1-40 位字母/数字/._-）"}, 400)
            return
        url = str(body.get("url") or "").strip().rstrip("/")
        if not (url.startswith("http://") or url.startswith("https://")):
            self._json({"error": f"url 必须是 http(s):// 完整地址，收到 {body.get('url')!r}"}, 400)
            return
        auth_key = str(body.get("authKey") or "").strip()
        if not auth_key:
            self._json({"error": "authKey 不能为空（节点 tools/agent/agent.auth 的内容）"}, 400)
            return
        payload: dict = {"id": nid, "url": url, "authKey": auth_key}
        if body.get("concurrency") is not None:
            conc = body.get("concurrency")
            if isinstance(conc, bool) or not isinstance(conc, int) or not 1 <= conc <= _NODE_CONC_MAX:
                self._json(
                    {"error": f"concurrency 需为 1-{_NODE_CONC_MAX} 的整数，收到 {conc!r}"}, 400
                )
                return
            payload["concurrency"] = conc
        if body.get("label"):
            label = str(body["label"]).strip()
            if not _NODE_LABEL_RE.fullmatch(label):
                self._json({"error": f"label 非法: {label!r}（1-64 位 [A-Za-z0-9._-]）"}, 400)
                return
            payload["label"] = label

        path = _nodes_config_path()
        cfg = _read_rl_config(path)
        if cfg is None:
            self._json({"error": f"rl-config.json 读不到或不是对象: {path}"}, 409)
            return
        raw_nodes = cfg.get("nodes")
        nodes: list[Any] = list(raw_nodes) if isinstance(raw_nodes, list) else []
        idx = next(
            (i for i, n in enumerate(nodes) if isinstance(n, dict) and n.get("id") == nid),
            -1,
        )
        prev = nodes[idx] if idx >= 0 else None
        if prev is not None and not prev.get("managed"):
            self._json(
                {"error": f"id {nid} 已被本机表节点占用（非 managed）——云机不许覆盖：换 id 或先移除该条目"},
                409,
            )
            return
        if body.get("skipPing") is True:
            print(
                f"[{time.strftime('%H:%M:%S')}] [hub-server] 节点登记 skipPing=1 id={nid} url={url}"
                "（跳过 ping 门——排障路径，节点可能不可达）",
                flush=True,
            )
        else:
            ping = distribution.node_ping(url, auth_key)
            if ping is None:
                self._json(
                    {"error": f"节点 ping 不通（{url}/v1/ping 无应答，或 authKey 不符）——不写盘"},
                    422,
                )
                return
        merged = _merge_managed_node(prev, payload)
        if prev is not None and merged == prev:
            self._json({"ok": True, "id": nid, "action": "unchanged"}, 200)
            return
        new_nodes = list(nodes)
        if idx >= 0:
            new_nodes[idx] = merged
        else:
            new_nodes.append(merged)
        atomic_write_json(path, {**cfg, "nodes": new_nodes}, indent=2)
        action = "created" if idx < 0 else "updated"
        restored = prev is not None and "unregistered_at" in prev
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] 节点登记 id={nid} url={url} action={action}"
            + ("（上轮干净收工 ⇒ 恢复 enabled=true）" if restored else "")
            + f"（来源 {self.client_address[0]}）",
            flush=True,
        )
        self._json({"ok": True, "id": nid, "action": action}, 200)

    def _admin_nodes_unregister(self) -> None:
        """POST /admin/nodes/unregister —— 会话收工（enabled=false + unregistered_at，**不删**）。"""
        if not self._auth_ok():
            return
        raw = self._read_raw_body()
        if raw is None:
            return
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError as e:
            self._json({"error": f"bad json: {e}"}, 400)
            return
        if not isinstance(body, dict):
            self._json({"error": "体必须是对象"}, 400)
            return
        nid = str(body.get("id") or "").strip()
        if not nid:
            self._json({"error": "unregister 需要 id"}, 400)
            return
        path = _nodes_config_path()
        cfg = _read_rl_config(path)
        if cfg is None:
            self._json({"error": f"rl-config.json 读不到或不是对象: {path}"}, 409)
            return
        raw_nodes = cfg.get("nodes")
        nodes: list[Any] = list(raw_nodes) if isinstance(raw_nodes, list) else []
        idx = next(
            (
                i
                for i, n in enumerate(nodes)
                if isinstance(n, dict) and n.get("id") == nid and n.get("managed")
            ),
            -1,
        )
        if idx < 0:
            self._json({"ok": False, "id": nid, "error": "没有该 managed 条目"}, 404)
            return
        prev = nodes[idx]
        merged = {**prev, "enabled": False, "unregistered_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if merged == prev:
            self._json({"ok": True, "id": nid, "action": "unchanged"}, 200)
            return
        new_nodes = list(nodes)
        new_nodes[idx] = merged
        atomic_write_json(path, {**cfg, "nodes": new_nodes}, indent=2)
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] 节点收工 id={nid}（enabled=false，"
            f"下个会话注册时恢复）（来源 {self.client_address[0]}）",
            flush=True,
        )
        self._json({"ok": True, "id": nid, "action": "unregistered"}, 200)
