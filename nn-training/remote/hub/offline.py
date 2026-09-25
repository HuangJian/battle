"""remote/hub/offline.py —— 离线段面（2026-09-24 S4 第十一刀）。

全离线 / 半离线段与 hub 之间的**最佳努力**通道（训练永不因网络停摆）：

    GET  /offline/task-pack         整段任务包 `task-<课>.zip`（云机连上 hub 就先取包）
    GET  /offline/resume            续跑锚点元信息（`resume: null` 是**正常应答**，不是 404）
    GET  /offline/resume/blob       锚点轮次的字节（三道门：鉴权 → 名字白名单 → 就是当前锚点）
    POST /offline/artifact          逐轮产物补传（按 (run_id, it) 幂等、首写锁定）
    POST /offline/result            段末摘要（覆盖写：它是「这条腿到哪了」的最新答案）

依赖只到 `common.protocol` ⇒ 秩 **0**。补传的**归属判定**（一 hub 多课程）与落盘/首写锁定
都在调度面（`_HubQueue.locate_offline_course` / `store_offline_*`），本模块只管 HTTP 形状。
"""

from __future__ import annotations

import json
import time
import urllib.parse
from email.message import Message
from io import BufferedIOBase
from pathlib import Path
from typing import Any

from common.protocol import (
    COURSE_ENABLE_MARKER,
    COURSE_MODE_OFFLINE,
    INIT_WEIGHTS_NAME,
    OFFLINE_ARTIFACT_BODY_MAX,
    OFFLINE_QUEUE_VERSION,
    OFFLINE_RESULT_BODY_MAX,
    OFFLINE_RESUME_BLOB_NAMES,
    ProtocolError,
)

# 任务包新鲜度门 / 缺包自愈门 / 清单读数的纯判据与模块状态都住 `hub/task_pack.py`（叶子）——
# 它有两个读者（本模块的取包端点 + `queue_offline` 的清单），所以**不**挂在本模块上（否则
# 队列那一侧要向上 import，账本当场报反向边）。本模块因此从 L0 升为 L1。
from remote.hub.store import _JobStore
from remote.hub.task_pack import (
    _TASK_PACK_LOCK,
    _TASK_PACK_MISS_TRIGGERS,
    _TASK_PACK_TRIGGERS,
    TASK_PACK_MISS_TRIGGER_LIMIT,
    TASK_PACK_STALE_THROTTLE_SEC,
    TASK_PACK_STALE_TRIGGER_LIMIT,
    _file_sha256,
    _hub_log,
    decide_task_pack,
    pack_index_part_sha,
    reset_task_pack_miss_triggers,
    reset_task_pack_triggers,
    task_pack_stale_reason,
    trigger_task_bundle_export,
)


