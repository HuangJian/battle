/**
 * NNOption.ts — P′-3 / E2′ 合成 `nn-option` candidate（plan/nn-hierarchical-options.plan.md §9.6）。
 *
 * 角色：把上层注入的 option（意图词表）+ targetKind/target/pathMode 展开成
 * 同一 `thinkImpl → runChain → endFrame` 生命周期里的动作。
 *
 * 两条执行路径：
 *   ① `targetKind === 'none'`：**纯委托** —— option 展开成该意图的候选子链
 *      （`candidateOverrideForIntent` 的同一份白名单、同一 `_orderedCandidates`
 *      顺序、first-commit-wins）。与 E1 `_candidateOverride` 子集逐字节等价
 *      （tests/god/nn-option-parity.test.ts + intent-option-parity.test.ts）。
 *   ② `targetKind !== 'none'`：**目标执行** —— 解析目标格（显式 target 格 →
 *      显式 targetId（逐 tick 跟踪）→ 最近 powerup / 最近敌人），按 pathMode 走：
 *        direct/detour → `navigateTowards`（A* 自然绕砖；detour 与 direct 同径，
 *                        因为默认寻路本来就不穿砖）
 *        break         → `carvePathInfoCached`（允许砖的代价路径）+ `carveFire`
 *      目标不可达/无目标 ⇒ 回退到 ①，不吞 fallback。
 *   ③ `param`（P′-2a2）：standoff 格数 [0,3,6]；仅 enemy/anchor 目标生效，
 *      距离 ≤ standoff 时交回 option 子链。`param=0` 无行为变化（P′-3 parity 不变）。
 *
 * 可达性：`CANDIDATES` 不含本候选（默认链与 M1 parity 逐字节不变）；只有
 * `_candidateOverride` 含 `'nnOption'` 时 shell 才使用扩展池
 * （`_orderedCandidatesE2`）。`evaluate` 在未注入 `_optionCmd` 时恒 false。
 */
import type { GodAIInput } from '../GodAIInput'
import { recordBranch } from '../GodAIInput'
import { CELL } from '../../constants'
import { runChain, type Candidate, type DecisionContext } from './DecisionCore'
import type { Cell } from './pathfind'
import { carvePathInfoCached } from './PathCarve'
import { carveFire } from './candidates/shared'
import type { IntentId } from '../intent/vocab'

/** 目标格（顶点坐标口径，与 pathfind / goal-executor 一致）。 */
export interface NNOptionTarget {
  col: number
  row: number
}

/**
 * 一次 option 注入。缺省值（调用方必须显式给全）：
 *   targetKind='none' · target=null · pathMode='direct' · param=0
 */
export interface NNOptionCommand {
  option: IntentId
  targetKind: 'none' | 'enemy' | 'anchor' | 'powerup'
  target: NNOptionTarget | null
  /** 显式敌 tank id（P′-2a2）：逐 tick 解析其当前格；目标消失 ⇒ 回退，不改就近目标。 */
  targetId?: number | null
  pathMode: 'direct' | 'break' | 'detour'
  param: number
}

/** `param` 的 standoff 格数（P′-2a2）：0 = 贴近目标；1 = 3 格；2 = 6 格。 */
export const PARAM_STANDOFF_CELLS = [0, 3, 6] as const

