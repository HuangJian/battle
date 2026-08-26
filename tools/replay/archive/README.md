# Archived replay one-off scripts

Moved out of `tools/replay/` by plan/refactor.trae.md §2.5 (2026-08-24),
mirroring `tools/diag/archive/` (DECISIONS §259 methodology). Each was
verified to have **zero references** — no imports from src/tests/tools, no
mentions in AGENTS.md / DECISIONS.md / docs / plan / README.

These are closed-investigation forensics kept for provenance; the standing
replay toolkit is verify-replay.ts / verify-guard-replay.ts / repro-seek.ts.
Do not resurrect them without re-verifying they compile against current
World/Simulation/replay APIs. Imports were depth-fixed for this location
(`../../../src`) — fix again if moved.
