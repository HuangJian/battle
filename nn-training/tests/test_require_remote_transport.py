"""test_require_remote_transport — 纯 push 不依赖本地 hub-server/cloudflared。"""

from __future__ import annotations

import pytest

from rl.loop_steps import require_remote_transport


def test_push_gpu_nodes_allow_empty_hub_url() -> None:
    require_remote_transport(
        hub_url="",
        token="tok",
        gpu_nodes=[{"url": "https://gpu", "authKey": "k"}],
        env_push=None,
    )


def test_pull_hub_url_still_ok() -> None:
    require_remote_transport(
        hub_url="https://hub",
        token="tok",
        gpu_nodes=[],
        env_push=None,
    )


def test_env_push_node_allows_empty_hub() -> None:
    require_remote_transport("", "tok", [], env_push="http://127.0.0.1:8790")


def test_no_transport_fails() -> None:
    with pytest.raises(SystemExit, match=r"gpu_push|hub_url"):
        require_remote_transport("", "tok", [], env_push=None)


def test_missing_auth_fails() -> None:
    with pytest.raises(SystemExit, match=r"token|authKey"):
        require_remote_transport(
            "",
            "",
            [{"url": "https://gpu", "authKey": ""}],
            env_push=None,
        )
