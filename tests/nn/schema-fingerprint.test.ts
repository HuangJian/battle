/**
 * schema-fingerprint.test.ts — obs v5 SCHEMA_FINGERPRINT 双端锚（hy X4）。
 *
 * obs spec §3.4-7 / §4：schema.py 与 obs-encoder.ts 导出同名常量，任一端常量
 * 变动（通道数/标量布局/打包值域/镜像索引/弹速桶）指纹必变。本测试把 TS 侧钉在
 * 与 Python 侧**同一字面量**上；`nn-training/tests/common/test_schema_fingerprint.py`
 * 把 Python 侧钉在同一值——任一端漏同步即红，而不是等 golden 前向对不上才发现。
 *
 * v5（plan/obs-v5-vector-gap.plan.md）：MAJOR 4→5、SCALAR_DIM 30→40、extra 9→37、
 * 指纹末段由 `dim|names|swaps` 三字段扩为 `dim|names|swaps|neg` 四字段。
 */
import { describe, it, expect } from 'bun:test'
import {
  SCHEMA_FINGERPRINT,
  OBS_SCHEMA_MAJOR,
  OBS_CHANNELS,
  SCALAR_DIM,
  SCALAR_X_INDICES,
  SCALAR_NAMES,
} from '../../src/nn/obs-encoder'
import {
  POLICY_EXTRA_DIM,
  POLICY_EXTRA_MIRROR_NEG,
  POLICY_EXTRA_MIRROR_SWAPS,
  POLICY_EXTRA_NAMES,
} from '../../src/nn/policy-extra'

// 必须与 nn-training/common/schema.py::SCHEMA_FINGERPRINT 逐字相同（两边单测共锚）。
const FINGERPRINT = 'dabef716'

// 必须与 nn-training/common/schema.py::SCALAR_LAYOUT 的第二元逐字同序（双端共锚）。
// 只钉维度挡不住「交换两个标量含义」——语义序列进指纹后这种漏同步才现形。
const SCALAR_LAYOUT_NAMES: (typeof SCALAR_NAMES)[number][] = [
  'slack',
  'baseDeadline',
  'lives',
  'level',
  'fireProgress',
  'turnCooldownRemaining',
  'ringCompleteness',
  'enemiesOnField',
  'spawnQueueRemaining',
  'tier_none',
  'tier_rookie',
  'tier_soldier',
  'tier_veteran',
  'tier_commander',
  'nearestEnemyDist',
  'nearestEnemyRelX',
  'nearestEnemyRelY',
  'nearestBaseDist',
  'nearestBaseRelX',
  'playerHp',
  'playerShield',
  'freeze',
  'stuck',
  'boat',
  'baseHp',
  'fence',
  'score',
  'emp',
  'iceVy',
  'iceVx',
  'moving',
  'myBullets',
  'pickupDist',
  'pickupRelX',
  'pickupRelY',
  'wallDist',
  'cornerDist',
  'baseRelY',
  'basePressure',
  'nearestEnemyApproach',
  'prevActionHold',
  'prevActionFireNone',
  'prevActionFireBeforeTurn',
  'prevActionFireAfterTurn',
  'ticksSinceHit',
  'nearestEnemyBeyond',
]

// 必须与 nn-training/common/schema.py::POLICY_EXTRA_LAYOUT 的第二元逐字同序。
const POLICY_EXTRA_LAYOUT_NAMES: (typeof POLICY_EXTRA_NAMES)[number][] = [
  'threatFront',
  'threatBack',
  'threatLeft',
  'threatRight',
  'hitDistFront',
  'hitDistBack',
  'hitDistLeft',
  'hitDistRight',
  'pincer',
  'corrF',
  'corrB',
  'corrL',
  'corrR',
  'ttiF',
  'ttiB',
  'ttiL',
  'ttiR',
  'laneEF',
  'laneEB',
  'laneEL',
  'laneER',
  'blkF',
  'blkB',
  'blkL',
  'blkR',
  'baseThreatU',
  'baseThreatL',
  'baseThreatR',
  'baseRingU',
  'baseRingL',
  'baseRingR',
  'baseThreatTTI',
  'baseThreatRelX',
  'baseThreatRelY',
  'baseEnemyDistU',
  'baseEnemyDistL',
  'baseEnemyDistR',
  'threatTankFront',
  'threatTankBack',
  'threatTankLeft',
  'threatTankRight',
  'prevActionUp',
  'prevActionDown',
  'prevActionLeft',
  'prevActionRight',
  'lastHitUp',
  'lastHitDown',
  'lastHitLeft',
  'lastHitRight',
  'enemyFacingUp',
  'enemyFacingDown',
  'enemyFacingLeft',
  'enemyFacingRight',
  'leadHitFront',
  'leadHitBack',
  'leadHitLeft',
  'leadHitRight',
]

describe('SCHEMA_FINGERPRINT (obs v5 双端锚)', () => {
  it('TS 指纹 == Python schema.SCHEMA_FINGERPRINT（共享字面锚）', () => {
    expect(SCHEMA_FINGERPRINT).toBe(FINGERPRINT)
  })

  it('配套常量与指纹同版（v6：16ch / 46sc / X=[15,18,29,33] / extra 57）', () => {
    expect(OBS_SCHEMA_MAJOR).toBe(6)
    expect(OBS_CHANNELS).toBe(16)
    expect(SCALAR_DIM).toBe(46)
    expect(SCALAR_X_INDICES).toEqual([15, 18, 29, 33])
    expect(POLICY_EXTRA_DIM).toBe(57)
  })

  it('标量语义序列与 schema.py SCALAR_LAYOUT 逐字同序（指纹已含）', () => {
    expect(SCALAR_NAMES.length).toBe(SCALAR_DIM)
    expect([...SCALAR_NAMES]).toEqual(SCALAR_LAYOUT_NAMES)
  })

  it('POLICY_EXTRA 语义序列与 schema.py 逐字同表（指纹已含）', () => {
    expect(POLICY_EXTRA_NAMES.length).toBe(POLICY_EXTRA_DIM)
    expect([...POLICY_EXTRA_NAMES]).toEqual(POLICY_EXTRA_LAYOUT_NAMES)
  })

  it('mirror 表：9 对互换 + 取负 [32]（swap 与 neg 分家，指纹末段四字段）', () => {
    expect([...POLICY_EXTRA_MIRROR_SWAPS].map((p) => [...p])).toEqual([
      [2, 3],
      [6, 7],
      [11, 12],
      [15, 16],
      [19, 20],
      [23, 24],
      [26, 27],
      [29, 30],
      [35, 36],
      [39, 40],
      [43, 44],
      [47, 48],
      [51, 52],
      [55, 56],
    ])
    expect([...POLICY_EXTRA_MIRROR_NEG]).toEqual([32])
  })
})
