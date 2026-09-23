"""common/hashing.py —— 字节 / 文件 sha256 的**唯一实现**。

## 为什么必须唯一（这是契约，不是巧合）

`wver`（权重版本键）、`init_weights_fp`、payload/blob 键、`code_sha256` 全都靠
**「同一份字节 ⇒ 同一个哈希」**把两侧串起来：训练机算出的 `wver` 要与节点算的相等，
hub 记的 blob 键要与 worker 找的相等。任何一处实现被「顺手优化」成不同写法，账本就会
把同一份权重读成两个版本——而且**测试多半还是绿的**（两侧各自自洽）。

本仓曾有 3 份字形相同的实现 + 1 处 inline：

* `dist_common.weights_fingerprint`（被 20+ 处调用，是事实上的公共 API）
* `remote/artifacts.sha256_file` / `sha256_bytes`
* `remote/hub_client._sha256_file`
* `remote/bundle.sha256_file` / `sha256_bytes`（函数内 `import hashlib`）

`remote/artifacts.py` 原 docstring 写「不依赖 dist_common 的导入，纯 stdlib」——这个
诉求是对的，但**换成依赖本模块同样成立**：本模块就是那个纯 stdlib 的落点，且两侧不再
各留一份定义。旧名字全部保留为 re-export（`from common.hashing import sha256_file`），
调用点与契约测试一个字都不用改。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

__all__ = ["sha256_bytes", "sha256_file"]

#: 读文件的块大小。改它**不影响**结果（sha256 是流式的），所以它不是「旋钮」，
#: 只是 IO 粒度；这里钉死 1 MiB 与全仓历史口径一致。
_CHUNK = 1 << 20


def sha256_bytes(b: bytes) -> str:
    """字节串的 sha256（hex，小写）。"""
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str | Path) -> str:
    """文件字节的 sha256（hex，小写）；流式读取，大文件不占内存。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()
