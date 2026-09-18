"""loop_steps —— TrainingSteps mixin：单轮结算与梯度步（2026-09-02 从 rl/loop_core.py 拆出）。

run_training 迭代体的「采集之后」各阶段：报告结算（stream 拆解 + 日志）、串行
PPO 更新、权重导出与归档、eval 后台 join（延迟化软等待）、iteration 事件落账。

由 TrainingLoop(TrainingSteps, TrainingGuards) 混入；依赖的实例属性在
TrainingLoop.__init__/迭代方法中赋值，此处仅声明类型。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import dist_common
from remote.protocol import (
    JobFailedError,
    ProtocolError,
    RetryableError,
    coef_active,
    find_payload,
)
from remote.push_client import submit_job as _push_submit
from remote.push_client import wait_result as _push_wait_result
from rl.archive import backup_weights
from rl.eval_m1 import read_eval_summary
from rl.events import write_event, write_gate_verdict, write_iteration
from rl.log import log
from rl.modes import _MODE_BACKUP_PREFIX

if TYPE_CHECKING:
    from rl.commit_journal import CommitJournal


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
    不受影响）、it 不前进。loop_core.run 捕获：--smoke 干净退出，真训练原地
    重试同一迭代（作废不是故障，不计失败连击）。
    """


def _course_push_url(args: Any) -> str:
    """本课 push 节点 URL（rl-config `courses.<stem>.push_node_url`；多课同值 = N:1 共享）。

    缺省空 = 沿用旧逻辑（全取 gpu_push 节点）。C1：URL 住 rl-config，永不进
    curricula（否则 course_fp 血缘漂移，D14 熔断误判）。
    """
    try:
        from train.loop_util import course_key_from_path

        stem = course_key_from_path(str(getattr(args, "course_path", "") or ""))
    except Exception:
        return ""
    if not stem:
        return ""
    try:
        cfg = dist_common.load_dist_config() or {}
        url = ((cfg.get("courses") or {}).get(stem) or {}).get("push_node_url") or ""
        return str(url).rstrip("/")
    except Exception:
        return ""


def _course_cf_tunnel(args: Any) -> tuple[str | None, str | None]:
    """本轮**真正生效**的隧道协议/边缘 IP → 写进 iteration 事件的 wire.protocol/edge_ip。

    为什么训练侧要读它：M1 的开关住在 rl-config（`rl.cf_*` + `courses.<stem>.cf_*` 覆盖），
    真正拉起 cloudflared 的是控制台——训练进程不读它就只能在指标里记 None，事后无法
    按选项分组统计（plan §1.4 的硬要求）。

    优先级：CLI 参数 > `courses.<stem>.cf_*` > `rl.cf_*` > None（不记）。
    与 `_course_push_url` 同口径读 rl-config：选项住 rl-config，**永不进 curricula**
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
ROLLOUT_SRCS: tuple[str, ...] = ("auto", "local", "node")


def _rollout_source(args: Any) -> str:
    """本轮 rollout 在哪跑：`local`（历史行为）| `node`（M3 整轮上云）。

    优先级：CLI `--rollout-src`（非 auto）> `courses.<stem>.rollout_src` > `rl.rollout_src`
    > local。与 `_course_cf_tunnel` / `_course_push_url` 同口径读 rl-config：选项住
    rl-config，**永不进 curricula**（D14 血缘），读不到一律 local（旧行为，不炸训练）。

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


def _gpu_push_nodes(remote_token: str, course_push_url: str = "") -> list[dict]:
    """GPU push 节点清单（HUB 推模式，DECISIONS §340 补充 4）：
    环境变量 REMOTE_PUSH_NODE（hub-start 冒烟注入本机伪节点，优先）→
    rl-config nodes[].gpu_push=true（真 GPU 机器，URL 指向其 worker_server 隧道）。

    多课程（plan multi-course-parallel-training P3-W1b）：`course_push_url` 非空时
    只取 URL 与之匹配的节点（N:1 共享天然成立——同 URL 多课同取）；为空时沿用旧逻辑
    （全取）。env 注入永远保留（显式冒烟覆盖，不受课程过滤影响）。
    非空但匹配 0 个的响亮失败在调用方（WARN + manifest 打标，不抛异常）。"""
    out: list[dict] = []
    env_node = os.environ.get("REMOTE_PUSH_NODE")
    if env_node:
        out.append({"url": env_node.rstrip("/"), "authKey": remote_token})
    cfg = dist_common.load_dist_config() or {}
    nodes = [n for n in cfg.get("nodes") or [] if n.get("gpu_push") and n.get("enabled", True)]
    want = (course_push_url or "").rstrip("/")
    if want:
        nodes = [n for n in nodes if str(n.get("url", "")).rstrip("/") == want]
    for n in nodes:
        out.append({"url": str(n.get("url", "")).rstrip("/"), "authKey": str(n.get("authKey", ""))})
    return out


#: `--remote-transport` 的合法值（auto = 历史优先级：本课 gpu_push 节点 > hub）。
#: `hubpush`（2026-09-18）= 发布到 hub、由 **hub 推给**登记在册的 GPU worker——训练侧不直连
#: 节点，于是队列/空闲判定/超时回落/多课程公平全住在一处（这就是它相对 `push` 的价值）。
REMOTE_TRANSPORTS: tuple[str, ...] = ("auto", "pull", "push", "hubpush")


