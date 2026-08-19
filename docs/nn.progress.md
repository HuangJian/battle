# NN Player AI — Training Progress Log

> All architecture changes, eval results, and lessons learned are recorded here.
> New entries are appended at the top (reverse chronological).

---

## §1 v2: Scalar Fusion Architecture (2026-08-19)

### What changed

The v1 backbone ignored all 24 scalar inputs. v2 concatenates the 24-dim scalar vector with the GAP output before the FC layer:

```
v1:  obs(14×26×26) → Conv(32→48→64) → GAP → FC(64→64) → heads
v2:  obs(14×26×26) → Conv(32→48→64) → GAP → cat(scalars) → FC(88→64) → heads
```

**FC layer input**: 64 (GAP) + 24 (scalars) = 88. Weight shape [64, 88].

### Files modified

| File | Change |
|------|--------|
| `nn-training/model.py` | `nn.Linear(c + scalar_dim, head_hidden)` + `torch.cat([x, scalars], dim=1)` |
| `nn-training/weights_io.py` | `load_state_into` tolerates FC shape mismatch (loads 13/14 params, skips FC) |
| `src/nn/infer.ts` | `fusedBuf` = pooled + scalars → FC; TS forward matches Python exactly |

### Parameter count

| | v1 | v2 |
|--|-----|-----|
| Total | ~50K | ~52K |
| FC input dim | 64 | 88 |
| FC params | 4,160 | 5,728 |

### Training warm-start strategy

Old conv weights (13/14 params) loaded into v2 model. FC layer randomly initialized.
This preserves learned spatial features while the FC layer learns to use scalar inputs from scratch.

### First epoch results (warm-started from v1 R10)

| Epoch | train_loss | val_loss | move_acc | fire_acc |
|-------|-----------|----------|----------|----------|
| 1 | 1.7835 | 1.3834 | 0.590 | 0.852 |
| 2 | 1.2787 | 1.3521 | 0.583 | 0.851 |

val_loss 1.35 at epoch 2 is already lower than v1's from-scratch start (1.91),
confirming the warm-start works — conv features transfer.

### Training timeline (v2, 68K samples)

| Round | val_loss | Δ vs R2 | Interpretation |
|-------|----------|---------|----------------|
| R1 | 1.1974 | +21.9% | Starting point |
| **R2** | **0.9984** | — | 🏆 Best — breaks v1 ceiling (1.0919) |
| R3 | 1.0066 | +0.8% | Plateau |
| R4 | 1.0172 | +1.9% | Overfitting begins |
| R5 | 1.0256 | +2.7% | — |
| R6 | 1.0342 | +3.6% | — |
| R7 | 1.0481 | +5.0% | — |

**Pattern**: same as v1 — val_loss bottoms at R2, then monotonically increases.
Scalar fusion lowered the ceiling (0.998 vs 1.092) but didn't change the shape.

### M1 Sim Eval (v2, best weights R2 val_loss=0.9984)

```
policy=nn  difficulty=hard  35 stages × 10 seeds = 350 games
WIN RATE 0.0% (gate 60%) → FAIL
SCORE V7 suite=0.1087  lcb=0.1071  meanWinRate=0
avgKills=3.04  avgTicks=4207
```

**All 350 games ended in gameover.** 0% win rate — same as v1 despite val_loss
improving 8.4% (1.0919 → 0.9984).

### v1 vs v2 comparison

| Metric | v1 (no scalars) | v2 (scalar fusion) | Δ |
|--------|-----------------|--------------------|----|
| val_loss | 1.0919 | **0.9984** | -8.4% ↓ |
| Win rate | 0.0% | 0.0% | — |
| Avg kills | 2.6 | **3.04** | +17% ↑ |
| Avg ticks | 4755 | 4207 | -11% |
| Score V7 | 0.1085 | 0.1087 | +0.2% |

**Key finding**: Scalar fusion improved learning (val_loss ↓, kills ↑) but didn't
improve winning. The model kills 17% more enemies but still can't survive to
clear a stage.

### Per-stage highlights

| Stage | avgKills | Notes |
|-------|----------|-------|
| Ramparts | 8.0 | Highest kills — still 0% win |
| Waterways | 6.5 | — |
| Eagle Nest | 6.4 | — |
| Checkers | **0.0** | Complete paralysis — 0 kills in all 10 games |
| Iron Curtain | 1.1 | — |
| Gauntlet | 1.3 | Worst score V7 (0.089) |

