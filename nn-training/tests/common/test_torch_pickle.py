"""tests/common/test_torch_pickle.py — 无 torch 环境读写的 torch.save 容器（Paddle 双生，2026-10-27）。

用户口径：**hub 统一收发 torch 格式**，Paddle worker 自己转换；两类 worker 混跑同一门课。
本文件是那条口径的机器判据 —— `common/torch_pickle.py` 必须与**真 torch 2.7** 双向互通：

  ① **读**：torch 写的 model/opt state_dict → numpy，值与 dtype/形状逐项相同；
  ② **写**：numpy 对象图 → 字节，`torch.load(weights_only=True)`（2.7 默认）能直接吃，
     且容器类型（OrderedDict）、0-dim `step`、4 维 conv 权重、bool 张量都不走样；
  ③ **链**：torch → numpy → 我们的字节 → torch，两次 torch 结果逐元素相同（转换不累积偏移）；
  ④ **消毒同源**：`sanitize_adam_sq_np` 与 torch 版对同一组随机状态读数/结果逐项一致；
  ⑤ **响亮拒收**：非 zip / 根级 data.pkl / 未知全局 ⇒ `ProtocolError`，绝不猜；
  ⑥ **层纪律（行为面）**：把 torch/numpy 设成「导入即炸」后模块照样 import 得进来
     —— 判据是 `sys.modules[name] = None` + 真 import，不是扫源码字样（2026-10-27 评审）。
"""

from __future__ import annotations

import collections
import importlib
import io
import pickle
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import torch_pickle
from common.errors import ProtocolError
from common.torch_pickle import dump_torch_pickle, load_torch_pickle
from remote import opt_delta as od


def _model_state() -> collections.OrderedDict:
    torch.manual_seed(7)
    return collections.OrderedDict(
        [
            ("stem.weight", torch.randn(8, 5, 3, 3)),
            ("stem.bias", torch.randn(8)),
            ("blocks.0.dw.weight", torch.randn(8, 1, 5, 5)),
            ("fc.weight", torch.randn(4, 9)),
            ("fc.bias", torch.zeros(4)),
        ]
    )


def _opt_state() -> dict:
    torch.manual_seed(11)
    return {
        "state": {
            0: {
                "step": torch.tensor(321.0),
                "exp_avg": torch.randn(8, 5, 3, 3),
                "exp_avg_sq": torch.rand(8, 5, 3, 3).abs(),
            },
            1: {"step": torch.tensor(321.0), "exp_avg": torch.randn(8), "exp_avg_sq": torch.rand(8)},
        },
        "param_groups": [
            {
                "lr": 1.5e-4,
                "betas": (0.9, 0.999),
                "eps": 1e-8,
                "weight_decay": 0.0,
                "amsgrad": False,
                "maximize": False,
                "foreach": None,
                "capturable": False,
                "differentiable": False,
                "fused": None,
                "decoupled_weight_decay": False,
                "params": [0, 1],
            }
        ],
    }


def _torch_dtype_name(dtype: torch.dtype) -> str:
    return str(dtype).removeprefix("torch.")


def _assert_matches_numpy(ref: dict, got: dict) -> None:
    assert type(got) is type(ref), (type(ref), type(got))
    assert list(got) == list(ref)
    for key, want in ref.items():
        if isinstance(want, torch.Tensor):
            arr = got[key]
            assert isinstance(arr, np.ndarray), key
            assert tuple(want.shape) == arr.shape, key
            assert arr.dtype.name == _torch_dtype_name(want.dtype), key
            np.testing.assert_array_equal(arr, want.detach().numpy())
        elif isinstance(want, dict):
            _assert_matches_numpy(want, got[key])
        else:
            assert got[key] == want, key


# ------------------------------------------------------------------ ① 读


