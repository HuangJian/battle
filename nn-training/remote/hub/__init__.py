"""remote/hub —— hub 服务端的子模块（2026-09-23 S4 第三步起）。

本包目前只装从 `remote/hub_server.py` 拆出的职责簇；`hub_server.py` 保留组装与门面。
**不 import `remote.hub_server`**（否则与「hub_server import 本包拿混入」成环）。
"""
