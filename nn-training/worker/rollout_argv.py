"""rollout_argv.py —— 逐局/逐轮 argv 的**重定向原语**（纯字符串，无仓内依赖 ⇒ 账本 L0）。

## 为什么单独一个模块（而不是留在 `worker/plan.py`）

`retarget_argv` 原先住在 `worker/plan.py`（账本 **L4**：它要 `worker.cmd` 的 `build_rollout_cmd`）。
2026-10-07 起它多了一个读者 —— `worker.iter_topup`（节点侧有界多批补差，账本 **L2**，只靠
`worker.volume_alloc` / `worker.iter_rollout` 这两个 L1 邻居）。**L2 模块 import L4 模块 = 上向边**，
而 `remote/worker.py`（L5）又必须在顶层 import 补差实现 ⇒ 补差模块的秩只能 ≤ L4。留原地无解，
故按本仓既定手段「把共同依赖**下沉**」（`tests/helpers/remote_dag.py` 的环处理只有「下沉」与
「参数注入」两种）：纯替换逻辑下沉成 L0 叶子，`worker/plan.py` 顶层再引回来（对读者是同一个
函数对象，`worker.plan.retarget_argv` 这个名字保持可用）。

## 它认识什么、不认识什么

只认识**四个必然逐局变化的 flag**（`--stages/--seeds/--out/--wver`）+ 一个「只有自定义关才有」的
可选 flag（`--stage-json`）。导出器细节（三导出器 + 课程覆盖 + D14 血缘）一概不知 —— 拼命令的
知识只有 `biz/cmd.build_rollout_cmd` 一份，复制到协议/云侧就会漂移。
"""

from __future__ import annotations

#: 逐轮重定向的 flag → 占位符名。值一律按当轮/当局实值写入（模板里是目标轮的值）。
RETARGET_FLAGS: dict[str, str] = {
    "--stages": "stage",
    "--seeds": "seed",
    "--out": "out",
    "--wver": "wver",
}


def retarget_argv(
    template: list[str],
    *,
    stage: int,
    seed: int,
    out: str,
    wver: str,
    stage_json: str | None = None,
) -> list[str]:
    """把模板 argv 的四个动态 flag 换成当局/当轮实值；`--stage-json` 按关卡增删改。

    行为：
      * `--stages/--seeds/--out/--wver` 一律替换其后的值；模板里没有则**追加**
        （`--wver` 在模板里可能是占位空串）。
      * `--stage-json`：`stage_json` 非空 → 替换/追加；为空 → **整对删除**（新关卡不是
        自定义关时留着旧值 = 拿错关卡的 JSON 跑，比缺失更危险）。
    """
    values = {"stage": int(stage), "seed": int(seed), "out": str(out), "wver": str(wver)}
    res: list[str] = []
    seen: set[str] = set()
    i = 0
    n = len(template)
    while i < n:
        tok = template[i]
        if tok in RETARGET_FLAGS:
            seen.add(tok)
            res += [tok, str(values[RETARGET_FLAGS[tok]])]
            i += 2 if (i + 1 < n and not template[i + 1].startswith("--")) else 1
            continue
        if tok == "--stage-json":
            seen.add(tok)
            if i + 1 < n and not template[i + 1].startswith("--"):
                if stage_json:
                    res += [tok, str(stage_json)]
                i += 2
                continue
            if stage_json:
                res += [tok, str(stage_json)]
            i += 1
            continue
        res.append(tok)
        i += 1
    for flag, name in RETARGET_FLAGS.items():
        if flag not in seen:
            res += [flag, str(values[name])]
    if stage_json and "--stage-json" not in seen:
        res += ["--stage-json", str(stage_json)]
    return res