def test_reads_torch_model_and_optimizer_state(tmp_path: Path) -> None:
    sd = _model_state()
    opt = _opt_state()
    torch.save(sd, tmp_path / "model.pt")
    torch.save(opt, tmp_path / "opt.pt")

    _assert_matches_numpy(sd, load_torch_pickle((tmp_path / "model.pt").read_bytes()))
    got_opt = load_torch_pickle((tmp_path / "opt.pt").read_bytes())
    assert got_opt["param_groups"] == opt["param_groups"]
    for idx in (0, 1):
        step = got_opt["state"][idx]["step"]
        assert step.shape == () and step.dtype == np.float32
        np.testing.assert_array_equal(step, np.array(321.0, dtype=np.float32))
        _assert_matches_numpy(opt["state"][idx], got_opt["state"][idx])


# ------------------------------------------------------------------ ② 写


def test_writes_archive_torch_load_accepts(tmp_path: Path) -> None:
    arrays = collections.OrderedDict(
        [
            ("conv.weight", np.arange(72, dtype=np.float32).reshape(2, 3, 3, 4)),
            ("bias", np.linspace(-1, 1, 5, dtype=np.float32)),
            ("step", np.array(17.0, dtype=np.float32)),
            ("mask", np.array([True, False], dtype=np.bool_)),
            ("count", np.array([2**40], dtype=np.int64)),
            ("strided", np.arange(12, dtype=np.float32).reshape(3, 4)[:, ::2]),
        ]
    )
    blob = dump_torch_pickle(arrays, prefix="model")
    got = torch.load(io.BytesIO(blob), map_location="cpu")

    assert type(got) is collections.OrderedDict
    for key, want in arrays.items():
        tensor = got[key]
        assert isinstance(tensor, torch.Tensor), key
        assert tuple(tensor.shape) == want.shape, key
        assert tensor.dtype == torch.from_numpy(np.ascontiguousarray(want)).dtype, key
        np.testing.assert_array_equal(tensor.numpy(), np.ascontiguousarray(want))

    opt = _opt_state()
    as_np = {
        "state": {
            i: {k: v.numpy() if isinstance(v, torch.Tensor) else v for k, v in node.items()}
            for i, node in opt["state"].items()
        },
        "param_groups": opt["param_groups"],
    }
    got_opt = torch.load(io.BytesIO(dump_torch_pickle(as_np, prefix="opt")), map_location="cpu")
    assert got_opt["param_groups"] == opt["param_groups"]
    for idx in (0, 1):
        assert got_opt["state"][idx]["step"].dim() == 0
        for name, want in opt["state"][idx].items():
            np.testing.assert_array_equal(got_opt["state"][idx][name].numpy(), want.numpy())
    # 2.7 的默认就是 weights_only=True；显式再跑一遍，钉住「未来默认变了也不受影响」。
    torch.load(io.BytesIO(blob), map_location="cpu", weights_only=True)


def test_archive_layout_matches_torch_contract() -> None:
    as_np = {k: v.numpy() for k, v in _model_state().items()}
    blob = dump_torch_pickle(as_np, prefix="model")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        assert names[0] == "model/data.pkl"
        assert zf.read("model/version") == b"3\n"
        assert zf.read("model/byteorder") == b"little"
        assert zf.read("model/.format_version") == b"1"
        storage_names = [n for n in names if n.startswith("model/data/")]
        assert storage_names == [f"model/data/{i}" for i in range(len(storage_names))]
        assert zf.read("model/data.pkl")[:2] == b"\x80\x02"


# ------------------------------------------------------------------ ③ 链


def test_torch_to_numpy_to_torch_round_trip(tmp_path: Path) -> None:
    opt = _opt_state()
    torch.save(opt, tmp_path / "opt.pt")
    first = torch.load(tmp_path / "opt.pt", map_location="cpu")

    mid = load_torch_pickle((tmp_path / "opt.pt").read_bytes())
    second = torch.load(io.BytesIO(dump_torch_pickle(mid, prefix="opt")), map_location="cpu")

    assert second["param_groups"] == first["param_groups"]
    for idx in first["state"]:
        for key, want in first["state"][idx].items():
            np.testing.assert_array_equal(second["state"][idx][key].numpy(), want.numpy())


