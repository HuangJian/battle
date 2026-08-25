#!/usr/bin/env bun
/**
 * gen-thumbnails.ts — Stage thumbnail generator.
 *
 * Renders StageData terrain layouts as SVG thumbnails (256×256).
 * Uses the classic theme colors for visual consistency.
 *
 * SVG is chosen over PNG because Bun has no canvas API and adding a
 * native canvas dependency would violate the "keep the bundle small"
 * principle (MANIFEST §14). SVGs are viewable in any browser and can
 * be batch-converted to PNG if needed.
 *
 * Usage:
 *   bun tools/level/gen-thumbnails.ts --input generated-stages.json --output-dir thumbnails/
 *   bun tools/level/gen-thumbnails.ts --stages 1-5 --output-dir thumbnails/
 *   bun tools/level/gen-thumbnails.ts --generated --count 5 --output-dir thumbnails/
 */

import { STAGES } from '../../src/config/stages'
import { generateStages, type Theme } from './level-gen'
import type { StageData } from '../../src/types'
import { GRID, PLAYER_SPAWN, ENEMY_SPAWNS } from '../../src/constants'

import { arg } from '../lib/cli'
// ============================================================
// Terrain colors (from CLASSIC_THEME)
// ============================================================

const TERRAIN_COLORS: Record<string, string> = {
  '.': '#0d0d0d', // empty (bg)
  b: '#b85c28', // brick
  s: '#b0b0b0', // steel
  w: '#2038d8', // water
  f: '#00a000', // forest
  i: '#80d0ff', // ice
  E: '#e8c840', // base
}

const SPAWN_MARKER_COLOR = '#ff4040'
const PLAYER_MARKER_COLOR = '#40a0ff'

// ============================================================
// SVG rendering
// ============================================================

/**
 * Render a single stage as an SVG string (256×256, viewBox 0 0 26 26).
 *
 * Each sub-block is a 1×1 rect. Spawn points and the player position
 * are marked with semi-transparent circles for visual reference.
 */
export function renderStageSVG(stage: StageData): string {
  const parts: string[] = []

  // SVG header
  parts.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 ${GRID} ${GRID}" shape-rendering="crispEdges">`,
  )

  // Background
  parts.push(`<rect width="${GRID}" height="${GRID}" fill="${TERRAIN_COLORS['.']}"/>`)

  // Terrain cells
  for (let r = 0; r < GRID; r++) {
    const line = stage.tiles[r] || ''
    for (let c = 0; c < GRID; c++) {
      const ch = line[c] || '.'
      if (ch === '.') continue
      const color = TERRAIN_COLORS[ch] ?? TERRAIN_COLORS['.']
      parts.push(`<rect x="${c}" y="${r}" width="1" height="1" fill="${color}"/>`)
    }
  }

  // Enemy spawn markers (semi-transparent red circles)
  for (const spawn of ENEMY_SPAWNS) {
    parts.push(
      `<circle cx="${spawn.col + 1}" cy="${spawn.row + 1}" r="0.8" fill="${SPAWN_MARKER_COLOR}" opacity="0.4"/>`,
    )
  }

  // Player spawn marker (semi-transparent blue circle)
  parts.push(
    `<circle cx="${PLAYER_SPAWN.col + 1}" cy="${PLAYER_SPAWN.row + 1}" r="0.8" fill="${PLAYER_MARKER_COLOR}" opacity="0.4"/>`,
  )

  // Stage name label
  const escapedName = stage.name.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  parts.push(
    `<text x="1" y="${GRID - 1}" font-size="2" fill="#e0e0e0" font-family="monospace" opacity="0.7">${escapedName}</text>`,
  )

  parts.push('</svg>')
  return parts.join('')
}

// ============================================================
// Batch rendering
// ============================================================

export interface ThumbnailOptions {
  stages: StageData[]
  outputDir: string
}

/**
 * Render multiple stages as SVG files.
 * Returns the list of file paths written.
 */
export async function renderThumbnails(opts: ThumbnailOptions): Promise<string[]> {
  // Ensure output directory exists
  const dir = opts.outputDir
  await Bun.write(`${dir}/.gitkeep`, '')

  const paths: string[] = []
  for (const stage of opts.stages) {
    const svg = renderStageSVG(stage)
    const filename = `${dir}/stage-${stage.id}.svg`
    await Bun.write(filename, svg)
    paths.push(filename)
  }
  return paths
}

// ============================================================
// CLI
// ============================================================

if (import.meta.main) {
  const inputFile = arg('input')
  const outputDir = arg('output-dir', 'thumbnails')!
  const stageSpec = arg('stages')
  const useGenerated = process.argv.includes('--generated')
  const genCount = parseInt(arg('count', '5')!, 10)
  const genTheme = (arg('theme', 'mixed') ?? 'mixed') as Theme
  const difficulty = arg('difficulty', 'hard')!

  let stages: StageData[]

  if (inputFile) {
    const raw = await Bun.file(inputFile).text()
    stages = JSON.parse(raw) as StageData[]
    process.stderr.write(`[gen-thumbnails] Loaded ${stages.length} stages from ${inputFile}\n`)
  } else if (useGenerated) {
    stages = generateStages(genCount, difficulty, genTheme, 1)
    process.stderr.write(`[gen-thumbnails] Generated ${stages.length} stages\n`)
  } else if (stageSpec) {
    if (stageSpec === 'all') {
      stages = STAGES
    } else if (stageSpec.includes('-')) {
      const [start, end] = stageSpec.split('-').map(Number)
      stages = STAGES.slice(start - 1, end) // CLI is 1-based (1..35); internal index is 0-based
    } else {
      const idx = parseInt(stageSpec, 10) - 1
      stages = [STAGES[idx]]
    }
    if (stages.length === 0 || !stages[0]) {
      console.error(`--stages: no valid stage indexes (1..${STAGES.length})`)
      process.exit(1)
    }
    process.stderr.write(`[gen-thumbnails] Using ${stages.length} classic stages\n`)
  } else {
    stages = STAGES.slice(0, 5)
    process.stderr.write(`[gen-thumbnails] Using first 5 classic stages (default)\n`)
  }

  const paths = await renderThumbnails({ stages, outputDir })

  process.stderr.write(`[gen-thumbnails] Wrote ${paths.length} SVG files to ${outputDir}/\n`)
  console.log(JSON.stringify({ outputDir, count: paths.length, files: paths }, null, 2))
}
