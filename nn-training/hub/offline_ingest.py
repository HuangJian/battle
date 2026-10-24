"""hub/offline_ingest.py —— 离线产物的**共享入账面**（2026-10-24，W5，`plan/push-mode-notebooks` §6.7）。

为什么要有它：补传产物现在有**两条入口** —— 云机自己 `POST /offline/artifact`，以及 push 派发器
从 worker 的 outbox 拉（`GET /offline/push/round`）。两条腿的校验（首写锁定 / 指纹 / 冲突 / 评估行
并入）不能一条有一份：那正是「push 模式静默写出错位产物」的入口。所以 HTTP handler 与派发器
调**同一个函数**，handler 只剩「鉴权 → 读 bounded body → 定位课程」。

★ **权威 = assignment，不是 worker**（评审 P0-5）：HTTP 路径上 worker 是权威（它自己导出的包）；
push 路径上 course / run_id 都是 hub 派的，一个 worker 侧 bug（或 blob 串包）就能把体写进另一门课
/ 另一个 run —— 首写锁定与 409 都拦不住它（它看起来是一次合法的新写入）。所以这里收
`run_id=`（派发器显式传 assignment 的值）：体里的 `run_id` 与之不符 ⇒ 409、**不落盘**、不推进 cursor；
体里缺 `run_id` ⇒ 用 assignment 的（权威补位，而不是拒收 —— 旧 outbox 可能没这个键）。

`parts="metrics"` 是另一条腿（只有统计、没有权重那一半）：它**不推进活动起点、不创建 `it-N/`**，
只喂 `store_offline_metrics` + 并入评估行。push 腿靠它保住「锚点之间那些轮」的读数
（y31-null 的教训：没有权重体的轮若连 row 一起丢，控制台整段空白）。

秩 **L1**：只依赖 `common.protocol` 与 `hub.task_pack`(L0)；`hub.offline`(L3) 与
`hub.offline_push`(L4) 都站在它上面（`hub` 以鸭子类型传入，不 import 任何调度面）。
"""

from __future__ import annotations

from typing import Any

from common.protocol import PLAN_NAME, ProtocolError
from hub.task_pack import (
    SEG_END_COURSE,
    SEG_END_OPEN,
    SEG_END_SEGMENT,
    _hub_log,
    auto_handoff_decision,
    auto_handoff_failure,
    note_auto_handoff_trigger,
    pack_index_meta,
    pack_index_part_sha,
    pack_seg_end_kind,
    trigger_auto_handoff,
)

#: `offline_advance_ok` 的拒因 → 日志文案。判据住在调度面（那里不打印），这句话是给现场的人看的。
_ADVANCE_SKIP_TEXT = {
    "hold_stale": "持有人的进度已静默超阈（stale）——迟到回传不夺回起点",
    "hold_token": "租约 token 不符（不是本课当前持有人）",
}

#: 统一日志（带时刻与 `[hub-server]` 前缀；与 hub 其余 `print` 同形）。
_log = _hub_log


class AuthorityConflictError(ProtocolError):
    """体自报的 course/run_id 与派发器的 assignment 不符（评审 P0-5）。"""


def _authority_body(body: dict, *, course: str, run_id: str, source: str) -> dict:
    """按 assignment 校正/校验体（**只在 push 路径**：`run_id` 非空时才启用）。

    * 体里 `run_id` 缺失 ⇒ **补上** assignment 的（旧 outbox 没这个键；hub 才是权威）。
    * 体里 `run_id`/`course` 有值且不符 ⇒ `AuthorityConflictError`（调用方透成 409 + 标 conflict）。
    """
    if not run_id:
        return body
    out = dict(body)
    got = str(out.get("run_id") or "").strip()
    if got and got != run_id:
        raise AuthorityConflictError(
            f"体自报 run_id={got} ≠ assignment run_id={run_id}（{source}）——不落盘"
        )
    got_course = str(out.get("course") or out.get("course_name") or "").strip()
    if got_course and got_course != course:
        raise AuthorityConflictError(
            f"体自报 course={got_course} ≠ assignment course={course}（{source}）——不落盘"
        )
    out["run_id"] = run_id
    if got_course:
        out["course"] = course
    return out