### Why scalar fusion didn't help winning

The model can now "see" lives, base distance, enemy distribution, etc. But it
still can't *act on* this information effectively. Root causes:

1. **BC distribution shift still dominates**
   - Scalar fusion reduces the information gap but doesn't fix the fundamental
     problem: once the NN's trajectory diverges from the human's, it can't recover
   - The model needs to be *robust* to its own mistakes, not just accurate on the
     first few decisions

2. **7×7 receptive field can't capture global strategy**
   - 3 layers of 3×3 conv → 7×7 receptive field on a 26×26 board
   - Model can't reason about "enemies are coming from the north, base is south"
   - Scalars give relative positions but the spatial backbone can't plan paths

3. **Checkers stage = complete failure mode**
   - 0 kills in 10 games — the model literally cannot move or shoot
   - Suggests the model has learned a brittle policy that collapses on certain
     terrain layouts

### Lessons learned (v2 additions)

7. **Scalar fusion is necessary but not sufficient** — the model needs scalars to
   make context-aware decisions, but scalars alone don't solve distribution shift
8. **val_loss continues to be a poor game-performance proxy** — 8.4% improvement
   with zero win-rate improvement
9. **Receptive field is the next bottleneck** — model can see the data but can't
   reason about spatial relationships beyond 7×7
10. **BC has a fundamental ceiling on hard difficulty** — the model needs to be
    robust to its own mistakes, which BC doesn't train for

### Status (2026-08-19)

**v2 scalar fusion: 0% win rate on hard. BC approach has reached its ceiling.**

Next options:
- Train on classic difficulty (easier → model can learn complete strategies)
- Switch to RL (reinforcement learning) — train with win/loss signals
- Increase model capacity (deeper conv, attention mechanism)

---

## §0 v1: Conv-Only Baseline (2026-08-18 → 2026-08-19)

### Architecture

```python
# nn-training/model.py v1
class NNPolicy(nn.Module):
    # Conv backbone: 14ch → 32 → 48 → 64, 3×3 kernels
    # GAP → FC(64→64) → ReLU → 3 heads (move/fire/item)
    # scalars parameter: ACCEPTED but IGNORED in forward()
    def forward(self, obs, scalars):
        x = obs.float()
        x = self.conv(x)           # (B, 64, 26, 26)
        x = self.gap(x)            # (B, 64, 1, 1)
        x = x.flatten(1)           # (B, 64)
        h = self.fc_relu(self.fc(x))  # (B, 64)  ← scalars NOT used
        return self.move_head(h), self.fire_head(h), self.item_head(h)
```

**Fatal flaw**: `scalars` parameter accepted but never concatenated into the FC input.
The model had no access to: lives, base distance, enemy distance, fire cooldown, ring integrity, inventory, etc.

### Training timeline

| Phase | Dates | Samples | Rounds | Best val_loss | Notes |
|-------|-------|---------|--------|---------------|-------|
| Initial baseline | 8/18 17:00 | 43,566 | 1×40ep | 1.2431 | First training run |
| Continuous 40ep | 8/18 21:08–23:30 | 43,566 | 3×40ep | **1.1320** | val_loss rebounded after R2 |
| Corpus expansion | 8/19 07:46 | 68,571 | 21×1ep | 1.4083 | From scratch after venv rebuild |
| Continuous 40ep | 8/19 09:54–15:07 | 68,571 | 9×40ep | **1.0919** (R2) | val_loss rebounded from R3 onward |

### val_loss trend (68K samples, v1)

| Round | val_loss | Δ vs R2 | Interpretation |
|-------|----------|---------|----------------|
| R1 | 1.1974 | +9.7% | Starting point |
| **R2** | **1.0919** | — | 🏆 Best |
| R3 | 1.0974 | +0.5% | Plateau |
| R4 | 1.1192 | +2.5% | Overfitting begins |
| R5 | 1.1422 | +4.6% | — |
| R6 | 1.1499 | +5.3% | — |
| R7 | 1.1639 | +6.6% | — |
| R8 | 1.1601 | +6.2% | — |
| R9 | 1.1625 | +6.5% | — |

**Pattern**: val_loss bottoms at R2, then monotonically increases — textbook overfitting.

### M1 Sim Eval (v1, best weights R2 val_loss=1.0919)

