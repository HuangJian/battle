# Archived one-off diagnostic scripts

Moved out of `tools/diag/` by plan/refactor.agy.md §3.7 (DECISIONS §259).
Each was verified to have **zero references** — no imports from src/tests/tools,
no mentions in AGENTS.md / DECISIONS.md / docs/*.progress.md.

These are closed-investigation forensics kept for provenance; they are NOT
part of the standing toolkit (run-forensics / per-seed-diff / decision-probe /
ab-* / base-loss-*). Do not resurrect them without re-verifying they compile
against current World/Simulation APIs.

## Second batch — plan/refactor.trae.md §2.5 (2026-08-24)

Added from three directories after a fresh zero-reference sweep (src/tests/
tools imports + package.json + AGENTS/DECISIONS/docs/plan/README; session
logs excluded):

- from `tools/replay/`: analyze-p2, probe-p2-stuck, repair-coop-replay,
  repro-seek-audio, who-broke-wall → now in `tools/replay/archive/`
- `freeze-thrash-audit.ts` (from tools/sim/) — freeze/thrash tick audit;
  superseded by the standing perf harness
- `probe-params.ts` (from tools/optimize/) — one-off param probe
- `diag-weak-stages.ts` — one-off weak-stage census

**Revival conditions** (same as above): a named investigation needs the
exact behavior again AND the file compiles against current APIs. Note the
imports were depth-fixed for this location (`../../../src`, `../../lib`);
if moved back, fix them again.
