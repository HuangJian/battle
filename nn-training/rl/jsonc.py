"""jsonc —— JSONC 子集加载器（限行注释，评审 LC §2.1）。

为什么不用 YAML：类型强制转换会静默改写 `off`/`on`/`1e-4` 等值，且 JSON 本就是
下发 TS 的线格式。为什么不用 json5：多一个依赖。

**只支持 `//` 行注释**（弃 `/* */` 块注释）：状态机退化为「是否在字符串内 +
是否已遇 `//`」，<30 行，edge case 仅剩字符串内 `//`（须跟踪转义奇偶）；块注释
的 `/* // */` vs `// /*` 嵌套歧义被整体消除。

校验/加载流程：`strip_comments` → `json.loads` → pydantic 模型（见 rl/config.py）。
"""

from __future__ import annotations


def strip_comments(src: str) -> str:
    """剥离 `//` 行注释，字符串内的 `//` 原样保留。

    不支持 `/* */`——遇到 `/*` 按普通字符处理（内容里若真有块注释，会在随后的
    `json.loads` 处响亮报错，不会静默误剥）。
    """
    out: list[str] = []
    i = 0
    n = len(src)
    in_str = False
    quote = ""
    esc = False
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
            i += 1
            continue
        if c == '"' or c == "'":
            in_str = True
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            # 吃掉到行尾（保留换行，行号不漂移 → json 报错定位准确）
            while i < n and src[i] != "\n":
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def loads(src: str) -> dict:
    """`strip_comments` + `json.loads`（JSONC 文本 → dict）。"""
    import json

    return dict(json.loads(strip_comments(src)))


def load(path: str) -> dict:
    """从文件读 JSONC（.jsonc / .json 皆可）。"""
    with open(path, encoding="utf-8") as f:
        return loads(f.read())
