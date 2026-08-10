import { describe, it, expect } from 'bun:test'
import {
  runStage,
  stageFloor,
  writePart,
  GATE_SEEDS,
  STAGES,
  HARD_TRUTH_WINS,
  CHAOS_TRUTH_WINS,
} from './gate-core'

// chaos stages 31..35 (part 6 of 7) — runs on its own core via bun --parallel.
const DIFFICULTY: string = 'chaos'
const TRUTH = DIFFICULTY === 'hard' ? HARD_TRUTH_WINS : CHAOS_TRUTH_WINS
const START = 30
const END = 35
const PART_ID = 'chaos-6'

describe(
  'god-ai-hard-chaos-gate [' + DIFFICULTY + ' stages ' + (START + 1) + '-' + END + ']',
  () => {
    const wins: number[] = []
    for (let idx = START; idx < END; idx++) {
      it(
        'S' + (idx + 1) + ' ' + STAGES[idx].name + ' meets floor',
        () => {
          const w = runStage(DIFFICULTY, idx)
          wins[idx - START] = w
          const f = stageFloor(TRUTH)(idx)
          const pct = ((w / GATE_SEEDS.length) * 100).toFixed(0)
          console.log(
            '[gate:' +
              DIFFICULTY +
              '] S' +
              (idx + 1) +
              ' ' +
              STAGES[idx].name +
              ': ' +
              w +
              '/' +
              GATE_SEEDS.length +
              ' (' +
              pct +
              '%) floor=' +
              f +
              (w < f ? '  <-- BELOW FLOOR' : ''),
          )
          expect(w).toBeGreaterThanOrEqual(f)
        },
        900000,
      )
    }
    // Publish partial results for the aggregate reducer (atomic write to ppid temp dir).
    it('records partial wins for the aggregate reducer', () => {
      writePart(PART_ID, { difficulty: DIFFICULTY, wins: wins.slice() })
    }, 900000)
  },
)
