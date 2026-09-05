"""remote_worker_serve.py — push 模式 GPU 侧服务端入口（`python -m remote_worker_serve`）。

独立 CLI（DECISIONS §340 补充 4，方向翻转）：notebook 一行
`!python -m remote_worker_serve --port 8790 --token <token>` 起服务，再起
`cloudflared tunnel --url http://localhost:8790`，把打印的 URL 贴进 HUB 侧
rl-config 的 nodes 条目（gpu_push: true）。实现全部在 remote/worker_server。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from remote.worker_server import serve_forever


def main() -> None:
    ap = argparse.ArgumentParser(description="remote PPO worker server (push mode, cloud GPU)")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--token", default="", help="Bearer token（与 HUB 共享密钥）")
    ap.add_argument("--token-file", default="", help="从文件读取 token（避免进程列表泄露，H10）")
    ap.add_argument("--work", default="tmp/remote-worker-serve", help="job/payload/code_cache 工作目录")
    ap.add_argument("--device", default="cpu", help="torch device: cpu / cuda / cuda:0")
    ap.add_argument("--threads", type=int, default=0, help="torch intra-op threads (0=default)")
    args = ap.parse_args()
    token = args.token
    if args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    serve_forever(args.port, token, Path(args.work), device=args.device, torch_threads=args.threads)


if __name__ == "__main__":
    main()
