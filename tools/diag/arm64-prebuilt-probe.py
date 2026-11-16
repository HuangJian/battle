# -*- coding: utf-8 -*-
"""arm64 真机上的 native 内核对拍 + 吞吐探针（**设备侧**，零编译，由 arm64-prebuilt-probe.ts 推送执行）。

两条臂都来自**代码库的入库产物**（同一份 conv.c、同一版 clang、同一套链接参数，只有
`-ffp-contract` 不同）：
  A = 参考臂，默认取 `git show <rev>:src/nn/conv/prebuilt/linux-arm64/conv_native.so`
      （老版本 = `-ffp-contract=off`，`fmla 0`）⇒ 它就是本机 wasm 的等价算术，也就是生产
      attestation 的对照臂。
  B = 被测臂 = 现役 `src/nn/conv/prebuilt/linux-arm64/conv_native.so`（`-ffp-contract=fast`，
      `fmla 60`）。

不在这里编译任何东西：设备装有 Termux 的 python3，`.so` 才 9 KB，`adb push` 即用。选用 python3
而不是 C 驱动是为了让「复核入库产物」不需要设备上有 clang（clang 版本也就不会变成两头差异）。

判定口径与生产完全一致：`max|A−B| ≤ 1e-5 × max(1, max|A|)`（逐张量），即
`src/nn/conv/conv_native_adapter.ts::ATTEST_TOLERANCE`。

用法（通常由 TS 侧调用；也可在设备上直接跑）：
    python3 arm64-prebuilt-probe.py <old.so> <new.so> <wblob.bin> <in0.bin> [in1.bin ...]

输出（最后两行是机器可读锚点，TS 侧按它们解析）：
    ATTEST=PASS|FAIL
    RATIO <new/old 比值>
"""
import ctypes
import os
import sys
import time

H, SP, IN_CH = 64, 676, 19
NIN, NBUF = IN_CH * SP, H * SP
ATTEST_TOLERANCE = 1e-5  # 与 src/nn/conv/conv_native_adapter.ts 同值


def load(path):
    # 必须给绝对路径：相对名会被 bionic 当成 soname 去搜索路径里找（"library not found"）。
    lib = ctypes.CDLL(os.path.abspath(path))
    lib.cf_abi.restype = ctypes.c_uint32
    abi = lib.cf_abi()
    if abi != 2:
        raise SystemExit(f"ABI 不符 {path}: {abi}（NATIVE_ABI=2）")
    P = ctypes.POINTER(ctypes.c_float)
    lib.cf_student_features.argtypes = [P, P, P, P]
    lib.cf_student_features.restype = ctypes.c_int
    return lib


def floats(path, n=None):
    data = open(path, "rb").read()
    cnt = n if n is not None else len(data) // 4
    if len(data) != cnt * 4:
        raise SystemExit(f"{path} 尺寸不符: {len(data)} != {cnt * 4}（期望 {cnt} 个 float）")
    return (ctypes.c_float * cnt).from_buffer_copy(data)


def run(lib, blob, in16, pooled, buf):
    rc = lib.cf_student_features(blob, in16, pooled, buf)
    if rc != 0:
        raise SystemExit(f"kernel rc={rc}（{lib._name}）")


def main():
    if len(sys.argv) < 5:
        raise SystemExit(__doc__)
    old_path, new_path, wblob_path = sys.argv[1], sys.argv[2], sys.argv[3]
    blob = floats(wblob_path)
    A, B = load(old_path), load(new_path)

    pA = (ctypes.c_float * H)()
    bA = (ctypes.c_float * NBUF)()
    pB = (ctypes.c_float * H)()
    bB = (ctypes.c_float * NBUF)()
    print(f"wblob {len(blob)} floats · old={os.path.basename(old_path)} new={os.path.basename(new_path)}")

    worst_p = worst_b = 0.0
    worst_p_scale = worst_b_scale = 1.0
    ok_all = True
    for k, path in enumerate(sys.argv[4:]):
        in16 = floats(path, NIN)
        run(A, blob, in16, pA, bA)
        run(B, blob, in16, pB, bB)
        sp = max(1.0, max(abs(x) for x in pA))
        sb = max(1.0, max(abs(x) for x in bA))
        dp = max(abs(pA[i] - pB[i]) for i in range(H))
        db = max(abs(bA[i] - bB[i]) for i in range(NBUF))
        ok = dp <= ATTEST_TOLERANCE * sp and db <= ATTEST_TOLERANCE * sb
        ok_all = ok_all and ok
        print(
            f"in{k}  pooled max|old|={sp:.4g} diff={dp:.3e} bound={ATTEST_TOLERANCE * sp:.3e} | "
            f"bufA max|old|={sb:.4g} diff={db:.3e} bound={ATTEST_TOLERANCE * sb:.3e} "
            f"=> {'PASS' if ok else 'FAIL'}"
        )
        if dp > worst_p:
            worst_p, worst_p_scale = dp, sp
        if db > worst_b:
            worst_b, worst_b_scale = db, sb
    print(
        f"最差: pooled {worst_p:.3e} (scale {worst_p_scale:.4g}, 相对 {worst_p / worst_p_scale:.2e}) · "
        f"bufA {worst_b:.3e} (scale {worst_b_scale:.4g}, 相对 {worst_b / worst_b_scale:.2e})"
    )

    # 吞吐：成对交替，各取最小（规避热节流/调度抖动）
    in0 = (ctypes.c_float * NIN)(*([0.5] * NIN))
    ROUNDS, ITER = 60, 20
    bestA = bestB = float("inf")
    for r in range(ROUNDS):
        first, second = (B, A) if (r & 1) else (A, B)
        t0 = time.perf_counter()
        for _ in range(ITER):
            run(first, blob, in0, pA, bA)
        d1 = (time.perf_counter() - t0) / ITER
        t0 = time.perf_counter()
        for _ in range(ITER):
            run(second, blob, in0, pA, bA)
        d2 = (time.perf_counter() - t0) / ITER
        da, db = (d2, d1) if (r & 1) else (d1, d2)
        bestA = min(bestA, da)
        bestB = min(bestB, db)
    print(
        f"内核稳态: old(no-fma) {bestA * 1e3:.3f} ms · new(fma) {bestB * 1e3:.3f} ms · "
        f"比值 {bestB / bestA:.4f}×（fma 节省 {(1 - bestB / bestA) * 100:.1f}%）"
    )
    print("ATTEST=" + ("PASS" if ok_all else "FAIL"))
    print(f"RATIO {bestB / bestA:.4f}")
    return 0 if ok_all else 5


if __name__ == "__main__":
    sys.exit(main())