def _course_hub_push(args: Any) -> bool:
    """本课是否要求 **hub 中介推送**（rl-config `courses.<stem>.hub_push` / `rl.hub_push`）。

    auto 下它是唯一切到 `hubpush` 的开关（CLI `--remote-transport hubpush` 则无条件切）：
    「push 要不要经 hub」是部署事实（云机在 hub 后面跑还是隧道直推），不是每轮要重算的
    东西，所以它住配置。读法与 `_course_push_url`/`_rollout_source` 同口径——选项住
    rl-config，**永不进 curricula**（D14 血缘）；读不到一律 False（旧行为，不炸训练）。
    """
    try:
        from train.loop_util import course_key_from_path

        stem = course_key_from_path(str(getattr(args, "course_path", "") or ""))
    except Exception:
        return False
    if not stem:
        return False
    try:
        cfg = dist_common.load_dist_config() or {}
        course = (cfg.get("courses") or {}).get(stem) or {}
        rl = cfg.get("rl") or {}
        return bool(course.get("hub_push") or rl.get("hub_push"))
    except Exception:
        return False


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

    `--remote-transport` 是**唯一**能压过「config 里有本课 gpu_push 节点就静默推云机」
    的开关。控制台 local preset（本机独立 localWorker）必须钉 pull：某课用 push 跑过
    一次后 `courses.<课>.push_node_url` 就留在 rl-config 里，不钉死则 job 全被推去云机，
    本机 worker 永远领不到活——而且日志看起来「训练正常」（最贵的那种错误）。
    auto 保持历史行为零变化（云机 pull/push preset 均不受影响）。

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
                "检查 rl-config nodes[].gpu_push / courses.<课>.push_node_url / REMOTE_PUSH_NODE"
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
    """`--ppo remote` 传输可用性门（纯 push 不再强制本地 hub-server/cloudflared）。

    - 鉴权：token 非空，或 env 节点，或任一 gpu_push 节点带 authKey；
    - 传输：hub_url（pull）**或** gpu_nodes/env（push 直推云机隧道）。
    不满足 → SystemExit（响亮，不静默回落）。
    """
    env = env_push if env_push is not None else os.environ.get("REMOTE_PUSH_NODE")
    if not token and not env and not any(n.get("authKey") for n in gpu_nodes):
        raise SystemExit(
            "[run_rl] --ppo remote 需要 --remote-token"
            "（或 rl-config gpu_push 节点 authKey / REMOTE_PUSH_NODE）"
        )
    if not hub_url and not gpu_nodes and not env:
        raise SystemExit(
            "[run_rl] --ppo remote 需要 remote_hub_url（pull）"
            "或 rl-config nodes[].gpu_push（push 直推云机隧道）——"
            "纯 push 模式不依赖本地 hub-server/cloudflared"
        )


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
    """按序向 push 节点提交 job 并等待结果；单节点失败换下一个，全部失败抛
    RetryableError（loop 原地重试同迭代）。--smoke 经 X-Smoke-Echo 头触发节点侧
    冒烟回显（不跑 PPO，结果带 smoke 标记 → 共享尾部作废本轮）。

    blobs（M2 B3）：opt/ref raw 字节（读自 job 目录）——push_client 判节点缓存命中，
    只传未命中的那些。

    ts_code_bytes（M3）：TS 运行时 zip（kind=iter 才非 None）；同样判节点缓存，只在
    未命中时随 body 上传（sha 不变则整段腿只传一次）。"""
    last: Exception | None = None
    # 确定性节点失败（410：节点说这个 job 在这台机器上跑不成）单独记一笔——
    # 所有节点都倒了时把它**原样抛出**，而不是包成 RetryableError（2026-09-17）。
    # 否则「bun 装不上」会被上层当瞬时失败重试 3 次（每次重新 push + 等满超时）。
    node_failed: JobFailedError | None = None
    for node in nodes:
        url, key = node["url"], node.get("authKey", "")
        try:
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
    """云 worker result agg → 训练侧结算 agg（与 _serial_ppo 的 ppo_update agg 同口径）。

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
    }


def kickstart_warn_kind(
    *, kick_on: bool, smoke: bool, agg_kickstart: float, kick_coef: float
) -> str:
    """云端 kickstart 遥测为 0 时的定性：'warn' | 'expired' | 'ok'。

    2026-09-08 vk1 事故的启动协议补丁只看了"遥测是不是 0"，没看"系数是不是
    已到期"——x2-start it31 起系数按 0.5**30<NEGLIGIBLE_COEF 正常归零、训练侧
    不再附 ref（loop_steps kick_live），worker 老实回 0 却被判"未执行缰绳"
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
    """`_remote_ppo_or_degrade` 认可的远端失败异常集合。

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


class TrainingSteps:
    """单轮结算与梯度步 mixin。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）
    args: Any
    ppo_backend: Any
    update_kwargs: Any
    _model: Any
    _opt: Any
    _device: Any
    _ref_model: Any
    _bc_ref: Any
    _ppo_mod: Any
    _ppo_goal: Any
    _ppo_intent: Any
    _save_weights_json: Any
    _start_it: int
    _traj_dir: Any
    _jsonl_path: Any
    #: R2a：写入账本后把事件并入 `LedgerView` 的钩子（实现在 TrainingGuards）。
    _ledger_apply: Any
    _report: dict
    _stream_meta: dict | None
    _eval_thread: threading.Thread | None
    _eval_gate: threading.Event | None
    #: 2026-09-17：`_eval_gate` 是否由**我们**提前放行（远端 PPO / 上云轮 / stream
    #: 轮）。本机 PPO 真接手时用它判断是否需要收回（R6）；无节点时的饥饿放行不算。
    _eval_gate_early_released: bool
    #: 本轮收官时仍未结束的 eval 尾巴 `(thread, 派发时刻)`——交给下一轮 rollout 收官
    #: 这个自然边界收拢（`_sweep_eval_tail`，不站等）。None = 无尾巴。
    #: 给类级默认值（而不只声明类型）：裸构造的实例（单测脚手架）没有 **init** 赋值。
    _eval_tail: tuple[Any, float] | None = None
    #: 本轮 eval 的派发时刻（尾巴的时间基准；None = 本轮未派发评估）。
    _eval_tail_start: float | None = None
    _rollout_sec: float
    #: M3：本轮节点侧采集墙钟；None = 本轮不在节点采集（本地轮）。必须在这里声明类型
    #: ——只在 _remote_iter 里赋值会被 mypy 推成 float，子类的 `float | None` 就冲突。
    _node_rollout_sec: float | None
    _ppo_sec: float
    #: 云端 worker **自报**的真训练秒（load+chunk+update，不含上传/排队/下载）。
    #: 与 `_ppo_sec`（往返墙钟）分开记——后者打包传输与排队，用于诊断/配额，
    #: 不应当作"训练量"（排队越久越"达标"是错的，且本地采样期间云端空转它看不到）。
    _ppo_cloud_sec: float
    _total_steps: int
    _chunks_n: int
    _agg: Any
    _kl_cum: Any
    _halted_flag: bool
    #: R9：远端连续失败且禁用降级 → 写 ABORT 后停腿（loop 在 _serial_ppo 后检查）。
    _leg_abort: bool
    #: R9：远端连续失败计数（成功即复位）与"已降级本机"标记。
    _remote_fail: int

    def _ensure_local_ppo_stack(self) -> None:
        """本机 PPO 栈（torch/backend/model/opt）——TrainingLoop 实现。

        R9 降级前必调（remote D2 启动路径会把栈置 None）。mixin 只声明，
        真身在 `loop_core.TrainingLoop`。
        """
        raise NotImplementedError

    _remote_degraded: bool
    #: 本 run 的 rotateSeed（loop_core 启动时抽一次；半离线段的计划要带上它，否则云机
    #: 重放出的对集与 hub 不同——`pairs_fp` 会在节点侧跑第一局之前报错）。
    _rotate_seed: int
    _dropped_games: Any
    _load_sec: Any
    _tail_drain_sec: Any
    _waves_n: Any
    #: 本轮主链为 eval 站在外面等的秒数（缺省 0 = 不站等）；类级默认同上。
    _eval_join_sec: float = 0.0
    #: 动态采集（plan/dynamic-rollout-volume）：None = 本轮课程未开该模式。
    _volume_target: int | None
    _volume_collected: int | None
    _volume_capped: bool
    #: bun 可执行文件路径（TrainingLoop 持有；延迟 eval 派发传给评估子进程）。
    bun: str
    #: 上轮节点配置快照（loop 每轮热读；drain 复用最近一份）。
    _last_dist_cfg: Any
    #: 本轮是否评估轮——真实现在 TrainingLoop 本体（loop_core.py），MRO 胜过
    #: 此处占位。body 用 raise 而不用 `...`：万一 MRO 被改坏，响亮失败而不是
    #: 静默返回 falsy 把 eval 全关掉。

    def _eval_on_round(self, it: int) -> bool:
        raise NotImplementedError("TrainingSteps._eval_on_round 被直接调用——MRO 破坏")

    def _hot_reload_course(self, it: int) -> None:
        """课程热加载（§2026-09-13-hot-reload）：每 iter 重读课程文件，rollout 前执行。

        - 语料身份未变（B/C 类编辑）→ 白名单字段写回 args，下一 iter 生效；
          结构绑定字段（bc/workers/out 等）响亮日志「停止→启动后生效」。
        - 语料身份变了（A 类破坏性）→ `course_edit` 事件（控制台横幅）+ 响亮日志，
          **沿用启动配置继续训练**；D13/指纹用启动冻结字节 ⇒ 编辑不进云端 payload。
        - 文件半行写/瞬时坏档 → 沿用旧配置静默等到能读，不打横幅。
        """
        args = self.args
        course = getattr(args, "course_obj", None)
        path = str(getattr(args, "course_path", "") or "")
        if course is None or not path:
            return
        from rl.hot_reload import apply_hot_fields, changed_field_names, plan_reload

        try:
            from rl.config import load_course

            new_course = load_course(path)
        except Exception as e:  # 半行写/编码竞态——下轮重试
            if not getattr(self, "_hr_broken", False):
                log(f"[hot-reload] it{it}: 课程文件暂不可读（沿用启动配置）：{e}")
            self._hr_broken = True
            return
        self._hr_broken = False

        verdict, hot, restart = plan_reload(course, new_course)
        if verdict == "same":
            if getattr(self, "_hr_verdict", "") == "rejected":
                from rl.events import write_event

                write_event(
                    self._jsonl_path,
                    {"event": "course_edit", "verdict": "restored", "it": it},
                )
                log(f"[hot-reload] it{it}: 课程文件已恢复启动配置——拒绝横幅解除")
            self._hr_verdict = "same"
            return

        if verdict == "rejected":
            from rl.config import corpus_identity_fp
            from rl.events import write_event

            new_fp = corpus_identity_fp(new_course)
            if getattr(self, "_hr_reject_fp", "") != new_fp:
                fields = changed_field_names(course, new_course)
                write_event(
                    self._jsonl_path,
                    {
                        "event": "course_edit",
                        "verdict": "rejected",
                        "it": it,
                        "fields": fields,
                        "detail": "语料身份（关卡环境/奖励语义）被编辑",
                    },
                )
                log(
                    f"[hot-reload] it{it}: ⚠ 拒绝热加载——语料身份被编辑"
                    f"（{','.join(fields)}）。沿用启动配置继续训练，"
                    f"编辑内容不进云端 payload；要应用请派生新课程/新关卡"
                    f"（D14 语料血缘不可 mid-run 破坏）"
                )
                self._hr_reject_fp = new_fp
            self._hr_verdict = "rejected"
            return

        from rl.events import write_event

        changed = apply_hot_fields(args, new_course)
        if "max_hours" in changed:
            self._deadline = time.time() + args.max_hours * 3600 if args.max_hours > 0 else None
        write_event(
            self._jsonl_path,
            {
                "event": "course_edit",
                "verdict": "applied",
                "it": it,
                "fields": changed,
            },
        )
        log(
            f"[hot-reload] it{it}: 课程编辑已热应用（{','.join(changed) or '无'}）"
            f"——下一 iter 生效"
            + (
                f"；restart-only 字段（{','.join(r for r in restart)}）停止→启动后生效"
                if restart
                else ""
            )
        )
        self._hr_verdict = "apply"

    def _course_iter(self, it: int) -> None:
        """M1c：每 iter 注入课程配置的加载期上下文（holder）与超参 schedule。

        - holder（reward_context）：reward_fn + gamma/lam + it + 血缘——loaders
          （ppo.engine.load_shard）读取，奖励唯一定义源=课程配置公式；
        - ppo_schedule（按绝对 iter 查表）：lr 改 opt.param_groups（保 Adam
          动量）、epochs/mb 改 args（串行/流式每轮读取）、kl_coef 存 args._kl_coef
          供 update 期注入。
        """
        args = self.args
        course = getattr(args, "course_obj", None)
        if course is None:
            from rl.reward_context import reset as _ctx_reset

            _ctx_reset()
            args._kl_coef = 0.0
            return
        if getattr(self, "_course_reward_fn", None) is None:
            from rl.reward_library import build_reward_fn

            self._course_reward_fn = build_reward_fn(course.reward_spec())
            log(f"[course] reward_fn compiled: formula_len={len(course.reward.formula)}")
        spec = course.reward_spec()
        from rl.reward_context import update as _ctx_update

        _ctx_update(
            reward_fn=self._course_reward_fn,
            gamma=float(getattr(args, "gamma", 0.995)),
            lam=float(getattr(args, "lam", 0.95)),
            it=it,
            identity={"course": course.name, "formula_hash": spec.identity()},
        )
        sch: dict = {}
        if course.ppo_schedule:
            from rl.schedule import resolve_ppo_schedule

            sch = resolve_ppo_schedule(course.ppo_schedule_dicts(), it)
        if "lr" in sch:
            # lr 折算必须落到 args.lr：remote 模式 hub 侧无 _opt，job manifest 的
            # lr 取自 args.lr（publish_job），worker 以 Adam(lr=manifest["lr"])
            # 建优化器——只写 opt.param_groups 会让三段 lr 表在远程路径全程失效。
            # 本地模式再同步 param_groups（保 Adam 动量，原语义不变）。
            args.lr = float(sch["lr"])
            if getattr(self, "_opt", None) is not None:
                self._opt.param_groups[0]["lr"] = args.lr
        if "mb" in sch:
            args.mb = int(sch["mb"])
        if "epochs" in sch:
            args.epochs = int(sch["epochs"])
        kl_coef = float(sch.get("kl_coef", 0.0) or 0.0)
        args._kl_coef = kl_coef
        args._kl_cap = sch.get("kl_cap")  # None = 不覆盖，由 policy.streamKlCap 决定
        # ent_coef（2026-09-11）：None = 用引擎常量 ENT_COEF（0.01）。0.0 是合法值（关掉熵正则），
        # 因此**不能**像 kl_coef 那样 `or 0.0` 兜底——那会把 None 与 0.0 混淆。
        args._ent_coef = sch.get("ent_coef")
        if sch:
            log(
                f"[course] ppo_schedule@it{it}: lr={sch.get('lr')} epochs={sch.get('epochs')} "
                f"mb={sch.get('mb')} kl_coef={kl_coef} kl_cap={sch.get('kl_cap', 'default')} "
                f"ent_coef={sch.get('ent_coef', 'default')}"
            )

    def _write_iter_stats(self, it: int) -> None:
        """M1c：每 iter 落 metrics_stats.jsonl（全维度统计 + 血缘；非致命）。

        M3：本轮 rollout 在云节点时**跳过**——metrics_stats 读的是本地 traj 目录的
        shard，上云轮的 shard 在节点上（跑完即毁），硬跑只会写一份 shards=0 的空统计，
        看起来像「本轮没采样」。逐维度口径改由节点回传的 report（dimMeans/scoreStats）
        承担（已记入 iteration 事件）。
        """
        if getattr(self, "_node_rollout_sec", None) is not None:
            log(
                f"[run_rl] metrics_stats it{it}: 本轮 rollout 在云节点（本地无 shard）——"
                "跳过逐维度统计，改看 iteration 的 report/wire"
            )
            return
        course = getattr(self.args, "course_obj", None)
        if course is None:
            return
        try:
            from rl.metrics_stats import metrics_stats

            identity = {
                "course": course.name,
                "formula_hash": course.reward_spec().identity(),
            }
            rec = metrics_stats(str(self._traj_dir), it=it, identity=identity)
            log(
                f"[run_rl] metrics_stats it{it}: shards={rec['shards']} "
                f"steps={rec['decision_steps']} elapsed_ms={rec['elapsed_ms']}"
            )
        except Exception as e:  # 统计失败不打断训练（warn-only，评审 P1-4）
            log(f"[run_rl] WARN metrics_stats failed (non-fatal): {e}")

    def _log_report(self, it: int, t_rollout: float) -> None:
        """报告结算：stream 报告拆解（eval 线程句柄 / 阶段耗时 / 遥测）与日志行。"""
        report = self._report
        stream_meta = self._stream_meta
        kl_cum = None
        halted_flag = False
        dropped_games = None
        load_sec = None
        tail_drain_sec = None
        waves_n = None
        if stream_meta is not None:
            # 流式评估线程句柄随报告回传（R4）：jsonl 写回前 join。
            self._eval_thread = report.pop("_eval_thread", None)
            _sm = report.pop("_stream")
            self._rollout_sec = _sm["rollout_sec"]
            self._ppo_sec = _sm["ppo_sec"]
            self._ppo_cloud_sec = float(_sm.get("ppo_sec") or 0.0) or self._ppo_sec
            self._total_steps = _sm["steps"]
            self._chunks_n = _sm["chunks"]
            self._agg = _sm["agg"]
            tail_drain_sec = _sm.get("tail_drain_sec")
            kl_cum = _sm.get("kl_cum")
            halted_flag = bool(_sm.get("halted", False))
            dropped_games = _sm.get("dropped_games")
            load_sec = _sm.get("load_sec")
            waves_n = _sm.get("waves")
        elif self._node_rollout_sec is not None:
            # M3 上云轮：t_rollout 含「等待节点跑完 rollout + PPO」的整段墙钟，拿它当
            # rollout_sec 会把 PPO/传输全算进采集（假指标）。用节点自报的采集墙钟。
            self._rollout_sec = float(self._node_rollout_sec)
        else:
            self._rollout_sec = round(time.time() - t_rollout, 1)
        self._kl_cum = kl_cum
        self._halted_flag = halted_flag
        self._dropped_games = dropped_games
        self._load_sec = load_sec
        self._tail_drain_sec = tail_drain_sec
        self._waves_n = waves_n
        log(
            f"[run_rl] rollout it{it}: games={report['games']} winRate={report['winRate']} "
            f"outcomes={json.dumps(report['outcomes'])} "
            f"samples={report['totalSamples']} ticks={report['totalTicks']}"
        )
        if "scoreStats" in report:
            ss = report["scoreStats"]
            log(
                f"[run_rl] score it{it}: mean={ss['mean']:.4f} std={ss['std']:.4f} "
                f"min={ss['min']:.4f} max={ss['max']:.4f}"
            )
        if "dimMeans" in report:
            log(f"[run_rl] dims it{it}: {json.dumps(report['dimMeans'])}")

    def _commit_journal(self) -> CommitJournal:
        """I1 WAL（hy E4/dsf）：PPO 提交序列的 started/done 台账（懒建）。

        路径 <traj>/commit_journal.jsonl。首次创建时扫描 pending——重启后见
        started 无 done 的轮次就大声报（本地轮靠 ppo_ckpt epoch 级断点续跑、
        远端轮按同 it 重发 job），把「上一轮提交到哪了」从事故考古变成一条日志。
        """
        j = getattr(self, "_commit_journal_obj", None)
        if j is None:
            from rl.commit_journal import CommitJournal

            j = CommitJournal(Path(self._traj_dir) / "commit_journal.jsonl")
            self._commit_journal_obj = j
            # R2b：报**在飞集**（不只是 phase/round）——「在等哪个 job、推给了谁」
            # 从「事故考古」变成一条日志（job_id 由 publish 后的 attach 行带上）。
            inflight = j.inflight()
            if inflight:
                detail = ", ".join(
                    f"{r['phase']}@{r['round']}"
                    + (f" jid={r['jid']}" if r.get("jid") else "")
                    + (f" via {r['dispatch']}" if r.get("dispatch") else "")
                    for r in inflight
                )
                log(
                    f"[run_rl] WAL replay-check: {len(inflight)} 个未完成提交 "
                    f"[{detail}] —— 本地轮由 ppo_ckpt 续跑、远端轮重发同 it job（幂等）"
                )
        return j

    def _forensics(self, tag: str) -> None:
        """I1 第 0 步取证（hy E4）：内存/磁盘快照进 run jsonl。

        OOM killer 与写盘失败不留 Python 堆栈、faulthandler 也不落盘——提交边界的
        最后一条 forensics 快照就是临终状态（RSS 峰值贴顶 = OOM 实锤；disk_free ≈ 0
        = 写盘失败实锤）。任何失败只记日志，绝不反杀训练。
        """
        try:
            from rl.forensics import log_snapshot

            log_snapshot(tag, self._jsonl_path, paths=[self._traj_dir])
        except Exception as e:  # 取证失败不阻断训练（诊断手段不是新故障面）
            log(f"[forensics] {tag} 快照失败（{type(e).__name__}: {e}）")

    def _per_stage_quota(self) -> int:
        """逐关严格样本量配额 = `ceil(target_transitions / 关数)`；**0 = 全收 = 老行为**。

        与 `_volume_topup` 共用 `target_per_stage`（同一个数，两侧不会漂），
        也共用 `parse_stages_arg` 解析 `--stages`（见其 docstring：**策略可以不同，
        判断必须同源**——否则会出现「采集说合法、训练说非法」的裂缝）。

        **本方法自带 mode 门**：`target_transitions` 只对 per-tick 有意义
        （intent/goal 不支持动态采集）⇒ 非 per-tick 一律返 0。放在这里而不是各调用点，
        是为了让 serial 与 remote `publish_job` 两条路径**不可能一个 gate 一个不 gate**。

        关集解析**不调 `self._volume_stages()`**：后者定义在 `TrainingLoop` 上，本 mixin
        （`TrainingSteps`）在类型层看不到它（mypy attr-defined）。`--stages` 缺席/不可解析
        ⇒ 返 0（静默降级为全收）；**响亮报错留在 `_volume_topup`** —— 采集侧先跑，
        真配错了在那里就炸，不必在这里重复炸一次。
        """
        if str(getattr(self.args, "mode", "")) != "per-tick":
            return 0
        target = int(getattr(self.args, "target_transitions", 0) or 0)
        if target <= 0:
            return 0
        from rl.volume_waves import parse_stages_arg, target_per_stage

        try:
            n_stages = len(parse_stages_arg(getattr(self.args, "stages", "")))
        except ValueError:
            return 0
        if n_stages <= 0:
            return 0
        return int(target_per_stage(target, n_stages))

    def _serial_ppo(self, it: int) -> None:
        """串行路径（stream_meta 为空）的 PPO 更新：load → chunk → update。

        远程模式（--ppo remote，D11）：改为「打包 → 发布 job → 轮询等待 → 三重校验
        落位」——PPO 本体在云端 worker 执行，hub 只做调度 + 文件搬运（免 torch，D2）。
        语义与 _serial_ppo 完全同构（阻塞等待一轮 PPO 结果后才进入导出/下一轮）。
        """
        if self._stream_meta is not None:
            return
        # 远端：成功即 return；降级后 args.ppo 已改 local → 落到本地路径继续本轮。
        if getattr(self.args, "ppo", "local") == "remote" and self._remote_ppo_or_degrade(it):
            return
        args = self.args
        traj_dir = self._traj_dir
        # 本机 PPO 真在跑：若此前按「远端」提前放行了本机份额，现在收回（R6）——
        # 远端降级的那一轮不得让本机 eval 局与 torch 抢核。
        self._regate_local_eval()
        # I1 WAL（hy E4/dsf）：提交序列 started/done 台账——重启后 pending() 即
        # 「上一轮提交未完成」的账；本地路径由下方 ppo_ckpt epoch 级断点续跑。
        self._commit_journal().start("ppo_local", str(it))
        self._forensics(f"ppo_local_pre it{it}")
        t_ppo = time.time()
        # P1-7：--adv-norm none 时串行路径跳过 global 归一（对照实验）
        # 严格样本量配额（target_transitions 路线）：逐关 ceil(target/关数) 步，
        # 截断在 GAE **之前**（见 ppo/common.trim_shard_arrays）。mode 门在
        # `_per_stage_quota()` 内部——serial 与 remote `publish_job` 共用同一个门，
        # 不可能出现「一条 gate、另一条不 gate」。返 0 时**一个参数都不传** ⇒
        # 老课程 / 非 per-tick 走到这里逐字节不变。
        _psq = self._per_stage_quota()
        _quota_kwargs: dict[str, int] = {"per_stage_quota": _psq} if _psq > 0 else {}
        episodes = self.ppo_backend.load_episodes(
            str(traj_dir),
            float(getattr(args, "gamma", 0.995)),
            float(getattr(args, "lam", 0.95)),
            normalize_adv=getattr(args, "adv_norm", "auto") != "none",
            normalize_ret=bool(getattr(args, "normalize_ret", 0)),
            **_quota_kwargs,
        )
        total_steps = sum(e["obs"].shape[0] for e in episodes)
        chunks = self.ppo_backend.chunk_episodes(episodes, args.mb)
        # I1 取证：load 全量 episodes 是内存峰值点——贴顶即 OOM 候选实锤。
        self._forensics(f"ppo_local_loaded it{it} steps={total_steps}")
        # ppo_backend epoch 级断点续跑：崩溃重启后从最近 checkpoint 继续未完成批次
        if args.mode in ("intent", "goal"):
            agg = self.ppo_backend.update(
                self._model,
                self._opt,
                chunks,
                args.epochs,
                self._device,
                ckpt_path=str(traj_dir / "ppo_ckpt"),
                **self.update_kwargs(args, it, self._start_it, self._ref_model),
            )
        else:
            # BC-anchored kickstart（§363）：缰绳系数走 update_kwargs 衰减语义
            # （warmup_iters=0 由 validate_args 强制，故 it1 即满额）；
            # value_warmup_epochs 忽略（per-tick 无 warmup 概念，R5-warmup 另排）。
            kick = 0.0
            bc_ref = getattr(self, "_bc_ref", None)
            if bc_ref is not None:
                kick = kickstart_coef(args, it)
            # 本机份额提前放行（2026-09-17）：末 `policy.evalLocalEarlyEpochs` 个 epoch
            # 开始即开闸，让预留尾段与本机 PPO 尾部并行（0 = 不加钩子，R6 原语义）。
            _gate_hook = self._local_gate_epoch_hook()
            _upd_kw: dict = {
                "kl_coef": float(getattr(args, "_kl_coef", 0.0) or 0.0),
                "ent_coef": getattr(args, "_ent_coef", None),
                "ref_model": bc_ref,
                "kickstart_kl": kick,
            }
            if _gate_hook is not None:
                _upd_kw["on_epoch_done"] = _gate_hook
            agg = self._ppo_mod.ppo_update(
                self._model,
                self._opt,
                chunks,
                args.epochs,
                self._device,
                ckpt_path=str(traj_dir / "ppo_ckpt"),
                **_upd_kw,
            )
        self._ppo_sec = round(time.time() - t_ppo, 1)  # 本机 PPO：真训练秒 == 往返秒
        self._ppo_cloud_sec = self._ppo_sec
        self._chunks_n = len(chunks)
        self._total_steps = total_steps
        self._agg = agg
        self._kl_cum = agg["kl"] if agg else None  # 串行：单次大更新，均值即累计口径
        # I1：提交序列完成（权重已由 backend 落盘、agg 已结算）——WAL 收口 + 临终对照快照。
        self._forensics(f"ppo_local_post it{it}")
        self._commit_journal().finish("ppo_local", str(it))

    def _abort_node_failure(self, it: int, e: BaseException, *, where: str) -> None:
        """节点已回报原因的**确定性**失败 → 写 ABORT 判决 + 停腿标记。

        2026-09-17（plan/remote-wire-remediation §5.3 缺口）：此前这类失败（bun 装不上 /
        TS 运行时取不到 / argv 非法）在训练侧只表现为 `wait_job` 25 分钟超时——
        「能力缺失」被写成「网络/排队问题」，而且每次重试再白烧一个超时窗口。现在原因
        随 `JobFailedError` 直接到达，判决里写的是真原因（人一眼能修）。

        不重试、不降级：节点缺的是运行时能力，换机/换轮都一样；修完节点重跑同课即可
        （重发同 job 会清失败标记，见 hub_client.publish_job）。
        """
        reason = str(e)[:300]
        write_gate_verdict(
            self._jsonl_path,
            it,
            "ABORT",
            f"{where} 节点确定性失败（原因已随 /jobs/{{id}}/fail 回传）：{reason}",
            decider="loop",
        )
        log(f"[run_rl] GATE ABORT it{it}: {where} 节点报确定性失败——不重试，立即停腿：{reason}")
        self._leg_abort = True

    def _remote_ppo_or_degrade(self, it: int) -> bool:
        """R9（plan/feasibility-map.md §12）：远端失败计数与 **opt-in** 降级。

        2026-09-15 T7：**默认不再自动降级本机**（`remote_degrade_after` 默认 0）。
        原因：① 远端失败应响亮停腿，不静默切到慢一个量级的本机 PPO；
        ② 旧默认 3 会撞上 remote 模式 `ppo_backend=None`（x3-power it1 打穿）。
        控制台启动弹窗提供 opt-in；开启后降级前会 `_ensure_local_ppo_stack()`。

        c6 it50 事故背景：单个 job 三次 1800s 超时、进程最终死在 eval 中途。
        本方法三档处置：
          · 成功 → 计数复位，True（调用方直接 return）；
          · 失败且未达阈值 → 原样抛出（loop 原地重试同一 iter）；
          · 失败达阈值 → `--remote-degrade-after N>0`：懒加载本机栈 + 改
            `args.ppo="local"` + `remote_degrade` 事件；
            **默认 N=0**：连败 3 次写 ABORT 判决后停腿。

        返回 True = 远端已出结果（本轮 PPO 结束）；False = 调用方改走本地路径。
        """
        args = self.args
        try:
            self._remote_ppo(it)
        except remote_retryable_exceptions() as e:
            if isinstance(e, JobFailedError):
                # 节点已回报原因的**确定性**失败（2026-09-17）：不消耗连败配额、不重试
                # ——重试只会再派给另一台同样干不了的机器，或等回同一个 410。
                self._abort_node_failure(it, e, where="远端 PPO")
                raise
            self._remote_fail += 1
            limit = int(getattr(args, "remote_degrade_after", 0) or 0)
            # 401/403/400：token 不对、IP 被 hub 闭锁、或请求本身有问题——**重试多少次
            # 都不会自愈**，继续消耗连败配额只是重复 publish 同一 job 并把停腿拖后
            # （x3-step 事故：5×30s 空转 + 账本 5 条同 id job_pending，最后照样死）。
            fatal = fatal_remote_http(e)
            if fatal:
                write_gate_verdict(
                    self._jsonl_path,
                    it,
                    "ABORT",
                    f"远端 PPO 不可重试失败 HTTP {fatal}——检查 --remote-token 与 hub 日志 "
                    f"AUTH FAIL / BLOCKED 行：{str(e)[:200]}",
                    decider="loop",
                )
                log(
                    f"[run_rl] GATE ABORT it{it}: 远端 HTTP {fatal}（鉴权/闭锁类，非网络抖动）"
                    f"——不再重试，立即停腿"
                )
                self._leg_abort = True
                raise
            log(
                f"[run_rl] remote ppo it{it} FAILED ({type(e).__name__}: {str(e)[:200]}) — "
                f"consecutive={self._remote_fail}"
                + (f"/{limit}" if limit > 0 else "（降级已禁用）")
            )
            if limit <= 0:
                # 显式关闭降级：连败 3 次即停腿（§12-R9 末句：仍失败写 ABORT 行）
                if self._remote_fail >= 3:
                    write_gate_verdict(
                        self._jsonl_path,
                        it,
                        "ABORT",
                        f"远端 PPO 连续失败 {self._remote_fail} 次且 --remote-degrade-after=0",
                        decider="loop",
                    )
                    log(f"[run_rl] GATE ABORT it{it}: 远端不可用且禁用降级——停腿")
                    self._leg_abort = True
                raise
            if self._remote_fail < limit:
                raise  # 未达阈值：按既有语义原地重试（同一 iter，不推进）
            # T7：降级前必须先建好本机 PPO 栈。remote 启动为 hub 省 torch 把
            # backend/model/opt 置 None；直接改 args.ppo=local 会让 _serial_ppo
            # 撞 None.load_episodes（x3-power it1 实锤）。
            self._ensure_local_ppo_stack()
            args.ppo = "local"
            self._remote_degraded = True
            write_event(
                self._jsonl_path,
                {
                    "event": "remote_degrade",
                    "iter": it,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "after_failures": self._remote_fail,
                    "reason": f"{type(e).__name__}: {str(e)[:300]}",
                },
            )
            log(
                f"[run_rl] R9 DEGRADE it{it}: 远端连续失败 {self._remote_fail} 次 —— "
                f"本腿改走本机 PPO（args.ppo=local）。恢复远端需重启训练并修好链路。"
            )
            return False
        self._remote_fail = 0
        return True

    def _remote_ppo(
        self,
        it: int,
        rollout_spec: dict | None = None,
        *,
        plan_bytes: bytes | None = None,
        wait_timeout_sec: float = 0.0,
        export_path: str | Path | None = None,
    ) -> dict:
        """远程 PPO（--ppo remote，D11/D12）：打包 → 发布 job → 轮询等待 → 三重校验落位。

        `rollout_spec` 非空 = **M3 整轮上云**（kind=iter）：本轮不发本地 shard（payload
        只有 init 权重 + 可选 blob），改随 job 发 rollout 规格 + TS 运行时；节点自己跑
        exporter 产 shard 再跑 PPO，回传里带采集报告。除「发什么/收什么」外，发布/传输/
        三重校验/落位/埋点全走同一条链（不复制一份会漂的第二实现）。
        返回 result（调用方 _remote_iter 需要里面的 report）。

        - 打包：本轮 traj it{it} 下 wver 匹配的 shard 集 + init 权重 + 上轮 opt tar；
        - 发布：磁盘 IPC（job 目录 + jsonl job_pending 事件）→ 旁路 hub-server；
        - 等待：阻塞轮询 server 结果（语义与 _serial_ppo 同构）；
        - 校验：init_weights_fp == 当前 args.out 指纹 + data_fp == 本地重算 + commit 一致；
        - 落位：weights_json → args.out（原子 replace）+ opt tar → it{it}/ppo_ckpt_remote。
        hub 全程免 torch（D2）：只做文件搬运 + sha256。
        """
        args = self.args
        it_dir = self._traj_dir
        t_ppo = time.time()
        # 半离线（kind="run"）：plan_bytes 非空 = 本 job 之后还要节点自主把计划跑完。
        if plan_bytes is not None and rollout_spec is None:
            raise SystemExit(
                "[run_rl] kind=run 必须同时带 rollout_spec（本 job 自己那一轮的采集规格）"
            )
        # I1 WAL：远端提交序列（打包→发布→等待→校验→落位）的 started/done 台账；
        # 重启后见 pending ⇒ 按同 it 重发 job（publish 幂等键 = run_id+it+wver，
        # verify_and_land 三重校验防错位落盘）。
        self._commit_journal().start("ppo_remote", str(it))
        self._forensics(f"remote_pre it{it}")
        from remote.hub_client import (
            git_head,
            iter_shard_dirs,
            mark_job_completed,
            pack_code_zip,
            publish_job,
            verify_and_land,
            wait_job,
        )

        hub_url = str(getattr(args, "remote_hub_url", "") or "")
        token = str(getattr(args, "remote_token", "") or "")
        # Push 优先解析（纯 push 不再依赖本地 hub-server / cloudflared）：
        # 有 gpu_push 节点或 REMOTE_PUSH_NODE 时，payload/code 直推云机隧道，
        # hub_url 可缺省。token 仍要（pull 回落 / env 节点鉴权）；配置节点自带 authKey。
        push_url = _course_push_url(args)
        transport = str(getattr(args, "remote_transport", "auto") or "auto")
        gpu_nodes = resolve_transport(transport, hub_url, token, _gpu_push_nodes(token, push_url))
        # hub 中介推送（2026-09-18）：发布带 manifest.dispatch="push"，由 hub 按登记表
        # 推给空闲 GPU worker——训练侧不直连节点，于是「队列顺序/空闲判定/超时回落/
        # 多课程公平」全住在一处。与 gpu_nodes 互斥（resolve_transport hubpush 恒返空）。
        hub_push = resolve_hub_push(transport, hub_url, token, _course_hub_push(args))
        require_remote_transport(hub_url, token, gpu_nodes)
        log(
            f"[run_rl] remote ppo transport={transport} push_nodes={len(gpu_nodes)} "
            f"hub_push={hub_push} hub={hub_url or '-'}"
        )
        job_root = str(getattr(args, "remote_job_root", "") or "") or str(
            Path(args.traj) / "remote-jobs"
        )
        # 本轮应训 shard 集（与 _serial_ppo load_episodes 装载口径一致）；
        # 三条分支的判定抽在 `_gate_round_shards`（纯函数，回归见 test_remote_ppo_gate_fields）。
        local_shards = iter_shard_dirs(
            args.traj,
            it,
            log=(lambda _m: None) if (rollout_spec or export_path is not None) else log,
        )
        shard_dirs = _gate_round_shards(
            local_shards=local_shards,
            rollout_spec=rollout_spec,
            exporting=export_path is not None,
            it=it,
            it_dir=str(it_dir),
        )
        # 课程快照（D13/D14）：课程文件全文 + course_fp = sha256(文件字节)
        course = getattr(args, "course_obj", None)
        if course is None:
            raise SystemExit(
                "[run_rl] --ppo remote 需要课程（--course <name>，D13 课程指针）——"
                "reward 公式/超参/关卡由课程单一事实来源"
            )
        course_path = Path(getattr(args, "course_path", "") or "")
        if not course_path.exists():
            course_path = Path(getattr(args, "course", "") or "")
        if not course_path.exists():
            from rl.config import resolve_course

            course_path = resolve_course(course.name)
        # 课程全文快照 + course_fp = sha256(**启动冻结字节**)——与
        # rl/cmd.course_fp_for_args / rl/loop_core._course_file_fp 同算法同字节源（D14），
        # 否则 CRLF 换行下 read_text 的通用换行翻译会使指纹不一致、血缘断裂。
        # 冻结 = 热加载编辑（含被拒的语料身份改动）永不进 D13 快照/指纹（不泄漏云端）。
        frozen = getattr(args, "course_frozen_bytes", None)
        course_bytes = frozen if frozen else course_path.read_bytes()
        course_text = course_bytes.decode("utf-8")
        course_fp = hashlib.sha256(course_bytes).hexdigest()
        # D14 语义版：corpus_fp = 语料身份（env+reward 解析值哈希，rl/config.corpus_identity_fp）。
        # 与 course_fp（文件血缘）并存进 manifest；worker 装载校验优先比 corpus_fp——
        # 预算/路径类 mid-run 课程编辑只动 course_fp，不再触发整轮 shard 拒收。
        from rl.config import corpus_identity_fp

        corpus_fp = corpus_identity_fp(course)
        # ppo_schedule 解析后值（执行用）——_course_iter 已按 it 折算进 args
        from rl.reward_library import METRICS_VERSION

        commit = git_head()
        # 启动时一次打包源文件 code.zip（避免后继并行修改干扰云端代码一致性）
        if not hasattr(self, "_code_sha256"):
            nn_root = Path(__file__).resolve().parent.parent
            code_zip_path = Path(job_root) / "code.zip"
            cs = pack_code_zip(nn_root, code_zip_path, log=log)
            self._code_sha256 = cs
            self._code_zip_path = code_zip_path
        # code.zip 已包含当前源码快照（含未提交修改），无需 git commit-pin 检查。
        from rl.queue import RUN_ID

        run_id = RUN_ID
        ckpt_remote_path = Path(args.traj) / f"it{it - 1}" / "ppo_ckpt_remote"
        ckpt_remote: Path | None = ckpt_remote_path if ckpt_remote_path.exists() else None
        # BC-anchored kickstart（§363）：缰绳系数走 update_kwargs 衰减（ref 传 None——
        # 系数是纯数学，不需模型）；ref 权重读课程 bc 文件（一次，base64 进 manifest）。
        kick_on = bool(getattr(args, "kickstart_ref", False))
        kick_kl = kickstart_coef(args, it) if kick_on else 0.0
        # 原先只看 kick_on 开关 ⇒ 缰绳早已松开、ref 权重还在每轮空运（~0.36 MB 原始，
        # 是 payload 里可观的一块）。系数退火到阈值以下就不再附字节。
        kick_live = kick_on and coef_active(kick_kl)
        ref_b64, ref_fp = _kickstart_ref_payload(args) if kick_live else ("", "")
        # I1 取证：publish 前的临终对照点（上传大 payload 前的 RSS/磁盘基线）。
        self._forensics(f"remote_pre_publish it{it}")
        # M0：打包（tar.xz + 编码）/ 落盘墙钟——iteration 事件的 wire.pack_sec。
        t_pack = time.time()
        # M2：协议瘦身开关（rl.slim / --remote-slim；默认开）。关 → 逐字节旧行为。
        slim = bool(int(getattr(args, "remote_slim", 1) or 0))
        if rollout_spec:
            # M3：TS 运行时 zip（一次打包，缓存在 self 上——sha 不变就不重打）。
            self._ensure_ts_code(job_root, log=log)
        manifest = publish_job(
            job_root=job_root,
            jsonl_path=str(self._jsonl_path),
            run_id=run_id,
            it=it,
            traj_dir=args.traj,
            shard_dirs=shard_dirs,
            init_weights_path=args.out,
            ckpt_remote_dir=ckpt_remote,
            commit=commit,
            code_sha256=self._code_sha256,
            code_zip_path=self._code_zip_path,
            course=course_text,
            course_fp=course_fp,
            # P4-W2 归属：课程短名（args.course_name，apply_course 挂上）；无课程为 ""。
            course_name=str(getattr(args, "course_name", "") or ""),
            corpus_fp=corpus_fp,
            reward_formula=course.reward.formula,
            formula_hash=course.reward_spec().identity(),
            metrics_version=METRICS_VERSION,
            gamma=float(getattr(args, "gamma", 0.995)),
            lam=float(getattr(args, "lam", 0.95)),
            mode=args.mode,
            epochs=int(args.epochs),
            mb=int(args.mb),
            lr=float(args.lr),
            kl_coef=float(getattr(args, "_kl_coef", 0.0) or 0.0),
            kl_cap=getattr(args, "_kl_cap", None),
            ent_coef=getattr(args, "_ent_coef", None),
            adv_norm=getattr(args, "adv_norm", "auto"),
            normalize_ret=bool(getattr(args, "normalize_ret", 0)),
            kickstart_kl=kick_kl,
            ref_weights_b64=ref_b64,
            ref_weights_fp=ref_fp,
            shuffle=True,
            schedule_raw=course.ppo_schedule_dicts(),
            # 严格样本量配额（target_transitions 路线）：与 _serial_ppo 同一个来源，
            # 保证 remote 与本机两条 PPO 路径装载口径一致。0 = 全收（历史行为）。
            per_stage_quota=self._per_stage_quota(),
            # M2：瘦身开关 + 冒烟轮强制带 init_weights.json（echo 回显要用）。
            slim=slim,
            keep_init_weights=bool(getattr(args, "smoke", False)),
            # M3：kind=iter 的三件套（rollout 规格 + TS 运行时 sha/文件）；
            # 非 iter 轮恒为默认（kind="ppo"，manifest 不含这两个键 —— 逐字节不变）。
            kind="run" if plan_bytes is not None else ("iter" if rollout_spec else "ppo"),
            rollout_spec=rollout_spec,
            plan_bytes=plan_bytes,
            # 全离线导出：只建 job 目录（拿它当打包源），不记账本也不进待领池——
            # 云机不在网络上，记一条 `job_pending` 只会让控制台看到一条永远等不到工人的任务。
            register=export_path is None,
            # hub 中介推送的意图（hub 读它决定「这份活由我推」；缺席 = pull，字节不变）。
            dispatch="push" if hub_push else "",
            ts_code_sha256=(
                str(getattr(self, "_ts_code_sha256", "") or "") if rollout_spec else ""
            ),
            ts_code_zip_path=getattr(self, "_ts_code_zip_path", None) if rollout_spec else None,
            log=log,
        )
        jid = manifest["job_id"]
        # R2b（plan/r2-loop-task-queue §2.3）：把刚发布的 job 补进 WAL 的**在飞集**——
        # job_id 是 publish 的返回值（start 时还没有），只能上这条 attach。重启后
        # `_commit_journal` 就能报出「在等哪个 job、推给了谁」，而不是只报一个 round 号。
        self._commit_journal().attach(
            "ppo_remote",
            str(it),
            jid=jid,
            dispatch="push" if hub_push else "pull",
            ts=time.time(),
        )
        pack_sec = round(time.time() - t_pack, 3)
        if export_path is not None:
            # 全离线：不等待、不发 job——把这一段任务打成能上传 Kaggle/Colab 的任务包。
            from remote.bundle import export_bundle

            assert plan_bytes is not None  # 调用方保证（kind=run）
            index = export_bundle(
                export_path,
                manifest=manifest,
                plan_bytes=plan_bytes,
                init_weights_path=args.out,
                code_zip_path=self._code_zip_path,
                job_dir=Path(job_root) / jid,
                hub_url=hub_url,
                note=f"run_rl --export-bundle（it{it} 之后整段；course={getattr(args, 'course_name', '') or '-'}）",
            )
            log(
                f"[run_rl] 全离线任务包已导出：{export_path}"
                f"（{index['run_id']} it{index['it']} → it{index['end_it']}，"
                f"{sum(int(p['bytes']) for p in index['parts'].values()) / 1e6:.1f} MB）—"
                "上传 Kaggle/Colab 后：remote.bundle import + remote.run_loop"
            )
            self._bundle_index = index
            raise BundleExportedError(str(export_path))
        # 阻塞等待云 worker 完成（与 _serial_ppo 同构；超时由 hub-server 租约吸收）。
        # 半离线段要等整段（节点跑完 N 轮才回传），所以预算由调用方给（缺省 30min）。
        timeout_sec = float(wait_timeout_sec or 0.0) or 30 * 60.0
        # remote PPO 等待期集群空闲 —— 立即开 evalboard 窗领批（含等待期间新入队的）。
        # 否则「rollout 后才 enqueue」的批要等 PPO 收官后的第二次 idle，卡数十分钟。
        if hasattr(self, "_evalboard_idle"):
            self._evalboard_idle(it, getattr(self, "_last_dist_cfg", None))
        # P3-W1b：本课 push_node_url 非空时只取 URL 匹配项（N:1 共享天然成立）；
        # 为空时沿用旧逻辑（全取，默认行为零变化）。gpu_nodes 已在上方解析。
        if (
            transport == "auto"
            and push_url
            and not os.environ.get("REMOTE_PUSH_NODE")
            and len(gpu_nodes) == 0
        ):
            # F-B5：非空但匹配 0 个且无 env 注入 → 响亮失败（WARN + manifest 打标，
            # 不抛异常——抛异常致 loop 无限原地重试 hang；静默回落 pull 仍能正确训练，
            # 危险在误诊不在停机，配错 URL 必须一眼可见）。
            msg = (
                f"[run_rl] WARN: courses push_node_url={push_url} 匹配到 0 个 "
                "gpu_push 节点——本轮回落 pull（remote_hub_url），请检查 rl-config "
                "courses 块或节点 gpu_push 标记"
            )
            log(msg)
            manifest["push_filter_warn"] = msg
        if gpu_nodes:
            # ---- HUB 直推分支（DECISIONS §340 补充 4）：payload/code 直接 POST 到
            # GPU 节点的 worker_server（其 cloudflared 隧道暴露），HUB 只做出站
            # HTTPS——弱链路落在 Kaggle 网络。打包/账本/三重校验/落位与 pull 同构。
            _pl = find_payload(Path(job_root) / jid)
            if _pl is None:
                raise ProtocolError(f"job {jid}: payload 不在盘上（push 无法发送）")
            payload_bytes = _pl.read_bytes()
            code_bytes = self._code_zip_path.read_bytes()
            # M2 B3：读 job 目录内的 opt/ref blob，交给 push_client 按节点缓存按需发送。
            from remote.protocol import BLOB_NAMES, blob_path

            blobs = {
                n: _bp.read_bytes()
                for n in BLOB_NAMES
                if (_bp := blob_path(Path(job_root) / jid, n)).exists()
            }
            result = _push_job_round(
                gpu_nodes,
                manifest,
                jid,
                payload_bytes,
                code_bytes,
                args,
                timeout_sec,
                log,
                blobs=blobs,
                ts_code_bytes=(
                    Path(self._ts_code_zip_path).read_bytes() if rollout_spec else None
                ),
            )
        else:
            result = wait_job(hub_url, token, jid, timeout_sec=timeout_sec, log=log)
        # 三重校验 + 落位（D12）：任一不等响亮拒绝，不落盘
        verify_and_land(
            result,
            manifest,
            init_weights_path=args.out,
            traj_dir=args.traj,
            it=it,
            out_weights=args.out,
            log=log,
        )
        mark_job_completed(self._jsonl_path, jid)
        # I1：远端提交序列完成（校验落位 + job 记账）——WAL 收口。冒烟作废轮也算
        # 完成（commit 本身成功了；作废轮由 _prepare_iter_dir 清场后重试新轮）。
        self._commit_journal().finish("ppo_remote", str(it), jid=jid)
        self._forensics(f"remote_post it{it}")
        if result.get("smoke"):
            # 冒烟回显（worker --echo）：全链路已验证，但权重 = init 回显非真 PPO——
            # 作废本轮。job_completed 已记账（审计链完整）；落位的 out 权重与
            # 发布时逐字节相同（init 回显），无需回滚；重试轮 _prepare_iter_dir 清场。
            log(f"[run_rl] remote ppo it{it}: job {jid} 是冒烟回显（result.smoke）——本轮作废")
            raise SmokeVoidRoundError(jid)
        # H7（review-hy）：--remote-precollect 1 → 在 PPO 等待窗口后 spawn 下一轮首波
        # 预采（θ_N 快照，复用 spawn_collect_next 双缓冲机制）。默认 0（Q10 测后开）
        # 时不可达。stale 分数上限 30% 的筛选（S5/F4）属 §6-D3 后续项，未在此实现。
        # M3：上云轮不得预采——节点已经在跑本轮的 rollout，hub 再 spawn 一个本地预采
        # 就成了双份采集（且下一轮又会被 rollout_src=node 拒绝发布）。
        if (
            rollout_spec is None
            and int(getattr(args, "remote_precollect", 0) or 0)
            and (args.iters <= 0 or it < args.iters)
        ):
            from rl.collect_only import spawn_collect_next

            # H7（review-hy）：预采子进程句柄必须存入 self._collect_child，
            # 否则主循环的 join_precollect_child（下一轮开头）接收 None 跳过
            # 等待，预采首波可能尚未落盘即被 _prepare_iter_dir 清场。
            self._collect_child = spawn_collect_next(args, it)
            if self._collect_child is not None:
                log(
                    f"[run_rl] remote precollect: next-round first-wave spawned (pid={self._collect_child.pid})"
                )
        # 结算字段（下游 breaker / stop-loss / events 账本原样消费，D4）
        self._agg = _remote_forward_agg(result.get("agg", {}))
        self._chunks_n = int(result.get("agg", {}).get("chunks", 0))
        self._total_steps = int(result.get("agg", {}).get("steps", 0))
        self._kl_cum = self._agg["kl"]
        self._ppo_sec = round(time.time() - t_ppo, 1)  # 往返墙钟（含打包/上传/排队/下载）
        # 真训练秒：云端 worker 自报的 load+chunk+update（旧 worker / echo 无此字段 → 回落往返）
        self._ppo_cloud_sec = float(result.get("ppo_sec") or 0.0) or self._ppo_sec
        # M0 统一计量：传输层实测（字节/秒）汇总进 iteration 事件的 wire 子字典。
        # protocol/edge_ip/slim 由 M1/M2 的配置面注入（未配 = None，旧行为）。
        _cf_tunnel = _course_cf_tunnel(args)
        self._wire = _wire_from_result(
            result,
            # hub 中介推送也是「推」：hub 侧记的是 submit 实测（body_bytes/upload_sec），
            # 与直推同一套读数——两种 push 的可观测性不该一个有一个无。
            is_push=bool(gpu_nodes) or hub_push,
            pack_sec=pack_sec,
            cfg={
                # M1：记**真正生效**的隧道选项（CLI > courses.<stem>.cf_* > rl.cf_* > None）。
                # 不能用 `getattr(args, "remote_cf_protocol", None)`——控制台写的是 rl-config，
                # 不在 CLI 参数里时那个读法永远是 None（2026-09-17 查出的真缺口）。
                "protocol": _cf_tunnel[0],
                "edge_ip": _cf_tunnel[1],
                "slim": bool(int(getattr(args, "remote_slim", 1) or 0)),
                # M3：记**实测**在哪采集（node = 本轮整轮上云；run = 整段自主），不是
                # args 字面量（auto 会被 _rollout_source 解析成 local/node——原样记
                # auto 等于没记）。
                "rollout_src": (
                    "run" if plan_bytes is not None else ("node" if rollout_spec else "local")
                ),
            },
        )
        # 启动协议补丁（2026-09-08 vk1 事故）：kickstart_ref 已要求时，it1 校准把
        # 「缰绳真实落地」做进循环——云端 agg 无 kickstart 键或值恒 0 = worker 没跑
        # 缰绳（旧代码/模块钉住），响亮警示而非静默裸奔；正常值应为 0.1~0.6 量级。
        # 2026-09-14 x2-start it31 豁免：系数按几何衰减到期归零后（kick_kl 不活跃、
        # 训练侧不再附 ref），worker 回 0 是预期行为，不得误报（kickstart_warn_kind）。
        _kick_kind = kickstart_warn_kind(
            kick_on=kick_on,
            smoke=bool(result.get("smoke")),
            agg_kickstart=float(self._agg.get("kickstart", 0.0)),
            kick_coef=kick_kl,
        )
        if _kick_kind == "warn":
            log(
                f"[run_rl] WARN remote it{it}: kickstart_ref 已要求（kk 衰减调度激活）"
                "但云端结果 kickstart=0——worker 未执行缰绳？查 worker 代码/会话新鲜度"
            )
        elif _kick_kind == "expired":
            log(
                f"[run_rl] remote it{it}: kickstart 系数已衰减到期（kk={kick_kl:g}）——"
                "worker 未上报距离属预期，不告警"
            )
        log(
            f"[run_rl] remote ppo it{it}: job {jid} accepted — "
            f"steps={self._total_steps} chunks={self._chunks_n} "
            f"kl={self._agg['kl']:.5f} entropy={self._agg['entropy']:.4f} "
            + (f"kickstart={self._agg['kickstart']:.4f} " if kick_on else "")
            + f"({self._ppo_sec}s round-trip) -> {args.out}"
        )
        return result

    def _ensure_ts_code(self, job_root: str, *, log: Any) -> None:
        """M3：打包 rollout 用的 TS 运行时 zip（一次，缓存在 self 上）。

        为什么在训练侧打而不是节点侧 `bun install`：`tools/sim/export-rl-rollout.ts`
        的链路**零第三方运行时依赖**（非相对 import 只有 node 内建 `fs`/`path`），所以
        打包即可，云机不必装依赖（plan §5.3）。内容固定时间戳 + 内容寻址 sha，
        同源码反复跑只传一次。
        """
        if str(getattr(self, "_ts_code_sha256", "") or ""):
            return
        from remote.hub_client import pack_ts_code_zip

        repo_root = Path(__file__).resolve().parents[2]  # nn-training/rl/x.py -> 仓根
        zp = Path(job_root) / "ts_code.zip"
        self._ts_code_sha256 = pack_ts_code_zip(repo_root, zp, log=log)
        self._ts_code_zip_path = zp

    def _remote_iter(self, it: int, pairs: list[tuple[int, int]]) -> None:
        """M3：**整轮上云**（kind=iter）——节点跑 rollout + PPO，hub 只发规格、收结果。

        与 `_remote_ppo` 共享整条发布/传输/三重校验/落位/埋点链（只换「发什么、收什么」）：
          * 发：rollout 规格（逐局 argv，job 目录内相对路径）+ TS 运行时 + init 权重；
          * 收：权重/opt/agg（同旧）+ **采集报告**（本机此时无 shard 可算）。

        与动态采集（target_transitions）互斥：那套语义要求训练侧反复读本地 shard 补波，
        而这里 shard 在节点上（跑完即毁）。配错就响亮失败，不静默降级。
        """
        args = self.args
        if int(getattr(args, "target_transitions", 0) or 0) > 0:
            raise SystemExit(
                "[run_rl] rollout_src=node 与 --target-transitions（动态采集）互斥："
                "补波需要训练侧反复读本地 shard，而上云轮的 shard 在节点上（跑完即毁）。"
                "要动态采集就保持 rollout_src=local"
            )
        from rl.iter_job import build_iter_spec

        wver = dist_common.weights_fingerprint(args.out)
        workers = int(getattr(args, "remote_iter_workers", 0) or 0) or int(
            getattr(args, "workers", 1) or 1
        )
        spec = build_iter_spec(
            args,
            pairs,
            wver=wver,
            workers=workers,
            game_timeout_sec=float(getattr(args, "remote_iter_game_timeout", 0.0) or 0.0),
            hub_bun=str(getattr(self, "bun", "bun") or "bun"),
        )
        log(
            f"[run_rl] rollout_src=node it{it}: {len(pairs)} 局上云采集"
            f"（node workers={workers}，wver={wver[:12] if wver else '-'}…）"
        )
        t_roll = time.time()
        try:
            result = self._remote_ppo(it, rollout_spec=spec)
        except remote_retryable_exceptions() as e:
            # 上云轮**不经过** `_remote_ppo_or_degrade`（loop_core 在 _node_rollout 时
            # 跳过 _serial_ppo），所以那条路的「鉴权/闭锁类失败立即 ABORT」得在这里
            # 补上：否则 401/403 会走通用兜底 5×30s 重发同一 job 再死（x3-step 事故
            # 的同一个浪费）。只贴判决，不在这里降级——上云轮没有本地 shard 可训练。
            if isinstance(e, JobFailedError):
                # 与上面同规：节点已回报原因（如 bun 装不上 / TS 运行时取不到）——
                # 这是确定性能力缺失，重试无益，立即带原因停腿。
                self._abort_node_failure(it, e, where="rollout_src=node 采集+PPO")
                raise
            fatal = fatal_remote_http(e)
            if fatal:
                write_gate_verdict(
                    self._jsonl_path,
                    it,
                    "ABORT",
                    f"rollout_src=node 不可重试失败 HTTP {fatal}——检查 --remote-token "
                    f"与节点隧道：{str(e)[:200]}",
                    decider="loop",
                )
                log(
                    f"[run_rl] GATE ABORT it{it}: 上云 node 轮远端 HTTP {fatal}"
                    f"（鉴权/闭锁类，非网络抖动）——不再重试，立即停腿"
                )
                self._leg_abort = True
            raise
        rep = dict(result.get("report") or {})
        rep.pop("perGameSecs", None)  # 逐局秒数只用于诊断，不进 iteration 账本
        self._report = rep
        # 节点侧采集墙钟（本机口径的 self._rollout_sec 在这里无意义——整轮都在云上）。
        self._node_rollout_sec = float(rep.get("elapsedSec") or 0.0)
        self._rollout_sec = self._node_rollout_sec
        log(
            f"[run_rl] remote iter it{it}: 节点采集 {rep.get('games')} 局 "
            f"（{self._node_rollout_sec}s），往返 {round(time.time() - t_roll, 1)}s"
        )

    def _remote_run_segment(self, it: int, pairs: list[tuple[int, int]], n: int) -> int:
        """半离线：把 it..end_it **整段**交给云机自主跑（kind=run），返回段尾 it。

        用户需求（2026-09-17）：「云机领到任务（课程 + 初始权重 + 代码）后，即使本机 hub
        一直失联，也能全程自主完成训练，并以 kaggle/colab 官方方式提供产物打包下载」。

        与本机、kind=iter（逐轮上云）的差别只有一条：**hub 不再逐轮决策**。计划
        （`rl/plan.build_plan`）把「后面每轮跑哪些局 + argv 长什么样 + 到哪停」一次性写成
        文件随 payload 下发；节点用同 commit 的代码重放（`pairs_fp` 两侧对账，不符就
        一局不跑），逐轮权重/指标写进产物目录（`remote/artifacts.py`），末尾才回传合并结果。

        本函数只做三件 hub 侧的事：① 组装计划并发布；② 用**放大的**等待预算阻塞（整段
        墙钟量级）；③ 把节点回的逐轮明细落成 `run_segment` 事件（给控制台画曲线），并把
        段尾的报告/指标交给本轮结算（iteration 事件复用 `_record_iteration`）。

        中间轮没有本机 eval：它们不在本机跑，归档里也没有它们的权重（拿活指针去充数就是
        P0 修过的「标签超前一轮」）。所以调用方在本轮**跳过** `_dispatch_delayed_eval`。
        """
        args = self.args
        if str(getattr(args, "ppo", "") or "") != "remote":
            raise SystemExit(
                "[run_rl] 半离线整段（--run-iters）要求 --ppo remote：整段 rollout + PPO "
                "都在节点上跑，本机只发计划、收结果"
            )
        from rl.iter_job import build_iter_spec
        from rl.plan import RUN_NODE_LABEL, build_plan, dump_plan

        wver = dist_common.weights_fingerprint(args.out)
        if not wver:
            raise SystemExit(f"[run_rl] 半离线 it{it}: 本机无权重（{args.out}）——无起点不发段")
        workers = int(getattr(args, "remote_iter_workers", 0) or 0) or int(
            getattr(args, "workers", 1) or 1
        )
        game_timeout = float(getattr(args, "remote_iter_game_timeout", 0.0) or 0.0)
        spec = build_iter_spec(
            args,
            pairs,
            wver=wver,
            workers=workers,
            game_timeout_sec=game_timeout,
            hub_bun=str(getattr(self, "bun", "bun") or "bun"),
            node_label=RUN_NODE_LABEL,
        )
        iters_total = int(getattr(args, "iters", 0) or 0)
        if iters_total <= 0:
            if n < 0:
                raise SystemExit(
                    "[run_rl] --run-iters<0（跑到课程末尾）需要课程声明 iters——"
                    "没有终点就不叫整段，节点会一直跑下去"
                )
            iters_total = it + n
        plan = build_plan(
            args,
            it=it,
            iters_total=iters_total,
            rotate_seed=int(self._rotate_seed),
            # n-1：计划里的 argv 模板是给**下一轮**用的，段尾 = it + (n-1)。
            max_iters=0 if n < 0 else n - 1,
            workers=workers,
            game_timeout_sec=game_timeout,
            budget_sec=float(getattr(args, "run_budget_sec", 0.0) or 0.0),
            log=log,
        )
        end_it = int(plan["end_it"])
        log(
            f"[run_rl] rollout_src=run it{it}: 半离线段 it{it} → it{end_it}"
            f"（节点自主跑 {end_it - it} 轮；hub 失联不影响，产物在节点工作目录）"
        )
        t0 = time.time()
        try:
            result = self._remote_ppo(
                it,
                spec,
                plan_bytes=dump_plan(plan),
                wait_timeout_sec=_run_wait_sec(args),
            )
        except remote_retryable_exceptions() as e:
            # 与 `_remote_iter` 同规：节点已回报的确定性失败（bun 装不上 / TS 拿不到）
            # 与鉴权/闭锁类 HTTP 都**不重试**——重发同一段只是再白烧一个巨大等待预算。
            if isinstance(e, JobFailedError):
                self._abort_node_failure(it, e, where="半离线段 rollout+PPO")
                raise
            fatal = fatal_remote_http(e)
            if fatal:
                write_gate_verdict(
                    self._jsonl_path,
                    it,
                    "ABORT",
                    f"半离线段不可重试失败 HTTP {fatal}——检查 --remote-token 与节点隧道："
                    f"{str(e)[:200]}",
                    decider="loop",
                )
                log(f"[run_rl] GATE ABORT it{it}: 半离线段远端 HTTP {fatal}——不再重试")
                self._leg_abort = True
            raise
        rows = [r for r in (result.get("iters") or []) if isinstance(r, dict)]
        last = rows[-1] if rows else {}
        rep = dict(last.get("report") or result.get("report") or {})
        rep.pop("perGameSecs", None)  # 逐局秒数只用于诊断，不进 iteration 账本
        self._report = rep
        # 结算口径用**段尾那一轮**（本轮的 PPO 已在节点跑完）：采集墙钟同理。
        self._node_rollout_sec = float(rep.get("elapsedSec") or 0.0)
        write_event(
            self._jsonl_path,
            {
                "event": "run_segment",
                "iter_start": int(it),
                "iter_end": end_it,
                "iters": rows,
                "run_state": result.get("run_state"),
                "plan_sha256": str(result.get("plan_sha256", "") or ""),
                "artifacts": result.get("artifacts") or {},
                "wall_sec": round(time.time() - t0, 1),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        got_end = int(result.get("it_end") or end_it)
        if got_end != end_it:
            # 节点自报的段尾与计划不符：结果仍然可信（协议已校验严格递增 + it_end == 末轮），
            # 但“我们以为跑到哪”必须按**实际**改，否则下一轮会重跑已训过的轮。
            log(
                f"[run_rl] WARN 半离线段实际跑到 it{got_end}（计划 it{end_it}）——"
                f"按实际推进（run_state={result.get('run_state')}）"
            )
        log(
            f"[run_rl] 半离线段收回：it{it} → it{got_end}（{len(rows)} 轮明细，"
            f"往返 {round(time.time() - t0, 1)}s，state={result.get('run_state')}）"
        )
        return got_end

    def _export_offline_bundle(self, it: int, pairs: list[tuple[int, int]], n: int) -> None:
        """`--export-bundle`：把 it..it+n-1 打成**可上传云机**的全离线任务包（本轮不训练）。

        用户需求（2026-09-17）：「hub 支持打包导出训练任务（课程、初始权重、代码），以
        kaggle/colab 官方方式上传云机后，云机全程自主完成训练」。与半离线的差别：包一旦
        写出，hub 就可以关机——任务信息（课程/超参/血缘/计划/代码）全在包里。

        轮次对齐（整条第 N 个容易错的地方）：本轮的 `it` **就是**包里要跑的第一轮（loop 的
        `it` = last_completed+1），而包里 `plan.start_it` 必须 = `it - 1`（计划区间是
        `start_it+1 .. end_it`，起点权重 = `args.out` = W(it-1) 的产物）。所以
        `max_iters = n`（不是 n-1：这里没有「job 自己那一轮」要扣）。

        没跑过任何一轮（`args.out` 无权重）就拒导——包里没有起点的任务等于没任务。
        """
        args = self.args
        if str(getattr(args, "ppo", "") or "") != "remote":
            raise SystemExit(
                "[run_rl] --export-bundle 要求 --ppo remote：包里的 manifest 需要云端 PPO 的"
                "超参与策略血缘（本机模式没有这些字段，无法拼出可跑的任务）"
            )
        iters_total = int(getattr(args, "iters", 0) or 0)
        if iters_total <= 0:
            raise SystemExit(
                "[run_rl] --export-bundle 需要课程声明 iters（包里的计划必须有终点——「跑到哪停」"
                "是任务定义的一部分，不能靠云机猜）"
            )
        if it <= 1 and not dist_common.weights_fingerprint(args.out):
            raise SystemExit(
                f"[run_rl] --export-bundle: 没有起点权重（{args.out}）——先跑至少一轮，"
                "或把已有权重放到 --out 指向的位置"
            )
        from rl.iter_job import build_iter_spec
        from rl.plan import RUN_NODE_LABEL, build_plan, dump_plan, planned_iters

        wver = dist_common.weights_fingerprint(args.out)
        workers = int(getattr(args, "remote_iter_workers", 0) or 0) or int(
            getattr(args, "workers", 1) or 1
        )
        game_timeout = float(getattr(args, "remote_iter_game_timeout", 0.0) or 0.0)
        spec = build_iter_spec(
            args,
            pairs,
            wver=wver,
            workers=workers,
            game_timeout_sec=game_timeout,
            hub_bun=str(getattr(self, "bun", "bun") or "bun"),
            node_label=RUN_NODE_LABEL,
        )
        plan = build_plan(
            args,
            it=it - 1,
            iters_total=iters_total,
            rotate_seed=int(self._rotate_seed),
            max_iters=0 if n < 0 else n,
            workers=workers,
            game_timeout_sec=game_timeout,
            budget_sec=float(getattr(args, "run_budget_sec", 0.0) or 0.0),
            log=log,
        )
        log(
            f"[run_rl] 全离线导出：it{it} → it{plan['end_it']}"
            f"（{len(planned_iters(plan))} 轮）——本轮不训练、不等待"
        )
        # export_path 非空 ⇒ `_remote_ppo` 只建 job 目录（打包源）+ 写包 + 抛 BundleExportedError。
        self._remote_ppo(it, spec, plan_bytes=dump_plan(plan), export_path=str(args.export_bundle))

    def _export_weights(self, it: int) -> None:
        """按模式导出权重（goal/intent/per-tick）并归档（只归档不自动清理）。

        远程模式（--ppo remote）：weights_json 已由云 worker 产出、`_remote_ppo`
        三重校验落位到 args.out——这里只归档 + 日志（不再调 torch 导出，D2/D12）。
        """
        args = self.args
        # 课程声明 backup_prefix/backup_dir 时优先（D6 课程单一事实来源）；缺省
        # 退回按模式前缀 + 默认 nn-training/weights（旧行为）。
        bak_prefix = str(getattr(args, "backup_prefix", "") or "") or _MODE_BACKUP_PREFIX[args.mode]
        bak_dir = str(getattr(args, "backup_dir", "") or "") or None
        if getattr(args, "ppo", "local") == "remote":
            bak = backup_weights(args.out, it, prefix=bak_prefix, backup_dir=bak_dir)
            log(
                f"[run_rl] remote ppo it{it}: weights already landed by cloud worker "
                f"(D12) -> {args.out}"
            )
            if bak:
                log(f"[run_rl] weights archived -> {bak}")
            return
        if args.mode == "goal":
            from models.goal_net import GoalNet

            self._ppo_goal.export_goal_weights(cast(GoalNet, self._model), args.out)
        elif args.mode == "intent":
            from models.intent_net import IntentNet

            self._ppo_intent.export_intent_weights(cast(IntentNet, self._model), args.out)
        else:
            self._save_weights_json(self._model, args.out)
        bak = backup_weights(args.out, it, prefix=bak_prefix, backup_dir=bak_dir)
        log(
            f"[run_rl] ppo it{it}: steps={self._total_steps} chunks={self._chunks_n} "
            + (
                f"policy={self._agg['policy']:.4f} value={self._agg['value']:.4f} "
                f"entropy={self._agg['entropy']:.4f} kl={self._agg['kl']:.5f} -> {args.out}"
                if self._agg is not None
                else "metrics n/a — ppo_backend checkpoint completed by previous process"
            )
        )
        if bak:
            log(f"[run_rl] weights archived -> {bak}")

    # ------------------------------------------------- in-loop eval 墙钟（2026-09-17）

    def _eval_policy_cfg(self) -> dict:
        """rl-config 的 policy 块（每轮热读；见 loop_core 的 `_last_dist_cfg`）。"""
        return (getattr(self, "_last_dist_cfg", None) or {}).get("policy") or {}

    def _eval_join_soft_sec(self) -> float:
        """PPO 收官后的软等上限（policy.evalJoinSoftSec；默认 30s，0 = 完全不站等）。

        2026-09-17 用户指令：先前的硬编码 180s 把 eval 尾巴整段暴露在 PPO 之后
        （本机份额又只在 `_join_eval` 才放行 ⇒ 叠加成 PPO 后的第二次串行等待）。
        """
        from rl.eval_local import eval_join_soft_sec

        return eval_join_soft_sec(self._eval_policy_cfg())

    def _regate_local_eval(self) -> None:
        """本机 PPO 接手本轮 ⇒ 收回「提前放行」，恢复 R6（本机份额让位 PPO）。

        只收回**我们自己**提前放的 gate（`_eval_gate_early_released`）：无节点时
        `release_local_gate_if_starved` 放行的 gate 不动（否则本机局被卡到收官）。
        """
        if not getattr(self, "_eval_gate_early_released", False):
            return
        if self._eval_gate is not None and self._eval_gate.is_set():
            self._eval_gate.clear()
            log("[eval] 本机 PPO 接手本轮 —— 本机份额重新让位（R6；等到 _join_eval 或末 epoch）")
        self._eval_gate_early_released = False

    def _local_gate_epoch_hook(self) -> Callable[[int, object], None] | None:
        """本机 PPO 的提前放行钩子（末 `early` 个 epoch 开始即放行）；None = 不提前放行。

        与吞吐 T4 预采同一判据（ppo_update 的 on_epoch_done 每完成一个 epoch 回调，
        1 基）；`policy.evalLocalEarlyEpochs=0` → 返回 None，维持 R6 原语义。
        """
        from rl.eval_local import early_epoch_reached, eval_local_early_epochs

        gate = self._eval_gate
        if gate is None or gate.is_set():
            return None
        early = eval_local_early_epochs(self._eval_policy_cfg())
        if early <= 0:
            return None
        epochs = int(getattr(self.args, "epochs", 1) or 1)

        def _hook(ep_done: int, _model: object) -> None:
            if not gate.is_set() and early_epoch_reached(int(ep_done), epochs, early):
                gate.set()
                log(
                    f"[eval] 本机份额提前放行（本机 PPO epoch {ep_done}/{epochs}）"
                    " —— reserved 尾段与 PPO 尾部并行"
                )

        return _hook

    def _sweep_eval_tail(self) -> None:
        """上一轮 eval 尾巴的**自然收拢点**：下一轮 rollout 收官时（2026-09-17 用户指令）。

        为什么不是固定秒数：软等要么白站（尾巴早落地）要么丢（尾巴更晚），两个方向都
        不对。尾巴在下一轮整段采集期间有几分钟可用——它自己跑完就自己写 summary（
        wver 键控、续跑幂等），所以到这里通常只剩一次零成本观测/清账。**本函数不 join、
        不 sleep**：还在跑的（异常：节点慢/挂了）只打 WARN，由它自己的 `eval_window_sec`
        deadline 结束；`policy.evalJoinSoftSec>0` 时才走旧的「边界处最多补等 N 秒」。
        """
        pending = self._eval_tail
        self._eval_tail = None
        if pending is None:
            return
        thread, t_start = pending
        elapsed = time.time() - t_start
        window = float(getattr(self.args, "eval_window_sec", 1500) or 1500)
        if not thread.is_alive():
            log(f"[eval] tail settled during rollout (+{elapsed:.0f}s) — 已自落账")
            return
        from rl.eval_local import eval_tail_overran

        soft = self._eval_join_soft_sec()
        if soft > 0.0:
            # 应急旋钮：只在边界处补等（旧语义）；缺省 0 ⇒ 不进这个分支
            _t_join = time.time()
            thread.join(timeout=soft)
            waited = time.time() - _t_join
            self._eval_join_sec = round(self._eval_join_sec + waited, 1)
            if not thread.is_alive():
                log(f"[eval] tail settled at rollout boundary (+{elapsed:.0f}s, waited {waited:.1f}s)")
                return
        level = "WARN " if eval_tail_overran(t_start, window, time.time()) else ""
        log(
            f"[eval] {level}tail still running at rollout boundary "
            f"(alive {elapsed:.0f}s / window {window:.0f}s) — 继续后台消化，不阻塞主链"
        )

    def _join_eval(self, it: int) -> dict | None:
        """v3.12 eval 延迟化：eval 不阻塞训练主链（后台线程 + wver 键控）。

        门判定读 eval_log 的 eval_summary（iter 字段保留原轮号 + wver），晚入账只
        让判定窗口顺延，判据不变。**per-tick 不站等**（2026-09-17）：未收官的尾巴整根
        传给 `_sweep_eval_tail`，由下一轮 rollout 收官这个自然边界收拢——不站着等任何
        固定秒数。intent/goal 仍全预算 join（止损判门要吃同轮 summary）。
        """
        args = self.args
        if self._eval_gate is not None:
            self._eval_gate.set()
        self._eval_gate_early_released = False
        eval_join_sec = 0.0
        eval_thread = self._eval_thread
        if eval_thread is not None and eval_thread.is_alive():
            budget = float(args.eval_window_sec) + 60.0
            if args.mode in ("intent", "goal"):
                # intent/goal：eval_summary 须在 jsonl 写回前结算（止损判门依赖）。
                log(
                    f"waiting up to {budget:.0f}s for clean-eval round before next "
                    f"weight distribution"
                )
                _t_join = time.time()
                eval_thread.join(timeout=budget)
                eval_join_sec = round(time.time() - _t_join, 1)
            else:
                # per-tick：不站等（缺省）→ 交棒；policy.evalJoinSoftSec>0 时才补等。
                soft = min(budget, self._eval_join_soft_sec())
                if soft > 0.0:
                    log(
                        f"[run_rl] eval deferred: soft-wait {soft:.0f}s for tail "
                        f"(policy.evalJoinSoftSec — 应急旋钮)"
                    )
                    _t_join = time.time()
                    eval_thread.join(timeout=soft)
                    eval_join_sec = round(time.time() - _t_join, 1)
                if eval_thread.is_alive():
                    # 交棒：下一轮 rollout 收官时收拢（_dispatch_delayed_eval 入口）
                    self._eval_tail = (eval_thread, self._eval_tail_start or time.time())
                    log(
                        "[run_rl] eval deferred: tail handed to next rollout boundary "
                        "（不站等；线程自己按 eval_window_sec 收尾并落账）"
                    )
        self._eval_tail_start = None
        self._eval_join_sec = eval_join_sec
        # intent/goal：回读该迭代 eval_summary（评估线程写入；止损判门的数据源）。
        eval_rec = (
            read_eval_summary(self._jsonl_path, it) if args.mode in ("intent", "goal") else None
        )
        # pace checkpoint（intent/goal 护栏）：iter5 首现通关。
        if args.mode in ("intent", "goal") and it == 5 and self._report["winRate"] <= 0:
            log("WARN pace: no clear by iter5 (rollout winRate=0) — investigate")
        return eval_rec

    def _dispatch_delayed_eval(self, it: int, dist_cfg: dict | None) -> None:
        """延迟 eval 派发（P0 修复）：本轮采集收官后，为上一轮已完成权重 W(it-1) 派发。

        旧语义在此处派发读活指针 = W(it-1) 却标 itN（标签超前一轮）；新语义标
        权重轮 M=it-1，读不可变归档（回落活指针 + WARN）。游戏仍藏进随后 PPO(it)
        空窗，wall 不变。未覆盖的对局由派发内幂等续跑；全覆盖即空转返回。
        仅 per-tick（intent/goal 走 m1 路径，it0 基线走独立流，均不动）。
        """
        from rl.eval_dispatch import dispatch_eval_bg, find_archive_weights, select_delayed_eval_it
        from rl.queue import RUN_ID

        args = self.args
        # 本轮采集刚落幕（rollout 收官）= 上一轮 eval 尾巴的自然收拢点：先收拢，再派新轮。
        self._sweep_eval_tail()
        if getattr(args, "mode", "per-tick") != "per-tick":
            # intent/goal m1 与 it0 基线走各自派发流，此处不碰（rollout_phase 已处理）。
            return
        self._eval_thread = None
        self._eval_gate = None
        m = select_delayed_eval_it(it, self._eval_on_round)
        if m is None:
            return
        src = find_archive_weights(
            str(getattr(args, "backup_dir", "") or ""),
            str(getattr(args, "backup_prefix", "") or ""),
            m,
        )
        from_archive = src is not None
        src_path = src if src is not None else str(args.out)
        if not from_archive:
            log(
                f"[eval] it{m}: 归档缺席（backup 失败？）——回落活指针 {src_path} 派发"
                "（wver 与离线复跑不可比，本轮 eval 仅供参考）"
            )
        from rl.eval_local import eval_local_early_epochs, local_gate_release_plan

        self._eval_gate = threading.Event()
        self._eval_gate_early_released = False
        # 尾巴的窗口起点（收拢时判“是否跑过自己的窗口”）；只作时间基准，不参与等待。
        self._eval_tail_start = time.time()
        # 本机份额放行档（2026-09-17）：本轮本机不跑 PPO（远端 PPO / 整轮上云 / stream
        # 已在轮内跑完）⇒ 立刻放行（核心空闲，预留尾段即时开跑）；本机 PPO ⇒ 末 epoch
        # 放行（early=0 时维持 R6：_join_eval 才放行）。
        plan = local_gate_release_plan(
            ppo_remote=str(getattr(args, "ppo", "local")) == "remote",
            node_rollout=bool(getattr(self, "_node_rollout", False)),
            stream_round=getattr(self, "_stream_meta", None) is not None,
            early_epochs=eval_local_early_epochs(self._eval_policy_cfg()),
        )
        if plan == "immediate":
            self._eval_gate.set()
            self._eval_gate_early_released = True
            log("[eval] 本机份额提前放行（本轮 PPO 不在本机跑）——reserved 尾段立即开跑")
        self._eval_thread = dispatch_eval_bg(
            self.bun,
            src_path,
            self._traj_dir,
            args,
            dist_cfg or {},
            f"{RUN_ID}.{it}",
            m,
            (self._report or {}).get("winRate"),
            local_gate=self._eval_gate,
        )
        log(
            f"[eval] it{m} dispatched from "
            f"{'archive' if from_archive else 'LIVE pointer'} {src_path} "
            f"(round it{it} PPO window)"
        )

    def _eval_covered(self, m: int, summaries: dict[int, list[dict]]) -> bool:
        """drain 覆盖判定：存在 dropped==0 的 summary 即完整（缺字段旧行按未覆盖）。"""
        rows = summaries.get(m, [])
        if not rows:
            return False
        return any(r.get("dropped") == 0 for r in rows)

    def _drain_pending_eval(self) -> None:
        """收官 drain（用户指令：最终轮立即 eval）：为最新已完成且无完整 summary
        的评估轮权重派发并等收官。串行执行（无 PPO 空窗可藏），等收官预算
        min(eval_window_sec + 60, 600)s，全程 best-effort 只记日志。
        smoke 轮 / 非 per-tick 直接跳过。
        """
        from rl.eval_dispatch import dispatch_eval_bg
        from rl.queue import RUN_ID

        args = self.args
        # 收官前先把在飞尾巴清账（同理：只观测/清账，不站等）。
        self._sweep_eval_tail()
        try:
            if getattr(args, "mode", "per-tick") != "per-tick" or getattr(args, "smoke", False):
                return
            eval_log = Path(self._traj_dir).parent / "eval_log.jsonl"
            summaries: dict[int, list[dict]] = {}
            try:
                with open(eval_log, encoding="utf-8") as jf:
                    for line in jf:
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        if r.get("event") == "eval_summary" and isinstance(r.get("iter"), int):
                            summaries.setdefault(int(r["iter"]), []).append(r)
            except OSError:
                pass
            arch_m: dict[int, str] = {}
            bdir = str(getattr(args, "backup_dir", "") or "")
            bpre = str(getattr(args, "backup_prefix", "") or "")
            if bdir and bpre:
                try:
                    from rl.archive import REPO_ROOT

                    root = REPO_ROOT
                except Exception:
                    root = None
                import os as _os
                import re as _re

                base = str(root / bdir) if root is not None and not _os.path.isabs(bdir) else bdir
                try:
                    pat = _re.compile(rf"^{_re.escape(bpre)}\.it(\d+)\..*\.json$")
                    for p in Path(base).glob(f"{bpre}.it*.*.json"):
                        mt = pat.match(p.name)
                        if mt:
                            kk, vv = int(mt.group(1)), str(p)
                            if kk not in arch_m:
                                arch_m[kk] = vv
                except OSError:
                    pass
            if not arch_m:
                log("[eval] drain: 无归档权重可评估——跳过")
                return
            cand = sorted(
                m
                for m in arch_m
                if m >= 1 and self._eval_on_round(m) and not self._eval_covered(m, summaries)
            )
            if not cand:
                log("[eval] drain: 评估轮权重均已完整 summary——无需收尾 eval")
                return
            if len(cand) > 1:
                log(f"[eval] drain: 旧缺口 {cand[:-1]} 留档（只收尾最新 it{cand[-1]}）")
            m = cand[-1]
            self._eval_gate = threading.Event()
            # 收官 drain 没有并发训练：立刻开闸，否则 local_worker 会等 gate 到 deadline
            # （2026-09-15 x3-power it30：远端 engine_epoch 全 mismatch + gate 未开 → 600s 零局）。
            self._eval_gate.set()
            self._eval_thread = dispatch_eval_bg(
                self.bun,
                arch_m[m],
                self._traj_dir,
                args,
                getattr(self, "_last_dist_cfg", None) or {},
                f"{RUN_ID}.{m}",
                m,
                None,
                local_gate=self._eval_gate,
            )
            budget = min(float(getattr(args, "eval_window_sec", 1800) or 1800) + 60.0, 600.0)
            log(f"[eval] drain: it{m} 收尾派发（archive），等收官 ≤{budget:.0f}s")
            self._eval_thread.join(timeout=budget)
            log(f"[eval] drain: it{m} 收尾结束（alive={self._eval_thread.is_alive()}）")
        except Exception as e:
            log(f"[eval] drain: 收尾 eval 失败（{type(e).__name__}: {e}）——不阻断收官")

    def _record_iteration(self, it: int) -> None:
        """iteration 事件落账（字段契约在 rl/events.py::write_iteration）。

        R2a：写入后立刻并入 `LedgerView`（`_ledger_apply`）——视图因此始终 == 盘上
        账本，且没有第二次全文件扫描（用户 2026-09-18 裁决：每课只读一遍）。
        """
        self._ledger_apply(
            write_iteration(
                self._jsonl_path,
                self.args,
                it,
                self._report,
                {
                    "rollout_sec": self._rollout_sec,
                    "ppo_sec": self._ppo_sec,
                    "ppo_cloud_sec": self._ppo_cloud_sec,
                    # M0 统一计量（additive；本地/旧路径无此键 → None）。
                    "wire": getattr(self, "_wire", None),
                    "total_steps": self._total_steps,
                    "chunks_n": self._chunks_n,
                    "agg": self._agg,
                    "kl_cum": self._kl_cum,
                    "halted": self._halted_flag,
                    "dropped_games": self._dropped_games,
                    "waves": self._waves_n,
                    "load_sec": self._load_sec,
                    "tail_drain_sec": self._tail_drain_sec,
                    "eval_join_sec": self._eval_join_sec,
                    # 动态采集（None = 未开该模式；additive 字段，旧行无此键）
                    "transitions_target": self._volume_target,
                    "transitions_collected": self._volume_collected,
                    "transitions_capped": True if self._volume_capped else None,
                },
            )
        )