class OfflineRoutes:
    """offline 路由 mixin（`HubHandler(…, OfflineRoutes, BaseHTTPRequestHandler)`）。"""

    # 由组合类（HubHandler）提供的运行时状态与通用助手——混入不继承 BaseHTTPRequestHandler，
    # 故需自行声明类型（否则 mypy 报 attr-defined）。⚠ 类型必须与 typeshed 逐字一致
    # （`headers: email.message.Message` / `rfile: BufferedIOBase` / `path: str`）：本混入在 MRO 里
    # 早于 BaseHTTPRequestHandler，声明成 Any 会把组合类里 `self.headers.get(...)` 的推断拓成 Any。
    hub: Any
    headers: Message
    path: str
    rfile: BufferedIOBase

    # 由组合类提供（实现都在 `remote/hub/http_face.py::HubHandler`）——混入只声明类型。
    _auth_ok: Any
    _bytes: Any
    _json: Any
    _log_reject: Any
    _query_course: Any
    _read_capped_body: Any
    _worker_id: Any



    def _get_task_pack(self) -> None:
        """`GET /offline/task-pack?course=<课>`：把整段任务包（`task-<课>.zip`）递给云机。

        为什么由 hub 发：云机 notebook 的第一条路径就是「先连 hub，能通就从 hub 取包」
        （用户口径 2026-09-19）；包本来就是本机产物（`tmp/<课>/task-<课>.zip`，控制台
        导出写的就是它），hub 的 `<traj-root>` 正是 `tmp` ⇒ 本端点只是把**同一个文件**
        按 HTTP 递出去，不造第二份真相。

        三种拒因各说各话（非法课程名 400 / 没这个包 404 / 未鉴权 401）：人在云机上排障时，
        「去控制台点导出」与「课程名写错了」是两条完全不同的下一步。
        """
        if not self._auth_ok():
            return
        course = self._query_course()
        try:
            p = self.hub.task_pack_path(course)
        except ProtocolError as e:
            self._json({"error": str(e), "course": course}, 400)
            return
        if not p.exists():
            known = self.hub.courses()
            self._json(
                {
                    "error": (
                        f"没有任务包 {p.name}——先在控制台「导出任务包」"
                        "（随时可导，不必停训），或检查课程名"
                    ),
                    "course": course,
                    "path": str(p),
                    "known_courses": known,
                    # ★ 2026-09-25（G7）：**缺包**也要自愈一次（此前这条路径零自愈）。
                    **self._task_pack_miss_gate(course, p),
                },
                404,
            )
            return
        # ★ 模式门（2026-09-25，plan/online-offline-role-routing §2.4）：包**确实在盘上**
        # 不等于「该发给你」。切离线时控制台会自动导出 `task-<课>.zip`、而且「已有包不动」
        # （course-mode.ts）⇒ **切回在线后那个包还在**，而本端点原来不查 mode ⇒ 离线盘
        # 能把在线课取走并跑整段（L6）。判据用状态码而不是 404：包在、没丢，正确动作是
        # 「去控制台切回离线」，404 会把人引向「再导一次」（越导越乱）。
        # ⚠ 只在「**表里有它且明确 online**」时拦：冷课/未扫到的课必须照旧放行
        # （与 `_task_pack_miss_candidate` ① 同一条规则——离线课本来就不常训练，从表里
        # 掉出去是常态，拿“不在表里”当 online 会把正常取包锁死）。
        if course in self.hub.courses() and self.hub.mode_of(course) != COURSE_MODE_OFFLINE:
            self._json(
                {
                    "error": (
                        "该课现在是 online —— 离线课请先在控制台切离线（切换会自动导出"
                        "任务包）；在线课请用 battle.tailscale.ipynb"
                    ),
                    "course": course,
                    "mode": self.hub.mode_of(course),
                },
                409,
            )
            return
        # 新鲜度门（§8）：旧包比没包更危险（云机会从旧起点重跑几十轮）⇒ 过期就触发重导 + 409；
        # 判定不了（包不是 zip / 没索引 / 课程还没权重）⇒ 照发——本端点首先是文件递送。
        gate = self._task_pack_gate(course, p)
        if gate is not None:
            self._json(gate[0], gate[1])
            return
        # 包就在盘上（新鲜，或过期到上界后降级照发）⇒ **缺包账本清零**：下次真缺包能重新触发。
        reset_task_pack_miss_triggers(course)
        try:
            data = p.read_bytes()
        except OSError as e:
            self._json({"error": f"读任务包失败: {e}", "course": course}, 500)
            return
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] task-pack -> {p.name} "
            f"({len(data)} bytes)",
            flush=True,
        )
        self._bytes(data, 200, "application/zip", filename=p.name)


    def _get_offline_resume(self) -> None:
        """`GET /offline/resume?course=<课>`：递「最新同轮齐全的续跑锚点」元信息（2026-09-22）。

        为什么单开一个端点而不是改进任务包：任务包是**导出那一刻**的只读快照（控制台写的
        那个 zip，`plan_sha256` 都绑在它上面），当场重打一份就等于让 hub 去当一个「导出器」
        ——那个能力只有 `run_rl --export-bundle` 有。所以锚点另走一条小消息：云机照旧取包，
        再把锚点铺进产物目录（`remote.run_loop --resume-dir`）。

        `resume: null` 是**正常应答**（没有比包更新的进度）——不是 404：云机要能区分
        「hub 说没有」与「端点不可用/鉴权失败」。
        """
        if not self._auth_ok():
            return
        course = self._query_course()
        if not course:
            self._json({"error": "需要 ?course=<课>"}, 400)
            return
        try:
            anchor = self.hub.resume_anchor(course)
        except ProtocolError as e:
            self._json({"error": str(e), "course": course}, 400)
            return
        except KeyError:
            self._json({"error": f"未知课程 {course}", "course": course, "known": self.hub.courses()}, 404)
            return
        if anchor is None:
            self._json({"course": course, "resume": None}, 200)
            return
        pub = {k: v for k, v in anchor.items() if not k.startswith("_")}
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] resume-anchor -> {course} "
            f"it{pub['it']}（{pub['source']}, wfp={str(pub['weights_fp'])[:12]}…）",
            flush=True,
        )
        self._json({"course": course, "resume": pub}, 200)


    def _get_offline_resume_blob(self) -> None:
        """`GET /offline/resume/blob?course=<课>&it=N&name=<件>`：递锚点轮次的字节。

        三道门：鉴权 → `name` 白名单（`OFFLINE_RESUME_BLOB_NAMES`）→ 「该 it 就是当前锚点」
        （只服务锚点本身，不接受任意 it/任意路径——这里不是通用文件服务）。
        """
        if not self._auth_ok():
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        course = (qs.get("course") or [""])[0].strip()
        name = (qs.get("name") or [""])[0].strip()
        try:
            it = int((qs.get("it") or [""])[0])
        except ValueError:
            self._json({"error": "需要整数 ?it=N"}, 400)
            return
        if not course:
            self._json({"error": "需要 ?course=<课>"}, 400)
            return
        if name not in OFFLINE_RESUME_BLOB_NAMES:
            self._json(
                {"error": f"name 必须是 {list(OFFLINE_RESUME_BLOB_NAMES)} 之一，收到 {name!r}"},
                400,
            )
            return
        anchor = self.hub.resume_anchor(course)
        if anchor is None or int(anchor["it"]) != it:
            self._json(
                {
                    "error": "该 it 不是当前续跑锚点（锚点可能已被更新的轮次取代）",
                    "course": course,
                    "it": it,
                    "anchor_it": (int(anchor["it"]) if anchor else None),
                },
                409,
            )
            return
        p = Path(str(anchor["_dir"])) / name
        try:
            data = p.read_bytes()
        except OSError as e:
            self._json({"error": f"读锚点件失败: {e}", "path": str(p)}, 500)
            return
        ctype = "application/json" if name.endswith(".json") else "application/octet-stream"
        self._bytes(data, 200, ctype, filename=f"it-{it:03d}-{name}")


    def _post_offline_artifact(self) -> None:
        """补传一轮产物：权重 + opt + 账本行 → `<job_root>/offline/<run_id>/it-NNN/`。

        鉴权与其他端点完全一致（Bearer）；**没有租约**——这条腿没有 job（全离线段连 hub
        都不需要就能跑完）。幂等/首写锁定/指纹校验见 `store_offline_artifact`。
        """
        if not self._auth_ok():
            return
        raw = self._read_capped_body(OFFLINE_ARTIFACT_BODY_MAX)
        if raw is None:
            return
        try:
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ProtocolError("补传体必须是 JSON 对象")
            # 多课程（2026-09-18）：一个 hub 服务多门课时，补传必须自报归哪门课
            #（① 体里的 course/course_name，节点从 job manifest 拄来；② ?course=；
            # ③ 已有 offline/<run_id>/ 的课——补传天然会重传续投，后续自动归位）。
            course = self.hub.locate_offline_course(body, self._query_course())
            if course is None:
                raise ProtocolError(
                    "补传无法归属课程：体里带 course（或 course_name），或加 ?course=；"
                    f"本 hub 的课程：{self.hub.courses()}"
                )
            res = self.hub.store_offline_artifact(course, body)
            # 本轮随体重一并到达的云机评估行 → 课程账本（去重；失败只记一笔，
            # **不影响**补传本身的成功与否：权重才是这一趟的硬要求）。
            try:
                n_games, n_sums = self.hub.merge_eval_rows(course, body.get("eval_rows"))
                if n_games or n_sums:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] [hub-server] eval rows +{n_games} "
                        f"/ summary +{n_sums}（course={course} it{body.get('it')}）",
                        flush=True,
                    )
            except Exception as e:
                print(
                    f"[{time.strftime('%H:%M:%S')}] [hub-server] eval rows 并入失败"
                    f"（忽略）: {type(e).__name__}: {e}",
                    flush=True,
                )
        except (ProtocolError, ValueError, UnicodeDecodeError) as e:
            self._json({"error": f"补传被拒: {e}"}, 400)
            return
        if res["status"] == "accepted":
            print(
                f"[{time.strftime('%H:%M:%S')}] [hub-server] OFFLINE course={course or '-'} "
                f"it{res['it']} run={res['run_id']} <- {len(raw)}B",
                flush=True,
            )
        # duplicate 也回 200：补传是重试友好的（重连/重启续投会重传），409 会让节点把它
        # 当成「没成功」每轮再传一遍——而首写锁定已经保证了内容不会变。
        self._json(res)


    def _post_offline_result(self) -> None:
        """补传段末摘要（覆盖写：它是「这条腿现在到哪了」的最新答案）。"""
        if not self._auth_ok():
            return
        raw = self._read_capped_body(OFFLINE_RESULT_BODY_MAX)
        if raw is None:
            return
        try:
            body = json.loads(raw.decode("utf-8"))
            if not isinstance(body, dict):
                raise ProtocolError("补传体必须是 JSON 对象")
            course = self.hub.locate_offline_course(body, self._query_course())
            if course is None:
                raise ProtocolError(
                    "段末摘要无法归属课程：体里带 course（或 course_name），或加 ?course=；"
                    f"本 hub 的课程：{self.hub.courses()}"
                )
            res = self.hub.store_offline_result(course, body)
        except (ProtocolError, ValueError, UnicodeDecodeError) as e:
            self._json({"error": f"补传被拒: {e}"}, 400)
            return
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] OFFLINE course={course or '-'} "
            f"result run={res['run_id']} it{res['it_end']}",
            flush=True,
        )
        self._json(res)

    def _get_offline_tasks(self) -> None:
        """`GET /offline/tasks`（2026-09-25，`plan/offline-task-discovery.plan.md` §3.1）：可领任务清单。

        为什么不是「`/admin/courses` 加几列」：这张表的消费者是**云机**（它据此排好本次会话的
        队列），而 `/admin/*` 是控制台口径（含在线课、不含包）。默认只报 `mode=offline` 的课
        + 包在哪 + 新鲜度 + 谁在跑；`?include=all` 才附带 `not_offline`（控制台排障用）。

        **只读**（plan §1.4-2）：不触发重导、不写账本、不动游标——触发重导仍是
        `/offline/task-pack` 的专属特权。
        """
        if not self._auth_ok():
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        include_all = (qs.get("include") or [""])[0].strip().lower() == "all"
        self._json(
            {
                "tasks": self.hub.offline_tasks(include_all=include_all),
                "hub_version": OFFLINE_QUEUE_VERSION,
                "generated_at": time.time(),
            },
            200,
        )

    def _post_offline_lease(self, action: str) -> None:
        """租约三合一的入口（`claim` / `heartbeat` / `release`，plan §3.2）。

        参数一律走查询串（与其余端点同一条形状）。**409 表达业务拒绝**（被别人持有 /
        已过期 / 不是持有人）——**不用 403**：job 腿上「403 lease mismatch 被 worker 读成
        ProtocolError ⇒ 报 job 失败 ⇒ 训练停腿」已经踩过一次（plan §3.2 返回码纪律）。
        """
        if not self._auth_ok():
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

        def _q(key: str) -> str:
            return str((qs.get(key) or [""])[0]).strip()

        course = _q("course")
        if not course:
            self._json({"error": "需要 ?course=<课>"}, 400)
            return
        try:
            pack = self.hub.task_pack_path(course)
        except ProtocolError as e:
            self._json({"error": str(e), "course": course}, 400)
            return
        if action == "claim":
            worker = _q("worker")
            if not worker:
                self._json(
                    {
                        "error": (
                            "需要 ?worker=<本机 id>（写进 <work>/.worker-id）；空 id 会让两台互相顶租约"
                        ),
                        "course": course,
                    },
                    400,
                )
                return
            if not pack.is_file():
                # 404 与 `/offline/task-pack` 同口径（带已知课程表）：包都没导出来，谈领租约就早了一步。
                self._json(
                    {
                        "error": f"没有任务包 {pack.name}——先在控制台「导出任务包」",
                        "course": course,
                        "known_courses": self.hub.courses(),
                    },
                    404,
                )
                return
            takeover = _q("takeover").lower() in ("1", "true", "yes")
            lease, _why = self.hub.claim_offline(course, worker, takeover=takeover)
            if not lease:
                holder = self.hub.holder_info(course)
                who = holder["worker_id"] if holder else "?"
                left = float(holder["expires_in"]) if holder else 0.0
                self._json(
                    {
                        "error": (
                            f"这门课的离线任务已被 {who} 持有（{left:.0f}s 后过期；"
                            "确实要顶掉它加 ?takeover=1）"
                        ),
                        "course": course,
                        "held": True,
                        "holder": holder,
                    },
                    409,
                )
                return
            print(
                f"[{time.strftime('%H:%M:%S')}] [hub-server] offline-claim {course} "
                f"worker={lease['worker_id']} takeover={int(takeover)}",
                flush=True,
            )
            self._json({"lease": lease}, 200)
            return
        token = _q("lease")
        if action == "heartbeat":
            res, why = self.hub.heartbeat_offline(course, token)
            if not res:
                holder = self.hub.holder_info(course)
                who = holder["worker_id"] if holder else "?"
                self._json(
                    {
                        "error": (
                            "租约已过期（本会话产物照旧落盘 + 打包；回传可能被判 duplicate 丢弃）"
                            if why == "expired"
                            else f"租约已被 {who} 接管——本会话继续跑完并打包"
                        ),
                        "course": course,
                        "expired": why == "expired",
                        "holder": holder,
                    },
                    409,
                )
                return
            self._json(res, 200)
            return
        ok, _why = self.hub.release_offline(course, token)
        if not ok:
            self._json(
                {
                    "error": "不是当前持有人（不覆盖别人的租约）",
                    "course": course,
                    "holder": self.hub.holder_info(course),
                },
                409,
            )
            return
        self._json({"released": True, "course": course}, 200)

    def _task_pack_miss_candidate(self, course: str) -> tuple[bool, str]:
        """「这门课该不该替它造一份包」——**盘上的事实优先于「hub 扫到了没有」**（评审 S-1）。

        为什么不只认 `courses()`：课程表是「1 小时新鲜度扫描」的产物，而**离线课本机不训练**
        ⇒ 冷掉或 hub 重启之后它从表里消失；那时只认表就会让「缺包自愈」在最需要它的场景里
        静默失效（云机 404 里 `known_courses` 也没有它，排障被指向错方向）。判据：
          ① hub 表里有它且**明确是 online** ⇒ 不替它导（云机来取包是配置误会）；
          ② 其余情形只要盘上有它的开课标记（`training-enabled.txt`，控制台开课写的）就算
             「这是门真课」——拼错的课程名不会在盘上有这个文件；
          ③ 表里是 offline 但标记被删（停课残留）⇒ 也替它导（mode 是更权威的意图）。
        """
        in_table = course in self.hub.courses()
        if in_table and self.hub.mode_of(course) != COURSE_MODE_OFFLINE:
            return False, (
                "这门课在 hub 里是 online（云机来取包是配置误会，不替它导）："
                "先到控制台把它切成离线；若刚重启过 hub，检查启动参数里的课程模式"
            )
        root = self.hub.traj_root()
        if root is not None and (root / course / COURSE_ENABLE_MARKER).exists():
            return True, ""
        if in_table:
            return True, ""
        return False, (
            "hub 的表里没有这门课，且盘上没有它的开课标记"
            "（检查课程名 / hub 的 --traj-root 是否就是控制台的 tmp）"
        )

    def _task_pack_miss_gate(self, course: str, p: Path) -> dict:
        """「**缺包**」自愈门（plan/offline-switch-auto-bundle §3.4）：并入 404 正文的字段。

        为什么要有它：包不存在时 `_task_pack_gate` 根本不跑（它只处理「过期」）⇒ 这条路径
        此前**零自愈**：云机等满 `wait_pack_sec`（30 分钟）再由一句 `SystemExit` 告诉人
        （用户 2026-09-25 报障）。现在 hub 替这门课触发一次控制台重导，带节流 + 上界；
        到上界/控制台不可达 ⇒ **不制造新的等待理由**，只在正文里说清真因并指路手动。

        返回字段（全部如实，不猜）：`triggered` / `trigger_note` / `give_up`（还有节流时的
        `retry_after`）。与过期那条腿的 409 区别：那里 `triggered` 兼表「已有触发在飞」，
        这里只表「**本次**真的推了一次」。
        """
        ok, why = self._task_pack_miss_candidate(course)
        if not ok:
            return {"triggered": False, "trigger_note": f"未触发重导：{why}", "give_up": False}
        now = time.time()
        with _TASK_PACK_LOCK:
            st = _TASK_PACK_MISS_TRIGGERS.setdefault(course, {})
            verdict = decide_task_pack(
                stale=f"缺包 {p.name}",
                secs_since_trigger=(now - float(st.get("last", 0.0))) if st.get("last") else 1e9,
                triggers=int(st.get("count", 0)),
                throttle_sec=TASK_PACK_STALE_THROTTLE_SEC,
                limit=TASK_PACK_MISS_TRIGGER_LIMIT,
            )
            if verdict == "trigger":
                st["last"] = now
                st["count"] = int(st.get("count", 0)) + 1
        if verdict == "trigger":
            ok2, why2 = trigger_task_bundle_export(course)
            if ok2:
                return {
                    "triggered": True,
                    "trigger_note": "已替你触发一次重导，稍后重试（导出约需数分钟）",
                    "retry_after": TASK_PACK_STALE_THROTTLE_SEC,
                    "give_up": False,
                }
            return {
                "triggered": False,
                "trigger_note": (
                    f"想替你触发重导，但控制台不可达/不接受（{why2}）"
                    "：请到控制台点一次「导出任务包」"
                ),
                "give_up": False,
            }
        if verdict == "throttled":
            return {
                "triggered": False,
                "trigger_note": "刚刚已触发过重导（节流窗内不再重复打扰控制台）",
                "retry_after": TASK_PACK_STALE_THROTTLE_SEC,
                "give_up": False,
            }
        _hub_log(
            f"task-pack {course}: 缺包已连续触发 {TASK_PACK_MISS_TRIGGER_LIMIT} 次仍没有包"
            "——不再触发，只指路手动"
        )
        return {
            "triggered": False,
            "trigger_note": (
                f"已连续触发 {TASK_PACK_MISS_TRIGGER_LIMIT} 次重导仍没有包"
                "——请到控制台手动「导出任务包」，并确认 tmp/<课>/weights.json 存在"
                "（导出要有起点权重）"
            ),
            "give_up": True,
        }

    def _task_pack_gate(self, course: str, p: Path) -> tuple[dict, int] | None:
        """过期门（plan §8.3）：返回 `(响应体, 状态码)` = 该拒；`None` = 照发。

        四种结局都在这里定死（纯判据在 `decide_task_pack`/`task_pack_stale_reason`）：
        `trigger` 触发重导后 409 ↦ `throttled` 窗内不再触发、直接 409 ↦ `give_up` 到顶
        **照发 + 告警**（不把云机 brick 到 deadline）↦ 判不了/新鲜：`None`。
        """
        pack_init = pack_index_part_sha(p, INIT_WEIGHTS_NAME)
        # 活动权重就在包的**同一个课程目录**（`<traj>/<课>/weights.json`，回传轮推进它）：
        # 从包路径反推而非查 store —— 未发现的课程名也能判（`task_pack_path` 也是这么算的）。
        active_sha = _file_sha256(p.parent / _JobStore.ACTIVE_WEIGHTS_NAME)
        stale = task_pack_stale_reason(pack_init_sha=pack_init, active_sha=active_sha)
        if not stale:
            reset_task_pack_triggers(course)  # 判据回到"新鲜" ⇒ 计数清零
            return None
        now = time.time()
        with _TASK_PACK_LOCK:
            st = _TASK_PACK_TRIGGERS.setdefault(course, {})
            # 显式传两个旋钮（不靠默认参数）：默认值在 import 时绑定，改不了、也不该被改。
            verdict = decide_task_pack(
                stale=stale,
                secs_since_trigger=(now - float(st.get("last", 0.0))) if st.get("last") else 1e9,
                triggers=int(st.get("count", 0)),
                throttle_sec=TASK_PACK_STALE_THROTTLE_SEC,
                limit=TASK_PACK_STALE_TRIGGER_LIMIT,
            )
            if verdict == "trigger":
                # 先记账再发请求：并发请求里只有第一个真去触发（窗内其余看到 throttled）。
                st["last"] = now
                st["count"] = int(st.get("count", 0)) + 1
            warn_once = verdict == "give_up" and not st.get("warned")
            if warn_once:
                st["warned"] = 1.0
        if verdict == "trigger":
            ok, why = trigger_task_bundle_export(course)
            detail = (
                "已触发控制台重导，请稍后重试"
                if ok
                else f"控制台不可达/不接受触发（{why}）：请到控制台点一次「导出任务包」，稍后重试"
            )
            return (
                {
                    "error": f"任务包已过期（{stale}）——{detail}",
                    "course": course,
                    "stale": True,
                    "triggered": ok,
                },
                409,
            )
        if verdict == "throttled":
            return (
                {
                    "error": f"任务包已过期（{stale}）——刚刚已触发过重导，请稍后重试",
                    "course": course,
                    "stale": True,
                    "triggered": True,
                },
                409,
            )
        # give_up：到上界仍过期 ⇒ 照发旧包（起点仍由 resume 锚点兜）——把保险丝说出来。
        if warn_once:
            _hub_log(
                f"task-pack {course}: 连续触发 {TASK_PACK_STALE_TRIGGER_LIMIT} 次仍判过期"
                f"（{stale}）——先照发旧包（云机侧靠 resume 锚点续跑），不再刷触发"
            )
        return None
