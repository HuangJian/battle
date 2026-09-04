"""remote_worker.py — 云端 PPO worker 入口（`python -m remote_worker`）。

独立 CLI（§3.2/D12）：notebook 只需一行
`!python -m remote_worker --poll <hub-url> --token <token>`，断线重连/轮询/
幂等重拉由进程自理。M1 假云回环直接驱动此入口（同机 `--poll http://127.0.0.1:PORT`）；
M2 真云只换 `--poll` 的 URL。

实现全部在 `remote.worker`（本文件是薄包装，便于 `python -m remote_worker`
的模块名解析）。
"""

from __future__ import annotations

from remote.worker import main

if __name__ == "__main__":
    main()