/** 解析目标格：显式 target 格 → 显式 targetId → 最近活道具 / 最近活敌；anchor 需显式目标。 */
function resolveTarget(self: GodAIInput, cmd: NNOptionCommand, pc: Cell): Cell | null {
  if (cmd.target) return { col: cmd.target.col, row: cmd.target.row }
  if (cmd.targetId != null && cmd.targetKind !== 'powerup') {
    const tanks = self.world.tanks
    for (let i = 0; i < tanks.length; i++) {
      const t = tanks[i]
      if (t.id !== cmd.targetId) continue
      if (!t.alive || t.isPlayer || t.spawnTimer > 0) break
      const c = self.tankCell(t) // shared buffer — copy immediately
      return { col: c.col, row: c.row }
    }
    return null // 显式目标已消失：回退 option 子链，不静默改就近目标
  }
  if (cmd.targetKind === 'powerup') {
    let best: Cell | null = null
    let bestD = Infinity
    const pus = self.world.powerUps
    for (let i = 0; i < pus.length; i++) {
      const pu = pus[i]
      if (!pu.alive) continue
      const col = Math.round((pu.x + pu.w / 2) / CELL)
      const row = Math.round((pu.y + pu.h / 2) / CELL)
      const d = Math.abs(col - pc.col) + Math.abs(row - pc.row)
      if (d < bestD) {
        bestD = d
        best = { col, row }
      }
    }
    return best
  }
  if (cmd.targetKind === 'enemy') {
    let best: Cell | null = null
    let bestD = Infinity
    const enemies = self._enemies
    for (let i = 0; i < enemies.length; i++) {
      const t = enemies[i]
      if (!t.alive || t.spawnTimer > 0) continue
      const c = self.tankCell(t) // shared buffer — copy immediately
      const d = Math.abs(c.col - pc.col) + Math.abs(c.row - pc.row)
      if (d < bestD) {
        bestD = d
        best = { col: c.col, row: c.row }
      }
    }
    return best
  }
  return null // anchor 需要显式 target（由 option 源决定）
}

/**
 * 按 pathMode 执行目标。返回 true = 已提交动作；false = 目标不可达（调用方回退
 * 到 option 子链）。
 */
function executeTarget(
  self: GodAIInput,
  ctx: DecisionContext,
  pc: Cell,
  target: Cell,
  mode: 'direct' | 'detour' | 'break',
): boolean {
  if (mode === 'break') {
    const info = carvePathInfoCached(self, pc, target)
    if (!info.path || info.path.length === 0) return false
    const dir = info.path[0]
    self._moveDir = dir
    carveFire(self, ctx, dir)
    recordBranch(self, 'nnOption')
    return true
  }
  // direct / detour：默认 A* 不穿砖（detour 的语义），无路 ⇒ 回退。
  const dir = self.navigateTowards(target)
  if (!dir) return false
  self._moveDir = dir
  self._fire = !ctx.onCooldown && self.shouldFireInDir(ctx.pcx, ctx.pcy, dir)
  recordBranch(self, 'nnOption')
  return true
}

/**
 * P′-2a2 standoff：距离 ≤ param 档位时把控制交回 option 子链。
 * 仅 enemy/anchor 目标生效；param=0 在进入曼哈顿计算前直接返回 false（旧行为逐字节不变）。
 */
function standoffReached(cmd: NNOptionCommand, pc: Cell, target: Cell): boolean {
  if (cmd.targetKind === 'powerup') return false
  const standoff = PARAM_STANDOFF_CELLS[cmd.param] ?? 0
  if (standoff <= 0) return false
  return Math.abs(target.col - pc.col) + Math.abs(target.row - pc.row) <= standoff
}

/**
 * E2′ `nn-option` candidate。无 World 写入、无 RNG、无逐 tick 分配
 * （option 子链在 reset 时构建）。
 */
export const NN_OPTION: Candidate = {
  id: 'nnOption',
  weight: 0,
  evaluate(self, ctx) {
    const cmd = self._optionCmd
    if (!cmd) return false

    if (cmd.targetKind !== 'none') {
      const pc = self.playerCell()
      const target = resolveTarget(self, cmd, pc)
      if (target && !standoffReached(cmd, pc, target)) {
        // 非法/未知 mode 一律 mask 回 direct（计划 §9.6 的 mask 规则）。
        const mode =
          cmd.pathMode === 'break' ? 'break' : cmd.pathMode === 'detour' ? 'detour' : 'direct'
        if (executeTarget(self, ctx, pc, target, mode)) return true
      }
    }

    const chain = self._optionChains.get(cmd.option)
    if (!chain || chain.length === 0) return false
    return runChain(self, ctx, chain) !== null
  },
}
