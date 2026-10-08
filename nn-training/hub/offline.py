"""hub/offline.py —— 离线段面（2026-09-24 S4 第十一刀）。

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
    INIT_WEIGHTS_NAME,
    OFFLINE_ARTIFACT_BODY_MAX,
    OFFLINE_CLAIM_PROTO_VERSION,
    OFFLINE_LEASE_TTL_SEC,
    OFFLINE_QUEUE_VERSION,
    OFFLINE_RESULT_BODY_MAX,
    OFFLINE_RESUME_BLOB_NAMES,
    PLAN_NAME,
    ProtocolError,
)

# 任务包新鲜度门 / 缺包自愈门 / 清单读数的纯判据与模块状态都住 `hub/task_pack.py`（叶子）——
# 它有两个读者（本模块的取包端点 + `queue_offline` 的清单），所以**不**挂在本模块上（否则
# 队列那一侧要向上 import，账本当场报反向边）。本模块因此从 L0 升为 L1。
from hub.store import _JobStore
from hub.task_pack import (
    _TASK_PACK_LOCK,
    _TASK_PACK_MISS_TRIGGERS,
    _TASK_PACK_TRIGGERS,
    TASK_AUTO_HANDOFF_RETRY_SEC,
    TASK_PACK_MISS_TRIGGER_LIMIT,
    TASK_PACK_STALE_THROTTLE_SEC,
    TASK_PACK_STALE_TRIGGER_LIMIT,
    _file_sha256,
    _hub_log,
    auto_handoff_decision,
    decide_task_pack,
    note_auto_handoff_trigger,
    pack_index_meta,
    pack_index_part_sha,
    reset_auto_handoff_triggers,
    reset_task_pack_miss_triggers,
    reset_task_pack_triggers,
    task_pack_stale_reason,
    trigger_auto_handoff,
    trigger_task_bundle_export,
)

#: `offline_advance_ok` 的拒因 → 日志文案（★P1-1）。住这里而不是 hub 深层的理由：这句话是给
#: **现场的人**看的（为什么这一轮没推进起点），而判据住在 `_HubQueue`（那里不打印）。
#: ★M4b：`pinned_online` 那条腿随权威删除。
_ADVANCE_SKIP_TEXT = {
    "hold_stale": "持有人的进度已静默超阈（stale）——迟到回传不夺回起点",
    "hold_token": "租约 token 不符（不是本课当前持有人）",
}


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

    # 由组合类提供（实现都在 `hub/http_face.py::HubHandler`）——混入只声明类型。
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
        # ★M4b：模式三门（`pinned_online` / `not_offline` / 旧 mode）随「课程无模式」删除 ——
        # 取包面现在只有两道闸：① live hold 的 lease 门（下一段）② 新鲜度门（再下一段）。
        # 停课不在这条腿上拦（★六轮 F3 定死：正在跑的会话被停课后仍要能重取包；冷课无标记
        # 的存量路径也靠它照发，见 `test_task_pack_cold_course_still_served`）。
        # ★M1b / P1-2：**live hold ⇒ 只有持 lease 的那台盘拿得到包**（新客户端取包带 `?lease=`）。
        # 为什么必须有这道门：上面的模式三门随模式一起退役后，「包在盘上」就等于「发给任何人」
        # ——正在被云机 A 跑的课会被云机 B 取走 = 同一份活两处跑（数据损坏级）。判据取 **hold**
        # （落盘、重启不丢）而不是内存租约：`state == "live"` 才是「有人真在跑」；stale
        # （进度静默超阈）按设计**可以**被别人取（那是自动接管那条腿）。
        # 旧客户端（不带 `?lease=`）在**没人持**的时候照旧能取包 —— 升级窗口里不 brick 现场；
        # 有人持的时候拿 409，这就是 P1-2 要给旧端留的第二道闸。
        hold = self.hub.hold_of(course)
        if str(hold.get("state") or "") == "live":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            token = str((qs.get("lease") or [""])[0]).strip()
            if token != str(hold.get("token") or ""):
                who = str(hold.get("worker_id") or "?")
                self._json(
                    {
                        "error": (
                            f"这门课的离线任务正被 {who} 接管（hold v2）——"
                            "要取包请先用 /offline/claim 领到租约并带上 ?lease=<token>；"
                            "确实要顶掉它到控制台点「强制解除接管」"
                        ),
                        "course": course,
                        "held": True,
                        "holder": self.hub.holder_info(course),
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
            # ★P1-1（M1b）：**盖章无条件、advance 要活+持准**——镜像/归档照落（算过什么的
            # 证据），但活动起点只听**活跃 hold 的持有人**。判据三态见 `offline_advance_ok`。
            advance, why = self.hub.offline_advance_ok(
                course, str(body.get("lease_token") or "")
            )
            res = self.hub.store_offline_artifact(course, body, advance_active=advance)
            res["advance_active"] = bool(advance)
            if why:
                res["advance_skipped"] = why
                print(
                    f"[{time.strftime('%H:%M:%S')}] [hub-server] 补传不推进活动起点"
                    f"（course={course} run={res.get('run_id')} it{res.get('it')}）："
                    f"{_ADVANCE_SKIP_TEXT.get(why, why)}",
                    flush=True,
                )
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
            # T6（plan/auto-offline-handoff §3.6）：跑满自报 = 把当前包记为 completed
            # ⇒ 该课**不可再领**（U6），直到人重导包（新 sha）自动解封。这是 T6 的
            # hub 侧生产链接线——此前 `note_offline_completed` 只有测试直接调（死代码）。
            # ★P1-7 / R3-f：盖章前校验「这份自报就是**当前包**的段末」（run_id / plan_sha256）：
            # 旧包/旧会话的 `end_it_reached` 不得封住新包（否则重导后依旧不可领）。
            if res.get("end_it_reached"):
                sealed, why = self._end_seal_ok(course, body)
                res["completed_sealed"] = sealed
                if sealed:
                    self.hub.note_offline_completed(course)
                elif why:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] [hub-server] 段末自报不盖章"
                        f"（course={course} run={res['run_id']}）：{why}",
                        flush=True,
                    )
        except (ProtocolError, ValueError, UnicodeDecodeError) as e:
            self._json({"error": f"补传被拒: {e}"}, 400)
            return
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server] OFFLINE course={course or '-'} "
            f"result run={res['run_id']} it{res['it_end']}"
            + ("（跑满）" if res.get("end_it_reached") else ""),
            flush=True,
        )
        self._json(res)

    def _end_seal_ok(self, course: str, body: dict) -> tuple[bool, str]:
        """段末自报能不能给「当前包」盖 completed 章（★P1-7 / R3-f）。只拦**有反证**的情形：

        判据 = 当前任务包的身份（`run_id` / `plan_sha256`，都是包里已有的字段）与自报逐项
        对照。包读不到 / 自报缺该项 ⇒ **不判**（照旧盖章——不制造新的失败态，旧形状的用例
        与旧 worker 不受影响）；只有「两侧都有值且不等」才拒章，那正是要拦的：旧包/旧会话的
        `end_it_reached` 把**新包**封成 completed（重导后依旧不可领，U6 反着生效）。
        """
        try:
            pack = self.hub.task_pack_path(course)
        except ProtocolError:
            return True, ""
        if not pack.is_file():
            return True, ""
        meta = pack_index_meta(pack)
        pack_run = str(meta.get("run_id") or "")
        pack_plan = pack_index_part_sha(pack, PLAN_NAME)
        got_run = str(body.get("run_id", "") or "")
        got_plan = str(body.get("plan_sha256", "") or "")
        if pack_run and got_run and pack_run != got_run:
            return False, f"自报 run_id={got_run} ≠ 当前包 run_id={pack_run}"
        if pack_plan and got_plan and pack_plan != got_plan:
            return False, (
                f"自报 plan_sha256={got_plan[:12]}… ≠ 当前包 {pack_plan[:12]}…"
            )
        return True, ""

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
        # ★M1b / Q3：`?worker=` 让清单把「一拖一」算进去（`busy` / `claimable` 按这台盘判）。
        # 缺省仍是**上界**语义（不含一拖一）——控制台排障不带 worker，不想看到自己的机群互挡。
        worker = str((qs.get("worker") or [""])[0]).strip()
        self._json(
            {
                "tasks": self.hub.offline_tasks(include_all=include_all, worker=worker),
                "hub_version": OFFLINE_QUEUE_VERSION,
                "generated_at": time.time(),
            },
            200,
        )

    def _get_offline_hold(self) -> None:
        """`GET /offline/hold?course=<课>`（★M1b / plan §1.5.2-P0-5①）：接管只读面。

        回答一个问题：「这门课上现在有没有**活着**的接管」——trainer 在每轮边界问一次
        （≤3s 超时），据此决定「本机这段该不该让给云机」（`held` 语义）。**只读**：不建 hold、
        不刷活性、不写账本。活性判据只认 `last_progress_at`（900s 无进度即 `state="stale"`），
        所以 `held = state == "live"` —— stale 不算 held（那是「云机掉线了」，本机该立刻恢复）。
        取不到（hub 不可达 / 401 / 课程名错）= 调用方退文件通道，本端点不制造新失败态。
        """
        if not self._auth_ok():
            return
        course = self._query_course()
        if not course:
            self._json({"error": "需要 ?course=<课>"}, 400)
            return
        hold = self.hub.hold_of(course)
        export = self.hub.pending_export_of(course)
        # 龄/TTL 余量走 `holder_info`（它才是「心跳龄 / TTL 余量」的记账面；`hold_of` 只给
        # hold 自己的字段 + state/expires_in）。两处同源：同一个 hold、同一把尺子。
        info = self.hub.holder_info(course) or {}
        state = str(hold.get("state") or "")
        self._json(
            {
                "course": course,
                "held": state == "live",
                "state": state,
                "worker_id": str(hold.get("worker_id") or ""),
                # 注意不能用 `or -1.0`：`progress_ago=0.0`（刚打过点）是**真值**，`or` 会把它
                # 变成缺失哨兵——「刚claim」在 trainer 眼里就成了「没进度」。
                "progress_ago": (
                    float(info["progress_ago"]) if info.get("progress_ago") is not None else -1.0
                ),
                "expires_in": float(info.get("expires_in") or 0.0),
                "last_progress_at": float(hold.get("last_progress_at") or 0.0),
                #: 导包软态：**不占闸**（Q1），读面照报 —— trainer 据此把文案从「云机在跑」
                #: 换成「正在给云机造包」（两者对协作盘的含义相同：这段先别派给它）。
                "pending_export": bool(export),
                "pending_export_by": str(export.get("by") or ""),
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
            # ★M1b（plan §1.5.2-P0-4）：**显式拒旧端**。本轮起「谁在跑这门课」由 hold 记账；
            # 旧码既不带 `?proto=2` 也不懂 hold ⇒ 在**入口**就让它停住（不靠它自律）。
            # 为什么借 `busy` 这个键：旧码的 409 分流里只有 `busy` 腿会把 `error` **原样**
            # 打进会话日志且本拍不跑（`remote/offline_boot.py` 的 blocker 路径；不耗 idle 预算、
            # 绝不静默双跑）——于是「请刷新 notebook」这句能真正到达现场。新码据 `proto_required` 判读。
            if _q("proto") != str(OFFLINE_CLAIM_PROTO_VERSION):
                self._json(
                    {
                        "error": (
                            "hub 的离线派发已改为 hold v2（接管模型）：本会话的客户端太旧"
                            f"（缺 ?proto={OFFLINE_CLAIM_PROTO_VERSION}）——请刷新"
                            "`battle.offline.ipynb` 后重开会话（旧会话把当前段跑完并打包即可，"
                            "不会与云机双跑）"
                        ),
                        "course": course,
                        "busy": True,
                        "proto_required": OFFLINE_CLAIM_PROTO_VERSION,
                        "retry_after": TASK_AUTO_HANDOFF_RETRY_SEC,
                    },
                    409,
                )
                return
            # 开课门（★M4b）：唯一 opt-out = 停课（标记不在）⇒ 409 `not_offline`。
            # （旧的 pinned_online 与「冷课 online 记录」两条门随权威删除；在训课一律放行，
            # 它的包由下面的导包触发现场生成。）
            if not self.hub.course_open(course):
                self._json(
                    {
                        "error": (
                            "这门课不在训练中（开课标记已删）⇒ 不在候选："
                            "重新开课后云机会自动接走"
                        ),
                        "course": course,
                        "not_offline": True,
                    },
                    409,
                )
                return
            if not pack.is_file():
                # P0-1（plan/auto-offline-handoff §3.1a）：在训课**本来就没有包**——claim 不能 404，
                # 而是「记 `pending_export` + 请控制台导包 + 409 指路」（★M4b：不建 hold）；
                # 不属候选的课（停课/未知）仍是 404。
                # ★六轮 F4：带上 worker（`begin_pending_export` 的「换主/超窗 ⇒ 重置触发账本」要用它）。
                self._claim_without_pack(course, pack, worker)
                return
            sha = _file_sha256(pack)
            if self.hub.completion_blocked(course, sha):
                # 段末摘要报到跑满 ⇒ 不可再领（二轮 P1-1）；重导包 sha 变 = 自动解封。
                self._json(
                    {
                        "error": (
                            "这门课当前任务包已跑满（不自动重跑）——"
                            "等人停课，或到控制台重导任务包（新包 sha 不同即自动解封）"
                        ),
                        "course": course,
                        "completed": True,
                    },
                    409,
                )
                return
            takeover = _q("takeover").lower() in ("1", "true", "yes")
            lease, why = self.hub.claim_offline(course, worker, takeover=takeover)
            if not lease:
                if why == "busy":
                    self._json(
                        {
                            # ★M1b：busy 按 **worker** 判（同一台盘串行；别的盘不互挡）——
                            # 所以这里必须把发起方带进去，否则回执里说不出「哪台盘在跑哪门课」。
                            "error": self.hub.busy_reason(course, worker)
                            or "别的课正在跑（一拖一：一次只 drain 一门）",
                            "course": course,
                            "busy": True,
                            "retry_after": TASK_AUTO_HANDOFF_RETRY_SEC,
                        },
                        409,
                    )
                    return
                holder = self.hub.holder_info(course)
                who = holder["worker_id"] if holder else "?"
                left = float(holder["expires_in"]) if holder else 0.0
                # ★M1b（plan §1.2-③ 不变量 3 + P2-4）：**live 的接管不可被顶**——`?takeover=1`
                # 也不再能覆盖它（旧文案说的「加 takeover=1 顶掉」已作废）。要清只有两条路：
                # ① 它自己进度静默超阈（届时新主**自动**接管）② 人到控制台点「强制解除接管」。
                # 为什么改：旧写法把「不能覆盖 live」变成一句空话（任何人一个参数就能双跑）。
                self._json(
                    {
                        "error": (
                            f"这门课的离线任务正被 {who} 接管（{left:.0f}s 内无进度即自动可接管）"
                            "——live 的接管不可被顶（takeover 也不行）；请换下一门，"
                            "或到控制台点「强制解除接管」再领"
                        ),
                        "course": course,
                        "held": True,
                        "holder": holder,
                    },
                    409,
                )
                return
            # 包真的在手里的这一刻，把「缺包重试」账本清掉（下次再缺从零计数）。
            reset_auto_handoff_triggers(course)
            # ★ 2026-10-04（用户报障「抢占在训在线课之后，在线训练的权重和 opt 全丢了？」）：
            #   **有包**的自动课在 claim 成功时也要请控制台判一次新鲜度 —— 包是「导出那一刻的
            #   起点（代码+权重+动量）」的快照，而在训课的权重**每轮都在动**；离线腿的续跑
            #   锚点只认回传/导入的轮次（`queue_resume.resume_sources`），**看不见本机在线轮**
            #   ⇒ 拿旧包 = 把云机拖回旧起点（白丢在线进度）。控制台按「包比活动权重新?」决定
            #   跳过还是作废+重导（规则表 ⑥'），且作废是**同步**的（renameSync 在本次 HTTP
            #   响应内完成）⇒ 云机随后取包时旧包已不在（404 等新包）——竞态在响应返回前关闭。
            handoff_note = self._ask_console_freshness(course)
            # ★ 2026-10-08（e2e 三条红 → 真 bug）：进度锚以**响应时刻**为准 —— claim 的第一个
            # 进度锚在 claim 开头就打了（`claim_offline` 末段的 `note_hold`），而上面的控制台
            # 核对是**阻塞调用**（控制台不可达时实测 ~2s，上限 `TASK_PACK_TRIGGER_TIMEOUT_SEC`）
            # ⇒ 秒级判活窗下（e2e 用 `BCITY_HOLD_PROGRESS_STALE_SEC=2`）刚交到客户端手里的
            # hold 当场是 stale：`GET /offline/hold` 回 held=false、清单 `claimable` 翻 true、
            # 同一台盘的 busy 闸同时失效（一拖一形同虚设）。带 token 重打：核对期间万一被
            # 别人 stale-接管，这一拍不会把旧主复活（`note_progress` 的令牌门）。
            self.hub.note_progress(course, token=str(lease.get("token") or ""))
            print(
                f"[{time.strftime('%H:%M:%S')}] [hub-server] offline-claim {course} "
                f"worker={lease['worker_id']} takeover={int(takeover)}",
                flush=True,
            )
            payload: dict = {"lease": lease}
            if handoff_note:
                payload["handoff"] = handoff_note
            self._json(payload, 200)
            return
        token = _q("lease")
        if action == "heartbeat":
            res, why = self.hub.heartbeat_offline(course, token)
            if not res:
                holder = self.hub.holder_info(course)
                who = holder["worker_id"] if holder else "?"
                if why == "revoked":
                    error = (
                        "租约已被撤销（人强制解除了接管）——本会话继续跑完并打包；"
                        "下一会话不要再领它"
                    )
                elif why == "expired":
                    error = "租约已过期（本会话产物照旧落盘 + 打包；回传可能被判 duplicate 丢弃）"
                else:
                    error = f"租约已被 {who} 接管——本会话继续跑完并打包"
                self._json(
                    {
                        "error": error,
                        "course": course,
                        "expired": why == "expired",
                        "revoked": why == "revoked",
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

    def _post_offline_progress(self) -> None:
        """`POST /offline/progress?course=<课>&lease=<token>`（★M1b / Q2）：**进度打点**。

        与心跳**同一套** token 分流（过期 / revoked / taken），差别只在效果：心跳只续 TTL，
        打点额外刷 `last_progress_at`（活性）——「任何合法接触都刷 TTL，只有进度刷活性」
        （§1.1 / P2-1）。打点必须落在**轮内完成事件**上（每 N 局完 / ≤300s 的检查点），
        不许另开一个定时线程 —— 那正是「心跳活、进度死」的教训（§68：心跳不能当活性）。
        """
        if not self._auth_ok():
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        course = str((qs.get("course") or [""])[0]).strip()
        if not course:
            self._json({"error": "需要 ?course=<课>"}, 400)
            return
        token = str((qs.get("lease") or [""])[0]).strip()
        ok, why = self.hub.progress_offline(course, token)
        if ok:
            self._json(
                {"ok": True, "course": course, "ttl_sec": OFFLINE_LEASE_TTL_SEC}, 200
            )
            return
        error = {
            "expired": "打点无效：租约已过期（本会话继续跑完并打包；回传可能被判 duplicate）",
            "revoked": "打点无效：租约已被撤销（人强制解除了接管）——跑完当前段即退出",
        }.get(why, "打点无效：租约已被别人接管——跑完当前段即退出")
        self._json(
            {
                "ok": False,
                "error": error,
                "course": course,
                "expired": why == "expired",
                "revoked": why == "revoked",
                "holder": self.hub.holder_info(course),
            },
            409,
        )

    def _ask_console_freshness(self, course: str) -> str:
        """自动课 claim 成功后：请控制台核对任务包新鲜度；返回一行回执（空 = 不该问/没问到）。

        与 `_claim_without_pack` 共用同一套触发账本（`auto_handoff_decision` 的节流 + 上界）：
        调用点刚 `reset_auto_handoff_triggers`（claim 成功清零）⇒ 每次新 claim 从零计数，
        云机在导包窗口里的轮询不会把控制台打爆。控制台不可达/不接受 ⇒ 只在回执里说清，
        半状态（包偏旧）由停滞告警与人工导出兜住。
        """
        if not self.hub.course_open(course):
            return ""
        decision = auto_handoff_decision(course)
        if decision == "throttled":
            return "控制台核对任务包新鲜度：本窗口已触发过（节流中，完成后云机会取到新包）"
        if decision != "trigger":
            return ""
        note_auto_handoff_trigger(course)
        ok, why2 = trigger_auto_handoff(course)
        if ok:
            return (
                "已请控制台核对任务包新鲜度（包比权重或源码旧会自动作废重导；"
                "作废在本次响应返回前已完成 —— 随后取包拿到的一定是当前进度 + 最新代码）"
            )
        return (
            f"想请控制台核对任务包新鲜度，但控制台不可达/不接受（{why2}）——"
            "盘上的包可能偏旧，请到控制台点一次「导出任务包」"
        )

    def _claim_without_pack(self, course: str, pack: Path, worker_id: str = "") -> None:
        """claim 遇缺包：记 `pending_export` 软态 + 请控制台导包（409 指路）；其余 ⇒ 旧 404。

        为什么不能让云机先等着：包的出现路径是「有人导出」——而**没有人**会替它导
        （导包由 claim 触发）。所以这个分支是整个导包链的**入口**（plan §3.1a），
        它必须在「包里没有包」时也能推进状态，而不是让云机在 30 分钟超时后 SystemExit。
        ★M4b：写入软态与导包触发两件事都已在 `begin_pending_export` 里与触发账本同步
        （Q1：无包 claim **不建 hold、不翻任何模式**）。
        """
        verdict, why = self.hub.begin_pending_export(course, worker_id)
        if verdict == "busy":
            self._json(
                {
                    "error": why,
                    "course": course,
                    "busy": True,
                    "retry_after": TASK_AUTO_HANDOFF_RETRY_SEC,
                },
                409,
            )
            return
        if verdict != "pending":
            # 404 与 `/offline/task-pack` 同口径（带已知课程表）：这门课不是候选，
            # 缺包只能人工导（不替人决定）。
            self._json(
                {
                    "error": f"没有任务包 {pack.name}——先在控制台「导出任务包」（{why}）",
                    "course": course,
                    "known_courses": self.hub.courses(),
                },
                404,
            )
            return
        decision = auto_handoff_decision(course)
        if decision == "trigger":
            note_auto_handoff_trigger(course)
            ok, why2 = trigger_auto_handoff(course)
            note = (
                "已请控制台写配置并导包（导出约需数分钟）"
                if ok
                else f"想替这门课导包，但控制台不可达/不接受（{why2}）——请到控制台点一次「导出任务包」"
            )
        elif decision == "throttled":
            note = "已经在导包了（节流窗内不重复触发）"
        else:
            note = "连续触发导包仍没有包——请到控制台检查这门课的自动交接（导出失败？）"
        self._json(
            {
                "error": f"这门课的任务包正在生成：{note}",
                "course": course,
                "pending_export": True,
                "triggered": decision == "trigger",
                "give_up": decision == "give_up",
                "retry_after": TASK_AUTO_HANDOFF_RETRY_SEC,
                "trigger_note": note,
            },
            409,
        )

    def _task_pack_miss_candidate(self, course: str) -> tuple[bool, str]:
        """「这门课该不该替它造一份包」——**盘上的事实优先于「hub 扫到了没有」**（评审 S-1）。

        为什么不只认 `courses()`：课程表是「1 小时新鲜度扫描」的产物，而**自主课本机不训练**
        ⇒ 冷掉或 hub 重启之后它从表里消失；那时只认表就会让「缺包自愈」在最需要它的场景里
        静默失效（云机 404 里 `known_courses` 也没有它，排障被指向错方向）。判据：
          ① 盘上有它的开课标记（`training-enabled.txt`，控制台开课写的）⇒「这是门真课」
             ——拼错的课程名不会在盘上有这个文件；
          ② 表里有它但盘上读不到标记（单课程裸 job_root 等）⇒ 也替它导（不误杀）。
        ★M4b：旧的「表里是 online ⇒ 不替它导」一条随模式删除（没有在线/离线之分了）。
        """
        in_table = course in self.hub.courses()
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