def ingest_offline_artifact(
    hub: Any,
    course: str,
    body: dict,
    *,
    parts: str = "",
    run_id: str = "",
    lease_token: str | None = None,
    size: int = 0,
    source: str = "worker",
    log: Any = _log,
) -> tuple[int, dict]:
    """一轮产物入账 → `(状态码, 响应体)`。

    `lease_token=None` 表示「按体里的 token 判 advance」（HTTP 腿）；push 腿传 assignment 的
    token（hub 自己就是租约持有人，worker 的体里没有它）。

    抛 `ProtocolError`/`ValueError` 表示**体本身不合法**（HTTP 腿 400）；`AuthorityConflictError`
    是同一族（handler 也 400，派发器标 conflict）。
    """
    try:
        body = _authority_body(body, course=course, run_id=run_id, source=source)
    except AuthorityConflictError as e:
        # 权威面不符 = **有答的拒收**（不是「体坏了」）：调用方要据此标 `conflict` 并换机，
        # 而不是把它当 400 重试同一份体。HTTP 腿不会走到这里（它的 `run_id` 为空）。
        log(f"拒收（权威不符）：{e}")
        return 409, {"error": str(e), "status": "conflict", "course": course, "run_id": run_id}
    if parts and parts not in ("metrics",):
        # ★ 2026-10-24（评审 P2-2）：只实现了 `?parts=metrics`。未知值以前会静默落进
        #   「整轮」分支，客户端以为按件发、hub 却按整轮校验（缺 weights_json ⇒ 400），
        #   错误信息离真因很远。这里响亮拒收，新 part 落地时再显式加白名单。
        return 400, {"error": f"未知 parts={parts!r}（当前只支持 metrics）"}
    if parts == "metrics":
        res = hub.store_offline_metrics(course, body)
        _merge_eval_rows(hub, course, body, log)
        log(
            f"OFFLINE-METRICS course={course or '-'} run={res.get('run_id')} "
            f"rows={res.get('rows')} skip={res.get('skipped')} <- {size}B"
        )
        return 200, res
    tok = str(body.get("lease_token") or "") if lease_token is None else str(lease_token)
    # ★P1-1（M1b）：**盖章无条件、advance 要活+持准** —— 镜像/归档照落（算过什么的证据），
    # 但活动起点只听**活跃 hold 的持有人**。判据三态见 `offline_advance_ok`。
    advance, why = hub.offline_advance_ok(course, tok)
    res = hub.store_offline_artifact(course, body, advance_active=advance)
    res["advance_active"] = bool(advance)
    # ★ 2026-10-11 事故：同一轮已有**不同字节**的权重 ⇒ 409（不静默、不覆盖、不并账）。
    # 这一支必须在 `merge_eval_rows` **之前**返回：那些 eval 行属于本次这一份权重，
    # 把它们并进 hub 上那份**不同**的轮，等于伪造读数。
    if res.get("status") == "conflict":
        log(
            f"补传冲突（不覆盖）：course={course or '-'} run={res.get('run_id')} "
            f"it{res.get('it')} hub={str(res.get('have_fp') or '')[:12]}… "
            f"got={str(res.get('got_fp') or '')[:12]}…（{source}）"
        )
        return 409, res
    if why:
        res["advance_skipped"] = why
        log(
            f"补传不推进活动起点（course={course} run={res.get('run_id')} "
            f"it={res.get('it')}）：{_ADVANCE_SKIP_TEXT.get(why, why)}"
        )
    _merge_eval_rows(hub, course, body, log)
    if res.get("status") == "accepted":
        log(
            f"OFFLINE course={course or '-'} it{res.get('it')} run={res.get('run_id')} "
            f"<- {size}B（{source}）"
        )
    return 200, res


