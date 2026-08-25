"""rl — RL 训练核心逻辑包（自 run_rl.py 工程化抽取，2026-08-25）。

分层：
  log.py           带时间戳日志（全链路统一格式）
  course.py        课程：parse_range / build_pairs（(rotateSeed,it) 纯函数）
  reports.py       跨 worker 报告聚合
  resume.py        断点续跑：completed_pairs / resumed_manifests / jsonl 锚点
  breaker.py       F4 熔断纯逻辑（阈值 + 连击判定）
  queue.py         中央队列调度（远端节点 + 本地槽位）与纯本地回退
  eval_dispatch.py 干净评估分发（固定语料贪心局，旁路不拖垮训练）
  stream.py        流式迭代（采集与 PPO 波次重叠）

入口约定：run_rl.py 必须留在 nn-training/ 顶层 —— 统一启动器只接受裸文件名。
"""