# ------------------------------------------------------------------ ④ 消毒同源


def test_sanitize_numpy_matches_torch() -> None:
    rng = np.random.default_rng(5)
    for _ in range(20):
        shape = tuple(int(x) for x in rng.integers(1, 5, size=3))
        m = rng.normal(size=shape).astype(np.float32)
        v = np.where(rng.random(shape) < 0.3, 0.0, np.abs(m) ** 2 * 0.01).astype(np.float32)
        t = {
            "state": {
                0: {
                    "step": torch.tensor(10.0),
                    "exp_avg": torch.from_numpy(m.copy()),
                    "exp_avg_sq": torch.from_numpy(v.copy()),
                },
                1: {
                    "step": torch.tensor(10.0),
                    "exp_avg": torch.from_numpy(m.copy()),
                    "exp_avg_sq": torch.from_numpy(v.copy()),
                    "max_exp_avg_sq": torch.from_numpy(v.copy()),
                },
            }
        }
        n = {
            "state": {
                0: {
                    "step": np.array(10.0, dtype=np.float32),
                    "exp_avg": m.copy(),
                    "exp_avg_sq": v.copy(),
                },
                1: {
                    "step": np.array(10.0, dtype=np.float32),
                    "exp_avg": m.copy(),
                    "exp_avg_sq": v.copy(),
                    "max_exp_avg_sq": v.copy(),
                },
            }
        }
        assert od.sanitize_adam_sq(t) == od.sanitize_adam_sq_np(n)
        np.testing.assert_array_equal(
            n["state"][0]["exp_avg_sq"], t["state"][0]["exp_avg_sq"].numpy()
        )
        # amsgrad 节点两侧都显式跳过。
        np.testing.assert_array_equal(n["state"][1]["exp_avg_sq"], v)
        assert od.sanitize_adam_sq_np(n) == 0  # 幂等


# ------------------------------------------------------------------ ⑤ 拒收


def test_rejects_foreign_archives(tmp_path: Path) -> None:
    with pytest.raises(ProtocolError, match="zip"):
        load_torch_pickle(b"definitely not a zip")

    root_zip = io.BytesIO()
    with zipfile.ZipFile(root_zip, "w") as zf:
        zf.writestr("data.pkl", pickle.dumps({"x": 1}, protocol=2))
    with pytest.raises(ProtocolError, match="子目录"):
        load_torch_pickle(root_zip.getvalue())

    torch.save({"x": ValueError("boom")}, tmp_path / "foreign.pt")
    with pytest.raises(ProtocolError, match="不认识"):
        load_torch_pickle((tmp_path / "foreign.pt").read_bytes())


def test_writer_rejects_unknown_dtype() -> None:
    with pytest.raises(ProtocolError, match="dtype"):
        dump_torch_pickle({"x": np.array([1 + 2j], dtype=np.complex64)})


# ------------------------------------------------------------------ ⑥ 层纪律


def test_import_survives_without_torch_or_numpy(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 层纪律的**行为**判据（2026-10-27 评审换掉原来那条 AST 文本扫描）。

    要保证的行为 = 「hub 机与 Paddle worker 在**没装 torch/numpy** 的机器上 import 得进来」
    （本模块是 `remote/opt_standard` 与 `worker/ppo/checkpoint_paddle` 的共同底座）。
    做法与 `test_ppo_backend_switch` 那族的「`sys.modules[name] = None` 等价于没装」同款：
    把两个框架都设成「导入即炸」，再真 import 一次模块体。
    """
    for name in ("torch", "numpy"):
        monkeypatch.setitem(sys.modules, name, None)
    importlib.reload(torch_pickle)