def _merge_eval_rows(hub: Any, course: str, body: dict, log: Any) -> None:
    """本轮随体到达的云机评估行 → 课程账本（去重；失败只记一笔，不影响产物本身）。"""
    try:
        n_games, n_sums = hub.merge_eval_rows(course, body.get("eval_rows"))
        if n_games or n_sums:
            log(f"eval rows +{n_games} / summary +{n_sums}（course={course} it{body.get('it')}）")
    except Exception as e:
        log(f"eval rows 并入失败（忽略）: {type(e).__name__}: {e}")


def ingest_offline_result(
    hub: Any,
    course: str,
    body: dict,
    *,
    run_id: str = "",
    source: str = "worker",
    log: Any = _log,
) -> tuple[int, dict]:
    """段末摘要入账（覆盖写）+ 盖章 / 下一段触发 → `(状态码, 响应体)`。

    两条入口（云机 POST / 派发器拉）共用这一份：`end_it_reached` 的盖章与「滚下一段」是
    事故高发面（旧包/旧会话把新包封死：`_end_seal_ok` 的三项对照），不能一条有一份。
    """
    try:
        body = _authority_body(body, course=course, run_id=run_id, source=source)
    except AuthorityConflictError as e:
        log(f"拒收（权威不符）：{e}")
        return 409, {"error": str(e), "status": "conflict", "course": course, "run_id": run_id}
    res = hub.store_offline_result(course, body)
    # T6：跑满自报 = 把当前包记为 completed ⇒ 该课**不可再领**（U6），直到人重导包（新 sha）。
    if res.get("end_it_reached"):
        sealed, why = end_seal_ok(hub, course, body)
        res["completed_sealed"] = sealed
        if sealed:
            hub.note_offline_completed(course)
        elif why:
            log(
                f"段末自报不盖章（course={course} run={res['run_id']}）：{why}"
            )
        # ★ J2：盖章照旧（`completed_pack_sha` 是「这份包别再发了」的唯一机制），另起一条
        # 「滚下一段」的腿：段末事件**主动**触发一次控制台出包（与 claim 无关）。只有课末
        # 不发下一段；未知也不发（宁可少滚一次，也不要在课末白烧一次导出）。
        if sealed:
            kind = seg_end_kind(hub, course, str(res.get("seg_end_kind") or ""))
            res["seg_end_kind"] = kind
            if kind in (SEG_END_OPEN, SEG_END_SEGMENT):
                trigger_next_segment(hub, course, kind, log=log)
            else:
                log(
                    f"{seg_kind_text(kind)}完成（course={course}）"
                    + (
                        "——不触发下一段"
                        if kind
                        else "——段末种类不可知（旧云机 + 包读不到计划），不触发下一段"
                    )
                )
    log(
        f"OFFLINE course={course or '-'} result run={res['run_id']} it{res['it_end']}"
        + ("（跑满）" if res.get("end_it_reached") else "")
        + (f"（{source}）" if source != "worker" else "")
    )
    return 200, res


def seg_end_kind(hub: Any, course: str, reported: str) -> str:
    """段末三态：优先云机自报，**缺席时从当前包里的计划反推**（P1-8 兜底）。

    为什么要反推：三态是 v2 新键，而它在父/子进程之间走控制文件 —— **在飞的 `code.zip`
    是导出那一刻的快照**，老子进程不认新键 ⇒ 自报里那个键静默缺席。若此时按旧行为
    （不触发），无限课的段末就永远滚不起来；按课末处理更坏（课被提前封死）。
    包里 `plan.json` 与云机读的是**同一份**只读事实，不造第二份真相。
    """
    if reported:
        return reported
    try:
        pack = hub.task_pack_path(course)
    except ProtocolError:
        return ""
    if not pack.is_file():
        return ""
    return pack_seg_end_kind(pack)


