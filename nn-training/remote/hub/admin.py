"""remote/hub/admin.py —— HubHandler 的 **admin 控制面**（2026-09-23 S4 第三步，从 `remote/hub_server.py` 拆出）。

运维/控制面路由：云端停机与恢复 · 课程表热切 · 队列与状态快照 · GPU 推送 worker 清单 ·
net-probe（隧道 A/B 的确定性载荷）。它们与数据面（claim / result / blob / task-pack …）
职责分明，却和 `HubHandler` 其余 40 个方法挤在同一个 1343 行的类里。

## 依赖方向：混入（调用者依赖被调用者）

`remote/hub_server.py` 里是 `class HubHandler(AdminRoutes, BaseHTTPRequestHandler)` —— 这一组
路由由 `do_GET` / `do_POST` 的派发表调用，所以「组合类依赖混入」就是这个方向。本模块是**混入**：
运行时状态（`self.hub` / `self.push`）与 4 个通用助手（`_auth_ok` / `_bytes` / `_json` /
`_query_course`）由**组合类**提供，在此只声明类型供 mypy/阅读（混入的常态，同
`rl/loop_remote.py` 与 `rl/loop_steps.py` 的约定）。

## 为什么 net-probe 的两个支撑名字**随本组一起搬**

`NET_PROBE_MAX` 与 `_deterministic_fill` 原先定义在 `hub_server.py` 顶层，且**只被本组使用**
（全仓无其它读者，已 grep 证实）。若把它们留在 `hub_server` 而由本模块 import，就会与
「`hub_server` import `admin` 拿混入」构成**双向环** ⇒ 必须随迁。搬来后**无需门面**
re-export（没有别的读者）。

## 为什么这一组是 `hub_server` 里最安全的第一刀（plan §5.3.2 实测）

① `HubHandler` 只有 3 个类属性（`hub` / `push` / `_blocked_logged`）⇒ 本组方法近乎无状态，
搬迁不改语义；② 本组只往外调 4 个通用助手，反向只有派发表调用；③ **测试接缝为零**——全仓对
`remote.hub_server` 的 patch 只有一处（`SEND_TIMEOUT_SEC`，不在本组），`tests/` 从它取的名字
全是 `_JobStore` / `_HubQueue` / `as_hub` / `make_server`，本组一个都没被外部 import。
"""

from __future__ import annotations

import json
import random
import time
import urllib.parse
from email.message import Message
from io import BufferedIOBase
from typing import Any

from common.protocol import COURSE_MODES, ProtocolError

# ------------------------------------------------------------------ net-probe（M0）

#: /admin/net-probe 响应体上限（与前端探针脚本约定；2MB 腿只需 2_097_152）。
NET_PROBE_MAX = 16 * 1024 * 1024
#: 确定性填充块（固定种子，绝不用随机——同一 bytes=N 每次必须逐字节相同，
#: 这样隧道 A/B 的差异只可能来自协议，不可能来自载荷）。
_PROBE_BLOCK = bytes(random.Random(0x5EED).getrandbits(8) for _ in range(65536))


def _deterministic_fill(n: int) -> bytes:
    """生成 n 字节确定性填充（重复 64KiB 固定块，省 CPU）。"""
    q, rem = divmod(n, len(_PROBE_BLOCK))
    return _PROBE_BLOCK * q + _PROBE_BLOCK[:rem]


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

    def _admin_offline(self) -> None:
        """`GET /admin/offline`：已收到的离线段进度（只读，逐课程 × 逐 run）。

        段内进度**只能**从补传产物看出来（hub 不跑那几轮，课程账本里没有它们的行），
        而控制台要在长段期间回答「它在跑还是挂了」——唯一能回答的就是最近一轮的时间戳。
        """
        if not self._auth_ok():
            return
        self._json(
            {"progress": self.hub.offline_progress(), "leases": self.hub.offline_leases()}, 200
        )

    def _admin_courses(self, set_mode: bool = False) -> None:
        """`GET /admin/courses` 看课程表；`POST ?course=X&mode=online|offline` 热切。

        volatile（与 halt 同性质，重启回启动参数）——运维需要一个能当场把一门课
        改派为离线的闸（例如某课的云机报销了，先不派活只收回传）。
        """
        if not self._auth_ok():
            return
        if not set_mode:
            self._json(
                {
                    "courses": [
                        {"course": c, "mode": self.hub.mode_of(c)} for c in self.hub.courses()
                    ]
                },
                200,
            )
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        course = (qs.get("course") or [""])[0]
        mode = (qs.get("mode") or [""])[0]
        # 课程未知 ⇒ **按需真扫一次再试**（2026-09-23）：`set_mode` 只认已登记的课程，
        # 而登记依赖顺带扫描（`claim_next`/`queue_state` 触发、有 2s 间隔闸）。于是
        # 「刚开课 / hub 刚重启」那一刻打来的 mode POST 必然 400——控制台那侧的重试窗口
        # 一旦整段落在发现之前，意图就静默失配（课留在 online，面板显示「在训/切离线」，
        # 用户实测：三个离线课里恰有一个如此）。指名一门课的写动作有资格要求一次真扫。
        # 只在「课不在表里」时扫（模式非法就不必扫盘了，直接落到下面 400）。
        if not self.hub.set_mode(course, mode) and course and course not in self.hub.courses():
            self.hub.discover(force=True)
            self.hub.set_mode(course, mode)
        if not self.hub.set_mode(course, mode):
            self._json(
                {
                    "error": f"需要合法 course（{self.hub.courses()}）与 mode（{list(COURSE_MODES)}）",
                    "course": course,
                    "mode": mode,
                },
                400,
            )
            return
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] course={course or '-'} mode -> {mode}",
            flush=True,
        )
        self._json({"course": course, "mode": self.hub.mode_of(course)}, 200)

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
