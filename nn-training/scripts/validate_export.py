#!/usr/bin/env python3
"""
validate_export.py — network-independent proof of the TS->Python npy contract.

The Battle City exporter (src/nn/npy.ts) writes *raw* .npy (magic + v1/v2 header +
C-order bytes). This script reads those files with a from-scratch parser (no numpy,
no torch — pure stdlib) and asserts:
  * obs            : (N,14,26,26) uint8
  * scalars        : (N,24)      float32
  * actions        : (N,3)       uint8   (move, fire, item)
  * masks          : (N,10)      uint8   (move5, fire2, item3 — 0/1)
  * conditions     : (N,)        uint8   (0 turn / 1 fire / 2 item / 3 subsample)
plus label-range / mask-consistency sanity. If this passes, numpy.load (and the
trainer) will read the same bytes identically.

Usage:
  python validate_export.py <path-to-shards-root>
  python validate_export.py ../battle2/tmp/nn-export
"""

import os
import re
import struct
import sys

EXPECT = {
    "obs.npy": ("<u1", (14, 26, 26)),
    "scalars.npy": ("<f4", (19,)),
    "actions.npy": ("<u1", (2,)),  # v2: [move, fire]，item 头删除
    "masks.npy": ("<u1", (7,)),  # v2: [move5, fire2]
    "conditions.npy": ("<u1", ()),
}


def read_npy(path):
    with open(path, "rb") as f:
        assert f.read(6) == b"\x93NUMPY", f"{path}: bad magic"
        major = f.read(1)[0]
        f.read(1)  # minor
        hlen = (
            struct.unpack("<H", f.read(2))[0] if major == 1 else struct.unpack("<I", f.read(4))[0]
        )
        header = f.read(hlen).decode("latin1")
        m_descr = re.search(r"'descr':\s*'([^']+)'", header)
        assert m_descr is not None, f"{path}: missing 'descr' in npy header"
        descr = m_descr.group(1)
        m_shape = re.search(r"'shape':\s*\(([^)]*)\)", header)
        assert m_shape is not None, f"{path}: missing 'shape' in npy header"
        shp = m_shape.group(1)
        shape = tuple(int(x) for x in re.findall(r"\d+", shp))
        data = f.read()
    return descr, shape, data


def to_array(descr, shape, data):
    n = 1
    for d in shape:
        n *= d
    if descr in ("|u1", "<u1"):
        assert len(data) == n, f"uint8 len {len(data)} != {n}"
        return list(data)
    if descr == "<f4":
        assert len(data) == n * 4, f"f4 len {len(data)} != {n * 4}"
        return list(struct.unpack("<" + "f" * n, data))
    raise ValueError(f"unsupported descr {descr}")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "../battle2/tmp/nn-export"
    if not os.path.isdir(root):
        print(f"ERROR: shards root not found: {root}")
        sys.exit(2)

    shards = sorted(
        d for d in (os.path.join(root, e) for e in os.listdir(root)) if os.path.isdir(d)
    )
    if not shards:
        print(f"ERROR: no shard dirs under {root}")
        sys.exit(2)

    total = 0
    cond_counts = [0, 0, 0, 0]
    action_range = {"move": [99, -1], "fire": [99, -1]}
    mask_bad = 0
    scalar_oob = 0
    obs_nonempty = 0
    errs = []

    for sd in shards:
        files = {f: os.path.join(sd, f) for f in EXPECT}
        if not all(os.path.exists(p) for p in files.values()):
            errs.append(f"{os.path.basename(sd)}: missing npy file(s)")
            continue
        arrays = {}
        for fname, (edescr, eshape) in EXPECT.items():
            descr, shape, raw = read_npy(files[fname])
            if descr != edescr:
                errs.append(f"{os.path.basename(sd)}/{fname}: descr {descr} != {edescr}")
            # compare trailing dims (leading N may differ per shard)
            if tuple(shape[1:]) != eshape:
                errs.append(f"{os.path.basename(sd)}/{fname}: shape tail {shape[1:]} != {eshape}")
            arrays[fname] = to_array(descr, shape, raw)
        N = len(arrays["conditions.npy"])  # (N,) -> N rows
        total += N

        conds = arrays["conditions.npy"]
        for c in conds:
            if 0 <= c <= 3:
                cond_counts[c] += 1
            else:
                errs.append(f"{os.path.basename(sd)}: bad condition value {c}")

        acts = arrays["actions.npy"]
        for i in range(N):
            m, fr = acts[i * 2], acts[i * 2 + 1]
            action_range["move"] = [
                min(action_range["move"][0], m),
                max(action_range["move"][1], m),
            ]
            action_range["fire"] = [
                min(action_range["fire"][0], fr),
                max(action_range["fire"][1], fr),
            ]
            if not (0 <= m <= 4):
                errs.append(f"{os.path.basename(sd)}: move label {m} out of [0,4]")
            if not (0 <= fr <= 1):
                errs.append(f"{os.path.basename(sd)}: fire label {fr} out of [0,1]")

        masks = arrays["masks.npy"]
        for i in range(N):
            mrow = masks[i * 7 : i * 7 + 7]
            if any(v not in (0, 1) for v in mrow):
                mask_bad += 1
            if mrow[0] != 1 or mrow[5] != 1:
                # move[0], fire[0](release-valid) must always be 1
                mask_bad += 1

        sc = arrays["scalars.npy"]
        # All scalars are normalized: most in [0,1]; the relative-direction
        # x/y components (indices 14,15,16,17,18) are in [-1,1]. So the valid
        # range for every scalar is [-1,1].
        for v in sc:
            if not (-1.001 <= v <= 1.001):
                scalar_oob += 1

        # obs non-empty check: at least one non-zero cell per sample
        obs = arrays["obs.npy"]
        L = 14 * 26 * 26
        for i in range(N):
            if any(obs[i * L : i * L + L]):
                obs_nonempty += 1

    print(f"shards            : {len(shards)}")
    print(f"total samples     : {total}")
    print(
        f"condition dist    : turn={cond_counts[0]} fire={cond_counts[1]} item={cond_counts[2]} subsample={cond_counts[3]}"
    )
    print(
        f"action ranges     : move={tuple(action_range['move'])} fire={tuple(action_range['fire'])}"
    )
    print(f"obs non-empty     : {obs_nonempty}/{total}")
    print(f"mask violations   : {mask_bad}")
    print(f"scalar out-of-[0,1]: {scalar_oob}")
    if errs:
        print(f"\nERRORS ({len(errs)}):")
        for e in errs[:20]:
            print("  - " + e)
        sys.exit(1)
    # sanity assertions on the contract
    ok = total > 0 and mask_bad == 0 and scalar_oob == 0 and obs_nonempty == total
    print("\nRESULT:", "PASS — TS->Python npy contract verified" if ok else "FAIL — see above")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