def trigger_next_segment(hub: Any, course: str, kind: str, *, log: Any = _log) -> None:
    """段末（非课末）⇒ 请控制台出下一段（**事件驱动**，与 claim 无关）。

    与 `task_pack` 那两本触发账共用同一套判据（`auto_handoff_decision`：已知失败 / 上界 /
    节流）——否则段末这条腿会绕过「导出失败就不再烧快照」的纪律。
    """
    decision = auto_handoff_decision(course)
    if decision != "trigger":
        log(f"{seg_kind_text(kind)}完成（course={course}）——不触发下一段（{decision}）")
        return
    ok, note = trigger_auto_handoff(course)
    if ok:
        note_auto_handoff_trigger(course)
    log(
        f"{seg_kind_text(kind)}完成（course={course}）——"
        + (f"已触发控制台出下一段（{note}）" if ok else f"想触发控制台出下一段，但没成（{note}）")
    )


def seg_kind_text(kind: str) -> str:
    """段末三态的人读一句话（日志与读面同一口径；`\"\"` = 未知）。"""
    return {
        SEG_END_COURSE: "课末",
        SEG_END_OPEN: "开区间本段",
        SEG_END_SEGMENT: "本段",
    }.get(kind, "本段（段末种类未知）")


def end_seal_ok(hub: Any, course: str, body: dict) -> tuple[bool, str]:
    """段末自报能不能给「当前包」盖 completed 章（★P1-7 / R3-f）。只拦**有反证**的情形。

    判据 = 当前任务包的身份（`run_id` / `plan_sha256`，都是包里已有的字段）与自报逐项对照。
    包读不到 / 自报缺该项 ⇒ **不判**（照旧盖章 —— 不制造新的失败态）；只有「两侧都有值且
    不等」才拒章，那正是要拦的：旧包/旧会话的 `end_it_reached` 把**新包**封成 completed。
    """
    try:
        pack = hub.task_pack_path(course)
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
        return False, f"自报 plan_sha256={got_plan[:12]}… ≠ 当前包 {pack_plan[:12]}…"
    return True, ""


def trigger_pack_export(hub: Any, course: str) -> dict:
    """「请控制台写配置并导包」的**唯一实现**（claim 的缺包分支与 push 派发器共用，评审 P0-4）。

    返回 `{decision, state, ok, note}`：

      * `decision` = `auto_handoff_decision` 的原值（`trigger` / `throttled` / `failed` / …）；
      * `state` = 机器可读状态（`triggered` / `failed` / `unknown`）——判据**不许**挂在
        `note` 的措辞上（`tests/helpers/text_asserts.py` 的口径）；
      * `ok` = 本次是否真的把「出包」请求递到了控制台（`throttled`/`failed` 时为 False）；
      * `note` = 给人看的一句话。

    调用方负责「先记 `pending_export`」（`hub.begin_pending_export`）：那是「有人在导包」的
    软态，与触发是两件事（这里只做「判节流 + 记账 + 调控制台」）。
    """
    decision = auto_handoff_decision(course)
    if decision == "trigger":
        note_auto_handoff_trigger(course)
        ok, why = trigger_auto_handoff(course)
        note = (
            "已请控制台写配置并导包（导出约需数分钟）"
            if ok
            else f"想替这门课导包，但控制台不可达/不接受（{why}）——请到控制台点一次「导出任务包」"
        )
        return {"decision": decision, "state": "triggered", "ok": bool(ok), "note": note}
    if decision == "failed":
        reason, _at = auto_handoff_failure(course)
        return {
            "decision": decision,
            "state": "failed",
            "ok": False,
            "note": f"上次导出**失败**：{reason}——本窗口不再重试（控制台修好后再点「导出任务包」）",
        }
    if decision == "throttled":
        return {
            "decision": decision,
            "state": "unknown",
            "ok": False,
            "note": "本窗口已触发过导包（节流窗内不重复触发；控制台是否真在跑未确认）",
        }
    return {
        "decision": decision,
        "state": "unknown",
        "ok": False,
        "note": "连续触发导包仍没有包——请到控制台检查这门课的自动交接（导出失败？）",
    }


__all__ = [
    "AuthorityConflictError",
    "end_seal_ok",
    "ingest_offline_artifact",
    "ingest_offline_result",
    "seg_end_kind",
    "seg_kind_text",
    "trigger_next_segment",
    "trigger_pack_export",
]
