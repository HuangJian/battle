"""loop_remote_push —— **直推腿** mixin（2026-09-25 S4 第二十二刀从 `rl/loop_remote.py` 拆出）。

判据同源：「**把一份 job 送到节点**」（提交 → 首发择机 → 取回择机）。三种上云形态
（本机轮 / `kind=iter` 整轮 / `kind=run` 整段）都要走它；本簇**不碰**任何 PPO / 落位 / 事件
语义，只依赖 `remote.push_client` 的两个入口与 `rl.loop_transport._push_over_nodes`
（failover 判决单源——与 `_push_job_round` 同一份）。

依赖方向：`class TrainingRemoteJob(TrainingRemotePush)`——job 四步的发布/取回相位调用本簇，
组合根 `TrainingRemote` 经它继承（**调用者依赖被调用者**）。

**DI seam 是本模块自己的**：本簇的方法体读 `_push_submit` / `_push_wait_result` ⇒ 它们
在**本模块**命名空间解析，测本簇的用例必须 patch `rl.loop_remote_push.*`（patch 旧的
`rl.loop_remote.*` 会变成静默空操作）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from common.protocol import ProtocolError, find_payload
from remote.push_client import submit_job as _push_submit
from remote.push_client import wait_result as _push_wait_result
from rl.log import log
from rl.loop_round import RemotePpoJob
from rl.loop_transport import _push_over_nodes


class TrainingRemotePush:
    """直推腿：把 job 提交给节点 / 首发择机 / 取回择机。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）。
    # 与 `rl/loop_steps.py` 的同类声明**有意并存**：混入的状态契约必须在**每个**文件里对
    # mypy 可见（否则本文件里的 `self.args` 会被判成未声明属性），而运行期的唯一真相
    # 是那个被实例化的组合类。下游混入（Job / Drive）经继承看得见这一组声明。
    args: Any
    #:`_remote_ppo_publish` 写入、本簇读取的两个传输产物（内容寻址的 code / TS 码 zip）。
    _code_zip_path: Any
    _ts_code_zip_path: Any

    def _push_submit_node(self, sess: RemotePpoJob, i: int) -> None:
        """向第 i 个直推节点提交 job（**发布**动作），并把传输读数记进会话。

        payload / code / blob 一律**从 job 目录重读盘**：会话刻意不持有几十 MB 的字节
        （见 `RemotePpoJob` 的文档），换节点重发时正好也重读一次。
        """
        node = sess.nodes[i]
        job_dir = Path(sess.job_root) / sess.jid
        _pl = find_payload(job_dir)
        if _pl is None:
            raise ProtocolError(f"job {sess.jid}: payload 不在盘上（push 无法发送）")
        from common.protocol import BLOB_NAMES, blob_path

        blobs = {
            n: bp.read_bytes()
            for n in BLOB_NAMES
            if (bp := blob_path(job_dir, n)).exists()
        }
        # M0：submit_job 返回本轮实测传输账（body/payload/code 字节 + 上传秒）
        sess.submit_wire = _push_submit(
            str(node["url"]),
            str(node.get("authKey", "")),
            sess.manifest,
            _pl.read_bytes(),
            self._code_zip_path.read_bytes(),
            blobs=blobs,
            ts_code_zip=(
                Path(self._ts_code_zip_path).read_bytes() if sess.rollout_spec else None
            ),
            echo=bool(getattr(self.args, "smoke", False)),
            log=log,
        )
        sess.node_i = i
        log(f"[run_rl] push: job {sess.jid} 已提交 -> {node['url']}（等待 GPU 完成）")

    def _push_submit_first(self, sess: RemotePpoJob) -> None:
        """按序找第一个收下这份 job 的节点；全失败时把**确定性原因**原样上抛。

        failover 判决在 `_push_over_nodes`（与 `_push_job_round` / `_push_fetch` 同一份）。
        """

        def step(i: int, _node: dict) -> None:
            self._push_submit_node(sess, i)

        _push_over_nodes(sess.nodes, 0, step, log)

    def _push_fetch(self, sess: RemotePpoJob) -> dict:
        """等已提交的节点回结果；该节点失败就换下一个（**重提交**），全失败照旧上抛。

        与组合入口 `_push_job_round` 同序（逐节点「提交 → 等」，任一环节失败即换人），
        差别只在「第一个节点的提交已经发生在发布相位」⇒ 本函数从 `sess.node_i` 起走，
        且换到新节点时要先补提交（新节点从没见过这份 job）。
        """

        def step(i: int, node: dict) -> dict:
            if sess.node_i != i:
                self._push_submit_node(sess, i)
            result = _push_wait_result(
                str(node["url"]),
                str(node.get("authKey", "")),
                sess.jid,
                timeout_sec=sess.timeout_sec,
                log=log,
            )
            if isinstance(sess.submit_wire, dict) and isinstance(result, dict):
                # 挂在结果上随返回一路上浮（_wire_from_result 消费）——不改 result 的
                # 校验字段，纯 additive。
                result["wire_hub"] = sess.submit_wire
            return result

        return cast(dict, _push_over_nodes(sess.nodes, sess.node_i, step, log))