```
policy=nn  difficulty=hard  35 stages × 10 seeds = 350 games
WIN RATE 0.0% (gate 60%) → FAIL
SCORE V7 suite=0.1085  lcb=0.1069  meanWinRate=0
avgKills=2.5  avgTicks=4234
```

**All 350 games ended in gameover.** 0% win rate — same as the initial baseline
despite val_loss improving 12% (1.2431 → 1.0919).

### Per-stage highlights

| Stage | avgKills | progress | baseIntegrity | mobility | accuracy |
|-------|----------|----------|---------------|----------|----------|
| Waterways (best) | 5.1 | 0.255 | 0.244 | 0.169 | 0.300 |
| Lattice (worst) | 0.6 | 0.030 | 0.072 | 0.085 | — |
| Ramparts | 4.0 | 0.200 | 0.182 | 0.484 | — |
| Steel Fortress | 2.5 | 0.125 | 0.000 | 0.314 | 0.552 |

**Key observations**:
- `baseIntegrity` ≈ 0 on most stages → base always destroyed
- `progress` ≤ 0.255 → kills at most 25% of enemies
- `mobility` ≤ 0.48 → limited map exploration
- No correlation between avgKills and score — killing more doesn't help if you can't protect the base

### Corpus analysis

**94.2% of training replays are wins** (98/104 cleared all enemies).
Only 6 losses in the corpus (partial clears on Bunker Hill, Labyrinth, Brick Maze, Spider).

This means the NN was trained primarily on winning trajectories but couldn't reproduce them in sim.

### Root cause analysis

#### Why val_loss ↓12% but win rate = 0%

1. **BC loss measures imitation accuracy, not winning ability**
   - val_loss = cross-entropy between NN predictions and human actions
   - A model that perfectly mimics a winning trajectory should win — unless it can't
     maintain the trajectory under distribution shift

2. **Distribution shift (the real killer)**
   - Training: given obs_t, predict action_t (ground truth from human replay)
   - Inference: NN's action_0 may match human, but action_1 diverges slightly →
     obs_1 diverges → action_2 diverges more → ... → cascade failure
   - Even 94% winning training data can't prevent this if the NN lacks the information
     needed to make the same decisions as the human

3. **Missing scalar inputs = missing decision context**
   - Human player decides "retreat to base" based on knowing: "I have 1 life left,
     base ring is damaged, enemy is approaching from the north"
   - NN only sees the 14-channel spatial snapshot — it can't distinguish "aggressive
     push" from "desperate retreat" without scalar context
   - The 24 scalar features (lives, base distance, fire cooldown, enemy count, etc.)
     were available in the encoding but never fed to the model

4. **Model capacity bottleneck**
   - 50K params for 68K samples — near the capacity boundary
   - GAP compresses 26×26 spatial info to 64 dims — heavy information loss
   - 3×3 convs have 7×7 receptive field — can't capture long-range spatial relationships

#### Why move_acc improved but didn't help

- move_acc 0.586 → 0.709 over 10 rounds
- But accuracy is measured against **human actions**, not **optimal actions**
- The human's movement in winning replays is context-dependent — "go left" is only
  correct when you know the base is to the right and enemies are above
- Without scalar context, the NN learns a statistical average of directions, not
  a context-aware policy

### Lessons learned

1. **Never ignore available inputs** — if scalars are encoded, they must be consumed
2. **val_loss is a poor proxy for game performance** — always validate with sim eval
3. **BC requires the model to see everything the human sees** — otherwise distribution
   shift makes inference unreliable
4. **Warm-starting conv weights is effective** — v2 epoch 1 val_loss (1.38) already
   below v1 from-scratch start (1.91)
5. **94% winning corpus ≠ easy BC** — distribution shift dominates even with clean data
6. **Architecture changes require `load_state_into` tolerance** — shape mismatches
   should be caught and handled gracefully, not crash the training loop

---

## §-1 Pre-history (before 2026-08-18)

Training infrastructure established:
- `nn-training/train_loop.py` — continuous training loop with auto-resume
- `nn-training/train_bc.py` — behavior cloning trainer
- `nn-training/start-training.sh` — launch script with VBS detach on Windows
- `tools/replay/export-observations.ts` — NDJSON → npy shard exporter
- `src/nn/infer.ts` — TS runtime inference
- `src/nn/policy-input.ts` — NNInput InputLike implementation
- `src/nn/obs-encoder.ts` — 14-channel spatial + 24-dim scalar encoder
