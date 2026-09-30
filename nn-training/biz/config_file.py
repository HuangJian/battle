"""rl/config_file —— **rl-config.json 的文件面**（S5 第十刀，2026-09-27）。

从 `biz/config.py` 整块搬出（**逐字节不动**）。这里是「本机那份永不入库的启动配置」
在代码里的唯一形状：

* `RL_CONFIG_ENV` —— 重定向环境变量名（`BCITY_RL_CONFIG`，源头 `common.distribution.RL_CONFIG_ENV`）；
* `rl_config_path()` —— 路径的**唯一**来源（env > `nn-training/rl-config.json`），实现委托
  `common.distribution.rl_config_path()`（一处实现、三处读取点共用）；
* `read_rl_config_file()` —— 读取 + 形状兜底（读不到 / 非 dict ⇒ 空 dict，等价「没配旋钮」）。

**为什么单独成家**：文件面（这份配置**在哪、怎么读**）与「课程解析面」（`curricula/*.jsonc`
查找与加载）、「类面」（课程模型的形状）互不引用——要改读取位置或兜底语义，只看这一个文件；
控制台/工具的读取点依旧走 `biz.config` 门面，名字不动。

依赖面 = stdlib（`json` / `pathlib` / `typing`）+ `common.distribution`。**不** import `rl.*` 其它模块。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import common.distribution

#: 机器本地启动配置（**永不入库**：`nn-training/.gitignore` 里 ignore 了 `rl-config.json`，
#: 它承载机器侧事实——hub 端口/token/节点表/槽位/`courses.<课>` 旋钮，控制台会直接改写它）。
RL_CONFIG_ENV = common.distribution.RL_CONFIG_ENV


def rl_config_path() -> Path:
    """rl-config.json 的**唯一**路径来源（env `BCITY_RL_CONFIG` > `nn-training/rl-config.json`）。

    实现委托给 `common.distribution.rl_config_path()`（那里是路径常量的家：dist 层的 eval 脚本也用同一份
    路径）——**一处实现、三处读取点共用**。缘由（2026-09-22，用户指令「测试应该使用自己的
    fixtures」）：路径原本硬编码在多处，于是**任何读它的用例都隐式依赖本机那份未入库的配置**
    ——本机 `rl.stream=1` 就让「`course_args` ≡ `run_rl.py`」的解析对拍变红
    （`tests/test_serve_wiring.py` 实测）：绿不绿取决于**别人机器上文件的内容**。有缝之后
    用例自带 tmp 夹具，两侧读取点都走同一个 env，对拍才是真对拍。
    """
    return Path(common.distribution.rl_config_path())


def read_rl_config_file() -> dict:
    """读 rl-config.json（读不到 / 不是 dict → 空 dict）。

    形状校验不是防御性装饰：读者按 `cfg.get("courses")` 取块，而一份顶层是数组/字符串的
    JSON（手改坏了）会让 `.get` 直接 AttributeError 落在**开课路径**上——一门课开不起来还
    看不出为什么。空 dict ⇒ 退化成「没配机器侧旋钮」，与文件不存在同一个结果。
    """
    try:
        data: Any = json.loads(rl_config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
