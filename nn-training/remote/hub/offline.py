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
    OFFLINE_ARTIFACT_BODY_MAX,
    OFFLINE_RESULT_BODY_MAX,
    OFFLINE_RESUME_BLOB_NAMES,
    ProtocolError,
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

    # 由组合类提供（实现都在 `remote/hub_server.HubHandler`）——混入只声明类型。
    _auth_ok: Any
    _bytes: Any
    _json: Any
    _query_course: Any
    _read_capped_body: Any



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
                        f"没有任务包 {p.name}——先在控制台导出（导出要求训练已停），"
                        "或检查课程名"
                    ),
                    "course": course,
                    "path": str(p),
                    "known_courses": known,
                },
                404,
            )
            return
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
                n_eval = self.hub.merge_eval_rows(course, body.get("eval_rows"))
                if n_eval:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] [hub-server] eval rows +{n_eval} "
                        f"（course={course} it{body.get('it')}）",
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
