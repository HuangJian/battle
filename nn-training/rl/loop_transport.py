"""loop_transport —— 传输/发布层的**唯一实现**（2026-09-23 从 rl/loop_steps.py 拆出，S4）。

loop_steps 里原来混着两种东西：`TrainingSteps` mixin（单轮结算与梯度步）**与**这
一组模块级自由函数——课程/rollout 源解析、transport 选择、hub 推送、节点 failover、
kickstart 系数、远端可重试异常集合。它们没有一个是方法，却被塞在同一个 2328 行文件里，
只因为历史上一次「从 loop_core.py 拆出」时按大小切、没按职责切。

本模块承载**传输与发布策略**这一职责；`rl/loop_steps.py` 只留 mixin，并对本模块做门面
re-export（既有 `from rl.loop_steps import X` 调用点与测试的 monkeypatch seam 因此不变）。

⚠ **DI seam 的命名空间（勿静默踩）**：本模块的 `dist_common` / `_push_submit` /
`_push_wait_result` 是**测试用的注入点**，patch 目标随实现走——测本模块自由函数的用例
patch `rl.loop_transport.*`；测 `TrainingSteps` **方法**的用例仍 patch `rl.loop_steps.*`
（两边各自解析自己的模块全局；同名的两个 seam 是有意分开的，见 tests/test_layering.py）。
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import dist_common
from common.protocol import (
    JobFailedError,
    ProtocolError,
    RetryableError,
    coef_active,
)
from remote.push_client import submit_job as _push_submit
from remote.push_client import wait_result as _push_wait_result


class BundleExportedError(Exception):
    """全离线任务包已写出（`--export-bundle`）：本轮不训练、不等待，干净退出。

    异常而不是返回值：导出路径穿过发布链（`_remote_ppo`）的中间层，那几层的返回值语义
    是「云回传结果」；用一个专用异常把「停在这里」传回去，比让每层都判断一个 flag 清楚。
    它与 `SmokeVoidRoundError` 不同：冒烟是「跑完了但作废」，这个是「根本没跑」。
    """


class SmokeVoidRoundError(Exception):
    """冒烟回显结果（result.smoke=true，remote/worker.py --echo）。

    本轮已按正常流程走完 发布→领取→三重校验落位，但权重是 init 回显而非真
    PPO 产出——作废本轮：不写 iteration 事件（last_completed_iter 续跑锚点
    不受影响）、it 不前进。loop_lifecycle.run 捕获：--smoke 干净退出，真训练原地
    重试同一迭代（作废不是故障，不计失败连击）。
    """


def _course_cf_tunnel(args: Any) -> tuple[str | None, str | None]:
    """本轮**真正生效**的隧道协议/边缘 IP → 写进 iteration 事件的 wire.protocol/edge_ip。

    为什么训练侧要读它：M1 的开关住在 rl-config（`rl.cf_*` + `courses.<stem>.cf_*` 覆盖），
    真正拉起 cloudflared 的是控制台——训练进程不读它就只能在指标里记 None，事后无法
    按选项分组统计（plan §1.4 的硬要求）。

    优先级：CLI 参数 > `courses.<stem>.cf_*` > `rl.cf_*` > None（不记）。
    与 `_rollout_source` 同口径读 rl-config：选项住 rl-config，**永不进 curricula**
    （D14 血缘）；读不到一律返回 None（旧行为，不炸训练）。
    """
    proto = str(getattr(args, "remote_cf_protocol", "") or "") or None
    edge = str(getattr(args, "remote_cf_edge_ip", "") or "") or None
    if proto and edge:
        return proto, edge  # 两个都由 CLI 给定 → 不必读盘
    try:
        from train.loop_util import course_key_from_path

        stem = course_key_from_path(str(getattr(args, "course_path", "") or ""))
    except Exception:
        stem = ""
    if not stem:
        return proto, edge
    try:
        cfg = dist_common.load_dist_config() or {}
        course = (cfg.get("courses") or {}).get(stem) or {}
        rl = cfg.get("rl") or {}
        proto = proto or str(course.get("cf_protocol") or rl.get("cf_protocol") or "") or None
        edge = edge or str(course.get("cf_edge_ip") or rl.get("cf_edge_ip") or "") or None
    except Exception:
        pass
    return proto, edge


#: `--rollout-src` 的合法值（auto = 按 rl-config 解析，缺省 local）。
ROLLOUT_SRCS: tuple[str, ...] = ("auto", "local", "node", "run")


def _rollout_source(args: Any) -> str:
    """本轮 rollout 在哪跑：`local`（历史行为）| `node`（M3 整轮上云）| `run`（整段上云）。

    优先级：CLI `--rollout-src`（非 auto）> `courses.<stem>.rollout_src` > `rl.rollout_src`
    > local。与 `_course_cf_tunnel` 同口径读 rl-config：选项住
    rl-config，**永不进 curricula**（D14 血缘），读不到一律 local（旧行为，不炸训练）。

    ★ `run`（2026-09-19 离线训练模式）是**声明**：真正的段长在 `_run_segment_iters`
    （`run_iters`），两者都进了 `ROLLOUT_SRCS` —— 只声明 `run` 而不给段长是配置错误，
    在 `step_course_iter` 里响亮拒跑（静默退化成「本地采样」正是最难查的那类）。

    ⚠ 写进 iteration 事件的 wire.rollout_src 用的是本函数的返回值，**不是** args 字面量
    —— 否则 auto 会记成 "auto"，事后无法按「实测在哪跑」分组。
    """
    mode = str(getattr(args, "rollout_src", "") or "auto")
    if mode and mode != "auto":
        if mode not in ROLLOUT_SRCS:
            raise SystemExit(
                f"[run_rl] 未知 --rollout-src {mode!r}（只接受 {'|'.join(ROLLOUT_SRCS)}）"
            )
        return mode
    try:
        from train.loop_util import course_key_from_path

        stem = course_key_from_path(str(getattr(args, "course_path", "") or ""))
    except Exception:
        stem = ""
    if not stem:
        return "local"
    try:
        cfg = dist_common.load_dist_config() or {}
        course = (cfg.get("courses") or {}).get(stem) or {}
        rl = cfg.get("rl") or {}
        val = str(course.get("rollout_src") or rl.get("rollout_src") or "") or "local"
    except Exception:
        return "local"
    if val not in ROLLOUT_SRCS or val == "auto":
        return "local"
    return val


#: 半离线段等待预算缺省（秒）：一次 kind=run job 覆盖多轮，节点要跑完才回传。
#: 这里只是 hub 侧的**等待上限**，不是训练预算（训练预算在计划里：budget_sec / iters）。
#: 8h ≈ Kaggle 单会话上限：超过它还没回，几乎只能是节点挂了——响亮超时好过默默挂着。
RUN_WAIT_DEFAULT_SEC = 8 * 3600.0


def _run_segment_iters(args: Any) -> int:
    """半离线段长：一次 `kind=run` job 覆盖几轮（0 = 关；<0 = 直到课程末尾）。

    优先级与 `_rollout_source` 同口径：CLI `--run-iters` > `courses.<stem>.run_iters` >
    `rl.run_iters` > 0。**缺省 0 = 关**（历史行为逐字节不变；要半离线才显式开）。
    选项住 rl-config，永不进 curricula（D14 血缘）。

    语义（用户 2026-09-17 定）：一次领走 = 整段——节点收到（课程 + 初始权重 + 代码 +
    计划）后即使 hub 彻底失联也能自己跑完，逐轮权重/指标落在产物目录（Kaggle
    /kaggle/working、Colab Drive）+ 可打包下载。
    """
    n = int(getattr(args, "run_iters", 0) or 0)
    if n:
        return n
    try:
        from train.loop_util import course_key_from_path

        stem = course_key_from_path(str(getattr(args, "course_path", "") or ""))
    except Exception:
        stem = ""
    if not stem:
        return 0
    try:
        cfg = dist_common.load_dist_config() or {}
        course = (cfg.get("courses") or {}).get(stem) or {}
        rl = cfg.get("rl") or {}
        return int(course.get("run_iters") or rl.get("run_iters") or 0)
    except Exception:
        return 0


def _run_wait_sec(args: Any) -> float:
    """半离线段的等待上限（秒）：CLI `--run-wait-sec` > `rl.run_wait_sec` > 8h。"""
    v = float(getattr(args, "run_wait_sec", 0.0) or 0.0)
    if v > 0:
        return v
    try:
        cfg = dist_common.load_dist_config() or {}
        v = float((cfg.get("rl") or {}).get("run_wait_sec") or 0.0)
    except Exception:
        v = 0.0
    return v if v > 0 else RUN_WAIT_DEFAULT_SEC


def _gpu_push_nodes(remote_token: str) -> list[dict]:
    """GPU push 节点清单（HUB 推模式，DECISIONS §340 补充 4）：
    环境变量 REMOTE_PUSH_NODE（冒烟预演注入本机伪节点，优先）→
    rl-config `nodes[].gpu_push=true`（真 GPU 机器，URL 指向其 worker_server 隧道）。

    ★ **课程与 worker 节点正交**（2026-09-19 用户口径：「课程任务与 worker 节点互相正交！
    所有 worker 都可能接到在训的课程任务，不管它是哪个课程的」）：这里**不再**按课程的
    `push_node_url` 过滤——登记在册的节点就是全部候选（hub 中介派发按队列顺序挑空闲的那个，
    直推按序 failover）。「把某门课钉到某台机器」不存在：课程定义任务，节点提供算力。
    env 注入是**独占**的显式覆盖（冒烟预演）：设了它就只有它，登记节点一律不参与——
    否则伪节点一失败，failover 会把预演的 job 送去真 GPU 上跑（「冒烟不该碰真训练」）。"""
    out: list[dict] = []
    env_node = os.environ.get("REMOTE_PUSH_NODE")
    if env_node:
        return [{"url": env_node.rstrip("/"), "authKey": remote_token}]
    cfg = dist_common.load_dist_config() or {}
    nodes = [n for n in cfg.get("nodes") or [] if n.get("gpu_push") and n.get("enabled", True)]
    for n in nodes:
        out.append({"url": str(n.get("url", "")).rstrip("/"), "authKey": str(n.get("authKey", ""))})
    return out


#: `--remote-transport` 的合法值（auto = 登记在册的 gpu_push 节点 > hub pull）。
#: 2026-09-19 起**课程与节点正交**：不再有「本课 push_node_url」这层按课程过滤。
#: `hubpush`（2026-09-18）= 发布到 hub、由 **hub 推给**登记在册的 GPU worker——训练侧不直连
#: 节点，于是队列/空闲判定/超时回落/多课程公平全住在一处（这就是它相对 `push` 的价值）。
REMOTE_TRANSPORTS: tuple[str, ...] = ("auto", "pull", "push", "hubpush")


def _hub_push_opt_in() -> bool:
    """是否**允许** hub 中介推送（rl-config `rl.hub_push`；**缺省 = 允许**）。

    auto 下它是唯一切到 `hubpush` 的开关（CLI `--remote-transport hubpush` 则无条件切）：
    「push 经不经 hub」是**部署事实**（机群在 hub 后面跑还是隧道直推），不是每轮要重算的
    东西 ⇒ 住 `rl.hub_push`（全局一个，2026-09-19 起**不再**按课程读 `courses.<课>.hub_push`：
    那是「把某门课钉到某条派发路」的耦合，用户口径是课程与节点正交）。

    缺省为什么是 **True**（用户 2026-09-19：「配了节点就默认走 hub 中介派发」）：hub 在
    新模型下**始终在线**（pull 本来就要求它在），而 hub 派发把队列顺序 / 空闲判定 / 超时
    回落 / 多课程公平全集中在一处。显式 `"hub_push": false` 回到直推节点。
    需要 `hub_url` + token 齐备才真生效（`resolve_hub_push`）；缺则维持直推，不炸训练。
    """
    try:
        cfg = dist_common.load_dist_config() or {}
        rl = cfg.get("rl") or {}
        v = rl.get("hub_push")
        return True if v is None else bool(v)
    except Exception:
        return True


def resolve_hub_push(
    mode: str,
    hub_url: str,
    token: str,
    opt_in: bool,
) -> bool:
    """传输裁决：本轮是否走 **hub 中介推送**（→ 发布带 `dispatch="push"` + 等 hub 回传）。

    `--remote-transport` 与配置各有分工：
      · `hubpush` → 无条件走（缺 hub_url/token 响亮 SystemExit，**不静默回落 pull**：
        配置写错了却“训练看着正常”是这篇仓里最贵的一类错误）；
      · `push`  → **不走**（“直推”是显式选择：云机没配 hub、或本机伪 GPU 冒烟）；
      · `pull`  → 不走；
      · `auto`  → `opt_in` 且 hub_url 与 token 齐备时走，否则保持历史行为（gpu_nodes 直推）。
    """
    if mode == "hubpush":
        if not hub_url or not token:
            raise SystemExit(
                "[run_rl] --remote-transport hubpush 需要 --remote-hub-url 与 --remote-token"
                "（hub 由它推给 GPU worker；直推云机用 --remote-transport push）"
            )
        return True
    if mode in ("push", "pull"):
        return False
    if mode != "auto":
        raise SystemExit(
            f"[run_rl] 未知 --remote-transport {mode!r}（只接受 {'|'.join(REMOTE_TRANSPORTS)}）"
        )
    return bool(opt_in and hub_url and token)


def resolve_transport(
    mode: str,
    hub_url: str,
    token: str,
    gpu_nodes: list[dict],
) -> list[dict]:
    """传输裁决（2026-09-15）→ **生效的 gpu_push 节点清单**（空 = 走 hub pull）。

    auto（默认）= 登记在册的 gpu_push 节点就是全部候选（课程与节点正交，2026-09-19）；
    想钉死一条路就用 `--remote-transport pull|push|hubpush` 显式压过——控制台已不再
    代写任何按课程的传输旋钮（课程定义任务，节点提供算力，二者不互相绑定）。

    非法组合响亮 SystemExit（与 require_remote_transport 同风格：绝不静默回落）。
    """
    if mode == "auto":
        return gpu_nodes
    if mode == "pull":
        if not hub_url or not token:
            raise SystemExit(
                "[run_rl] --remote-transport pull 需要 --remote-hub-url 与 --remote-token"
                "（本地 hub 场景：控制台 local preset 会注入本机 hub）"
            )
        return []
    if mode == "hubpush":
        # hub 中介推送：训练侧**不直连节点**（清单交给 hub 的登记表），校验留给
        # resolve_hub_push（它才需要 hub_url/token）。
        return []
    if mode == "push":
        if not gpu_nodes:
            raise SystemExit(
                "[run_rl] --remote-transport push 但没有可用的 gpu_push 节点——"
                "检查 rl-config nodes[].gpu_push（enabled）与 REMOTE_PUSH_NODE"
            )
        return gpu_nodes
    raise SystemExit(
        f"[run_rl] 未知 --remote-transport {mode!r}（只接受 {'|'.join(REMOTE_TRANSPORTS)}）"
    )


def require_remote_transport(
    hub_url: str,
    token: str,
    gpu_nodes: list[dict],
    env_push: str | None = None,
) -> None:
    """PPO 传输可用性门（单一 PPO 路径；纯 push 不再强制本地 hub-server/cloudflared）。

    - 鉴权：token 非空，或 env 节点，或任一 gpu_push 节点带 authKey；
    - 传输：hub_url（pull）**或** gpu_nodes/env（push 直推云机隧道）。
    不满足 → SystemExit（响亮，不静默回落）。
    """
    env = env_push if env_push is not None else os.environ.get("REMOTE_PUSH_NODE")
    if not token and not env and not any(n.get("authKey") for n in gpu_nodes):
        raise SystemExit(
            "[run_rl] PPO 需要 --remote-token"
            "（或 rl-config gpu_push 节点 authKey / REMOTE_PUSH_NODE）"
        )
    if not hub_url and not gpu_nodes and not env:
        raise SystemExit(
            "[run_rl] PPO 需要 remote_hub_url（pull）"
            "或 rl-config nodes[].gpu_push（push 直推云机隧道）——"
            "纯 push 模式不依赖本地 hub-server/cloudflared"
        )


def _push_over_nodes(
    nodes: list[dict],
    start_i: int,
    step: Callable[[int, dict], Any],
    log: Any,
) -> Any:
    """逐节点执行 `step(i, node)`；单节点失败换下一个；全失败时**抛出**（确定性原因优先）。

    为什么做成模块级共用：节点 failover 的**判决**只该有一份。它现在有三个调用方——
    组合入口 `_push_job_round`（节点轮/整段）与拆相后的 `_push_submit_first`（发布）/
    `_push_fetch`（等待）——三份各自演化过的 failover 语义是没法再对齐的（这个仓库已经
    因为「同一件事两条实现」付过几次学费）。

    确定性节点失败（410：节点说这个 job 在这台机器上跑不成）单独记一笔：所有节点都倒了
    时把它**原样抛出**，而不是包成 RetryableError（2026-09-17）。否则「bun 装不上」会被
    上层当瞬时失败重试 3 次（每次重新 push + 等满超时）。
    """
    last: Exception | None = None
    node_failed: JobFailedError | None = None
    for i in range(start_i, len(nodes)):
        url = nodes[i]["url"]
        try:
            return step(i, nodes[i])
        except JobFailedError as e:
            # 终局：节点已判定跑不成（原因在 e 里）。换下一个节点仍值得一试（另一台
            # 可能有 bun），但全部节点都倒时得把**原因**带上去（见下方 raise）。
            last = e
            node_failed = e
            log(f"[run_rl] push: 节点 {url} 报确定性失败（{e}）——尝试下一节点")
        except Exception as e:  # 单节点失败换下一个（含确定性拒绝）
            last = e
            log(f"[run_rl] push: 节点 {url} 失败（{type(e).__name__}: {e}）——尝试下一节点")
    if node_failed is not None:
        raise node_failed
    raise RetryableError(f"push 全部节点失败: {last}")


def _push_job_round(
    nodes: list[dict],
    manifest: dict,
    jid: str,
    payload_bytes: bytes,
    code_bytes: bytes,
    args: Any,
    timeout_sec: float,
    log: Any,
    blobs: dict | None = None,
    ts_code_bytes: bytes | None = None,
) -> dict:
    """（组合入口）按序向 push 节点提交 job 并等待结果；单节点失败换下一个，全部失败抛
    RetryableError（loop 原地重试同迭代）。--smoke 经 X-Smoke-Echo 头触发节点侧
    冒烟回显（不跑 PPO，结果带 smoke 标记 → 共享尾部作废本轮）。

    blobs（M2 B3）：opt/ref raw 字节（读自 job 目录）——push_client 判节点缓存命中，
    只传未命中的那些。

    ts_code_bytes（M3）：TS 运行时 zip（kind=iter 才非 None）；同样判节点缓存，只在
    未命中时随 body 上传（sha 不变则整段腿只传一次）。

    R2c-3 之后它退为**薄组合**：failover 循环在 `_push_over_nodes`，提交/等待两个原语分别
    是 `_push_submit` / `_push_wait_result`——与三相路径（`_push_submit_node` + `_push_fetch`）
    同源，于是「换节点」的行为只有一处定义。本函数的入参是**内存里的字节**（调用方已经
    读好），三相路径则是从 job 目录重读——这是两者唯一的区别。
    """

    def step(_i: int, node: dict) -> dict:
        url, key = node["url"], node.get("authKey", "")
        # M0：submit_job 返回本轮实测传输账（body/payload/code 字节 + 上传秒）
        submit_wire = _push_submit(
            url,
            key,
            manifest,
            payload_bytes,
            code_bytes,
            blobs=blobs,
            ts_code_zip=ts_code_bytes,
            echo=bool(getattr(args, "smoke", False)),
            log=log,
        )
        log(f"[run_rl] push: job {jid} 已提交 -> {url}（等待 GPU 完成）")
        result = _push_wait_result(url, key, jid, timeout_sec=timeout_sec, log=log)
        if isinstance(submit_wire, dict) and isinstance(result, dict):
            # 挂在结果上随返回一路上浮（_wire_from_result 消费）——不改 result 的
            # 校验字段，纯 additive。
            result["wire_hub"] = submit_wire
        return result

    return cast(dict, _push_over_nodes(list(nodes), 0, step, log))


def _kickstart_ref_payload(args: Any) -> tuple[str, str]:
    """BC ref 权重文件 → (base64, sha256)。缺失响亮失败（缰绳无尺子＝静默裸奔，
    不可接受）。仅 kickstart 激活路径调用。"""
    import base64

    path = str(getattr(args, "bc", "") or "")
    if not path or not Path(path).exists():
        raise SystemExit(
            f"[run_rl] kickstart_ref 要求课程 bc 权重存在（ref 尺子）：{path!r}——检查课程 bc 路径"
        )
    raw = Path(path).read_bytes()
    return base64.b64encode(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()


def _wire_from_result(
    result: dict,
    *,
    is_push: bool,
    pack_sec: float | None = None,
    cfg: dict | None = None,
) -> dict:
    """M0 统一计量：把传输层实测汇总成 iteration 事件的 `wire` 子字典。

    数据来源（两半各自实测，互不覆盖）：
      * worker 侧 `result["wire"]`：payload_bytes / 各阶段秒拆分 / result_bytes；
      * hub 侧 `result["wire_hub"]`：push = submit_job 实测 body_bytes/upload_sec；
        pull = hub-server 实测 sent_bytes/recv_bytes（随 /jobs/{id}/result 带回）。
    缺键一律 None（旧 worker / 旧 hub = 旧行无键，不破兼容，additive）。
    """
    _w = result.get("wire")
    w: dict = _w if isinstance(_w, dict) else {}
    _h = result.get("wire_hub")
    h: dict = _h if isinstance(_h, dict) else {}
    c = cfg or {}
    return {
        "up_bytes": (h.get("body_bytes") if is_push else h.get("sent_bytes")),
        "up_sec": (h.get("upload_sec") if is_push else None),
        "pack_sec": pack_sec,
        "down_bytes": (None if is_push else h.get("recv_bytes")),
        "down_sec": None,
        "blobs_miss": w.get("blob_miss"),
        "protocol": c.get("protocol"),
        "edge_ip": c.get("edge_ip"),
        "slim": c.get("slim"),
        "rollout_src": c.get("rollout_src"),
        # worker 侧拆分原样挂上（观测用；plan §2.2 未把它写进 hub 字典，但两半
        # 互不覆盖，合起来才是完整的“秒/字节到哪去了”的答案）。
        "worker": (dict(w) if w else None),
    }


def _remote_forward_agg(agg: dict) -> dict:
    """云 worker result agg → 训练侧结算 agg（与 PPO 引擎 update 的 agg 同口径）。

    2026-09-08 vk1 事故回归：R5§363 之后云端 agg 已携带 `kickstart`（缰绳遥测），
    结算端若丢弃该键 → iteration 行 kickstart 恒 None——worker 缰绳明明在跑、
    可观测性却全盲，整根腿被误判「课程配置未起效」而作废。缺失键按 0.0 兜底
    （旧 worker 无该键，不破迭代行结构）。"""
    return {
        "policy": float(agg.get("policy", 0.0)),
        "value": float(agg.get("value", 0.0)),
        "entropy": float(agg.get("entropy", 0.0)),
        "kl": float(agg.get("kl", 0.0)),
        "mean_ret": float(agg.get("mean_ret", 0.0)),
        "kickstart": float(agg.get("kickstart", 0.0) or 0.0),
        # demo 混 batch 遥测（同 kickstart additive；旧 worker 无此键 → 0.0）。
        "demo_bc": float(agg.get("demo_bc", 0.0) or 0.0),
    }


def kickstart_warn_kind(
    *, kick_on: bool, smoke: bool, agg_kickstart: float, kick_coef: float
) -> str:
    """云端 kickstart 遥测为 0 时的定性：'warn' | 'expired' | 'ok'。

    2026-09-08 vk1 事故的启动协议补丁只看了"遥测是不是 0"，没看"系数是不是
    已到期"——x2-start it31 起系数按 0.5**30<NEGLIGIBLE_COEF 正常归零、训练侧
    不再附 ref（loop_transport kick_live，2026-09-23 前住 loop_steps），worker 老实回 0 却被判"未执行缰绳"
    误报。系数已不活跃时的 0 是预期行为（'expired'），只在系数仍活跃却无
    遥测时判 'warn'（旧代码/会话钉住的真事故）。
    """
    if not kick_on or smoke:
        return "ok"
    if float(agg_kickstart) != 0.0:
        return "ok"
    if not coef_active(kick_coef):
        return "expired"
    return "warn"


def kickstart_coef(args: Any, it: int) -> float:
    """BC 缰绳系数：按 run 原点（it=1）衰减，loop 重启不复位。

    背景：`update_kwargs` 的衰减锚点是 `start_it`，而 resume 会把 start_it
    推到断点——直接复用导致 kk 在每次重启回到满额（c4-margin it31 manifest
    kk=1.0 实证；kb1 复跑 it53 KL 0.315 同源）。warmup 恒 0（kickstart 腿
    validate_args 强制配对），故原点恒为 1，与 intent/goal 的 warmup 语义无交集。
    """
    from run_rl import update_kwargs  # 延迟导入：run_rl 侧持有 loop 入口，顶层互引成环

    return float(update_kwargs(args, it, 1, None)["kl_coef"])


#: 远端 HTTP 状态码里**重试不可能自愈**的几类：请求本身有问题 / token 不对 / IP 被闭锁。
#: 网络抖动、5xx、429 都不在此列（那些该退避重试）。
FATAL_REMOTE_HTTP = (400, 401, 403)


def remote_retryable_exceptions() -> tuple[type[BaseException], ...]:
    """远端 PPO 路径认可的**可重试**远端失败异常集合（只走发布-等待单一路径）。

    `remote.hub_client` 只在远端路径延迟导入（见 `_remote_ppo`），异常类型同理延迟取。

    2026-09-16 x3-step 事故：`HubClientError(RuntimeError)` 不在白名单里 ⇒ hub 把回环
    IP 封掉（403 ip blocked）后，训练主循环连败 5 次被 `loop_core` 的通用兜底直接杀
    进程，而专为远端失败写的「连败 3 次写 ABORT 停腿」一次都没触发；每次重试还重新
    publish 同一 job（账本里同 id 留了 5 条 job_pending）。
    """
    from remote.hub_client import HubClientError

    # JobFailedError（2026-09-17）：节点已回报原因的**确定性**失败。必须在集合里——
    # 否则它会落到 loop_core 的通用兜底（连败即杀进程），专为远端失败写的停腿
    # 判决一行不写。两处调用方都先做 `isinstance(e, JobFailedError)` 判据：确定性
    # 失败不消耗连败配额、不重试，直接 ABORT 停腿。
    return (
        RetryableError,
        ProtocolError,
        OSError,
        TimeoutError,
        HubClientError,
        JobFailedError,
    )


def fatal_remote_http(e: BaseException) -> int:
    """从 HubClientError 的消息里抽状态码；命中「重试无意义」的返回该码，否则 0。

    消息形如 `wait_job: HTTP 403: {"error": "ip blocked"}`（hub_client 统一格式）。
    """
    m = re.search(r"HTTP\s+(\d{3})", str(e))
    if not m:
        return 0
    code = int(m.group(1))
    return code if code in FATAL_REMOTE_HTTP else 0


def _gate_round_shards(
    *,
    local_shards: list,
    rollout_spec: Any,
    exporting: bool,
    it: int,
    it_dir: str,
) -> list:
    """走到远程 PPO 发布前时，**本轮该训的 shard 集**该是什么（纯函数；不合规则 raise）。

    三条分支各有各的硬门（每一条都对应一类真事故）：

    * **本机轮**（`rollout_spec` 空、非导出）：必须真有完整 shard，否则发出去的 job 在
      云上以「payload 缺件」形式失败（错误落在假因上）——挡在发布前。
    * **M3 上云轮**（`rollout_spec` 非空）：本轮 shard 集**必须为空**——非空说明本地
      采样没关干净，云与本机会双份采集（data_fp 漂），拒绝发布。
    * **全离线导出**（`exporting`）：**既不发 job 也不训练**，只是一个打包动作。轮内
      一致性门在这里不适用：traj 目录里有没有历史残留 shard 与本包无关（那几轮不在
      计划范围内），空 shard 也不需要拦。

      最后这条是 2026-09-17 实测出来的：拿一个刚跑过一轮的真课（c6-chip）导包，
      `rollout_spec` 非空 + 残留 shard 命中上一条门 ⇒ `--export-bundle` 直接
      SystemExit、包产不出来——即「任何跑过一轮的课都导不出包」。
    """
    if exporting:
        return []
    if rollout_spec:
        if local_shards:
            raise SystemExit(
                f"[run_rl] remote it{it}: rollout_src=node 但 traj 目录已有本地 shard "
                "——本机采样没关（会双份采集），拒绝发布 iter job"
            )
        return []
    if not local_shards:
        raise SystemExit(
            f"[run_rl] remote it{it}: 无完整 shard（traj {it_dir} 空）——无法发布 job"
        )
    return local_shards
