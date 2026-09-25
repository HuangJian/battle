"""iter_job —— kind=iter job 的 hub 侧规格构造（M3，plan/remote-wire-remediation §5.2）。

职责边界刻意很小：**把「本机本来会跑的那些 rollout 命令」原样拿过来、只换路径**。
命令拼装仍是 `rl/cmd.build_rollout_cmd`（三导出器 + 课程覆盖 + D14 血缘的唯一来源），
本模块只做两件本地没有的事：

  1. `--weights` / `--out` 换成 **job 目录内的相对路径**（节点以 job 目录为 cwd 执行）；
  2. 去掉 argv[0]（本机 bun 的绝对路径）——节点用自己的 bun（协议白名单只放行脚本名）。

于是「节点跑的采集」与「本机跑的采集」逐字节同命令（计划 §5.5① 逐位对拍的前提），
而协议层只需校验 argv 本身的形状（白名单 + 相对路径 + 逐局 stage/seed，见
`remote.protocol.validate_rollout_spec`）。
"""

from __future__ import annotations

from typing import Any

from remote.protocol import INIT_WEIGHTS_NAME, ITER_NODE_LABEL
from rl.cmd import build_rollout_cmd


def build_iter_spec(
    args: Any,
    pairs: list[tuple[int, int]],
    *,
    wver: str,
    workers: int = 0,
    game_timeout_sec: float = 0.0,
    hub_bun: str = "bun",
    node_label: str = ITER_NODE_LABEL,
) -> dict:
    """(stage, seed) 对集 → kind=iter 的 `rollout` 规格（未归一化；发布前会再校验）。

    `hub_bun` 只用来喂 `build_rollout_cmd` 拼命令（argv[0] 随即被丢掉）——**不要**把它
    塞进规格：规格里的 `bun` 是「节点 PATH 上要找的可执行名」，本机的绝对路径在云机上
    毫无意义（Windows 的 `bun.exe` 更会直接找不到）。节点用自己的 bun，版本对账靠启动
    自检行（worker 日志 + wire.bun_version），不靠这个字段。

    `node_label` 写进每局 shard manifest（`--node-label`）：逐轮上云用 `iter`，半离线整段
    用 `run`（`rl.plan.RUN_NODE_LABEL`）——事后按 shard 就能分清“这一批局是逐轮上云跑的”
    还是“云端自主段跑的”。

    workers<=0 → 退化为 1（节点侧另有上限钳制）；`wver` 必须是本轮权重的指纹
    （`dist_common.weights_fingerprint(args.out)`）——它同时进 argv（`--wver`，写进每局
    shard manifest）和规格（声明集），两侧必须同值，否则节点复算 data_fp 必然不符。
    """
    if not pairs:
        raise ValueError("build_iter_spec: pairs 为空（没有要对局的局）")
    argv: list[list[str]] = []
    for idx, (stage, seed) in enumerate(pairs):
        cmd = build_rollout_cmd(
            hub_bun,
            args,
            weights=INIT_WEIGHTS_NAME,
            # 与本地 `w{i}/` 同形：每局一个独立目录（报告 per-game 独立，事后聚合）。
            out_dir=f"w{idx}",
            stage=int(stage),
            seed=int(seed),
            wver=str(wver or ""),
            node_label=str(node_label or ITER_NODE_LABEL),
            # 这份 argv 由**节点**执行（job 目录 cwd）：起始分布的快照还没走云侧通道
            # ⇒ 课程开了 state_init 时在发布前响亮拒（plan/x20-state-init.plan.md §P2.5）。
            node_side=True,
        )
        argv.append(list(cmd[1:]))  # 丢掉 bun 路径（节点用自己的）
    return {
        "argv": argv,
        "wver": str(wver or ""),
        "workers": int(workers) if int(workers) > 0 else 1,
        "game_timeout_sec": float(game_timeout_sec or 0.0),
        "bun": "bun",
    }
