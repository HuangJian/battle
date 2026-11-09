/** behavior-metrics.ts — 逐局指标注册表 + 统计量 + 走势图配置（纯函数，客户端安全）。
 *
 *  指标 = per-game 账本里的全部指标列：rollout 侧来自 `it<N>/per-game.json` /
 *  单局 `manifest.json` 的 `metricValues`，eval 侧来自 `eval_log.jsonl` 的
 *  `event:"eval"` 逐局行。服务端按 (iter[, wver]) 聚合六种统计量；这里只定义
 *  **展示口径**，不碰 IO。新增一个指标 = 在 `METRIC_DEFS` 加一行——趋势图、
 *  指标表、显示配置都从同一份表里长出来。
 */

// ────────────────────────── 统计量 ──────────────────────────

export type MetricStatKey = 'mean' | 'median' | 'min' | 'max' | 'p90' | 'p99'

export interface MetricStats {
  mean: number
  median: number
  min: number
  max: number
  p90: number
  p99: number
}

export const METRIC_STAT_KEYS: readonly MetricStatKey[] = [
  'mean',
  'median',
  'min',
  'max',
  'p90',
  'p99',
] as const

export const METRIC_STAT_LABEL: Record<MetricStatKey, string> = {
  mean: '均值',
  median: '中位数',
  min: '最小',
  max: '最大',
  p90: 'p90',
  p99: 'p99',
}

export const METRIC_STAT_OPTIONS: Array<{ value: MetricStatKey; label: string }> = [
  { value: 'mean', label: '均值' },
  { value: 'median', label: '中位数' },
  { value: 'min', label: '最小' },
  { value: 'max', label: '最大' },
  { value: 'p90', label: 'p90' },
  { value: 'p99', label: 'p99' },
]

export function isMetricStat(v: unknown): v is MetricStatKey {
  return (
    typeof v === 'string' &&
    (v === 'mean' || v === 'median' || v === 'min' || v === 'max' || v === 'p90' || v === 'p99')
  )
}

export function parseMetricStat(raw: string | null | undefined): MetricStatKey {
  return isMetricStat(raw) ? raw : 'mean'
}

/** 线性插值分位（与 numpy 默认 percentile 同口径；n=1 时恒该值）。 */
function percentile(sorted: number[], p: number): number {
  const n = sorted.length
  if (n === 0) return Number.NaN
  if (n === 1) return sorted[0]
  const pos = (n - 1) * p
  const lo = Math.floor(pos)
  const hi = Math.ceil(pos)
  if (lo === hi) return sorted[lo]
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo)
}

/** 逐局值 → 六种统计量；非有限值跳过，空数组 = null（缺读数，不编 0）。 */
export function computeMetricStats(values: Array<number | null | undefined>): MetricStats | null {
  const xs: number[] = []
  for (const v of values) {
    if (typeof v === 'number' && Number.isFinite(v)) xs.push(v)
  }
  if (xs.length === 0) return null
  xs.sort((a, b) => a - b)
  let sum = 0
  for (const v of xs) sum += v
  return {
    mean: sum / xs.length,
    median: percentile(xs, 0.5),
    min: xs[0],
    max: xs[xs.length - 1],
    p90: percentile(xs, 0.9),
    p99: percentile(xs, 0.99),
  }
}

// ────────────────────────── 指标定义 ──────────────────────────

/** 展示格式：整数 / 一位小数 / 0–1 百分比。 */
export type BehaviorMetricFmt = 'int' | 'dec1' | 'pct'

/** 逐局指标分组（配置弹窗按它分节；顺序即上屏顺序）。 */
export type BehaviorMetricGroupKey =
  'result' | 'combat' | 'damage' | 'movement' | 'survival' | 'loot' | 'base'

export interface BehaviorMetricGroupDef {
  key: BehaviorMetricGroupKey
  label: string
  /** 这一组在配置弹窗里的一句话说明（大白话）。 */
  desc: string
}

export const BEHAVIOR_METRIC_GROUPS: readonly BehaviorMetricGroupDef[] = [
  { key: 'result', label: '对局结果', desc: '整局的骨架：打了多久、死几次、什么时候开张和清场。' },
  {
    key: 'combat',
    label: '火力与命中',
    desc: '开火、命中、击杀的产量和质量：先看能不能打中，再看打不打得死。',
  },
  { key: 'damage', label: '承伤与血线', desc: '挨了多少、什么时候挨、挨得急不急：看稳定性。' },
  {
    key: 'movement',
    label: '走位与节奏',
    desc: '动还是停、有没有顶墙卡住、是不是长期站在敌人火线上。',
  },
  {
    key: 'survival',
    label: '距离与包围',
    desc: '敌人离你多近、被几个方向夹住、在危险区待了多久。',
  },
  { key: 'loot', label: '道具', desc: '道具掉了多少、捡了多少、离最近的道具多远。' },
  { key: 'base', label: '基地威胁', desc: '基地墙是否完好、敌人有没有打开射界、压力有多大。' },
] as const

/** [key, 展示名, 格式, hover 口径, 分组, 大白话释义]；key 与 `biz.reward_library.METRICS` /
 *  `tools/sim/export-rl-rollout.ts::METRICS_COLUMN_NAMES` 逐字一致。
 *  末尾两个是 eval 专属读数（rollout 侧没有对应列，缺值显示 —）。
 *  `hint` 是「走势配置」弹窗底部解释栏的正文：说清是什么、拿它看什么，不写内部黑话。 */
const METRIC_DEFS = [
  [
    'ticks',
    '总时长',
    'int',
    '本局总 tick 数',
    'result',
    '这一局总共打了多少拍（1 秒约 60 拍）。太长通常是拖着不打或来回磨；要和胜负一起看，单看时长没有好坏。',
  ],
  [
    'kills',
    '击杀数',
    'int',
    '本局击杀敌车数',
    'combat',
    '这一局打死了多少敌人。最直白的产出；要配开火次数和命中率，才知道是真准还是靠火力堆。',
  ],
  [
    'lives',
    '命数',
    'int',
    '终局剩余命数',
    'result',
    '结束时还剩几条命。剩得越多说明整局余量越大；比单看输赢更能看出稳不稳。',
  ],
  [
    'playerHits',
    '受击',
    'int',
    '玩家被命中次数',
    'damage',
    '玩家被敌人子弹打中的次数。最直接的安全读数；和承伤一起看，才知道挨得重不重。',
  ],
  [
    'playerDamageTaken',
    '承伤',
    'int',
    '玩家累计承伤',
    'damage',
    '这一局总共掉了多少血。挨得多不一定是坏事（可能在主动换血），要和胜负一起判断。',
  ],
  [
    'playerShots',
    '开火',
    'int',
    '玩家累计开火次数',
    'combat',
    '一共开了多少枪。高不一定好：命中率和击杀有没有跟着涨，才是关键。',
  ],
  [
    'enemyHits',
    '命中',
    'int',
    '玩家命中敌车次数',
    'combat',
    '子弹打中敌人的次数。比击杀更早反映准头，因为有些命中还没打死。',
  ],
  [
    'powerUpsCollected',
    '拾取',
    'int',
    '道具拾取总数',
    'loot',
    '这一局捡到的道具总数。和掉落数一起看，才知道是没刷还是没去捡。',
  ],
  [
    'powerUpsSpawned',
    '掉落',
    'int',
    '道具掉落总数',
    'loot',
    '这一局掉落的道具总数。它是供给量，不是主动性的读数。',
  ],
  // ⚠ 语义见 src/game/stuck-detect.ts:55：换格或命中即归零，是「局末当前连续段」，不是累计。
  [
    'stuckTicks',
    '卡滞',
    'int',
    '局末当前连续卡滞段 tick 数（换格或命中即归零，非累计）',
    'movement',
    '打到结尾时正卡在原地的连续时长（不是整局累计；一动或一命中就归零）。看收尾有没有卡死。',
  ],
  [
    'starsCollected',
    '星星',
    'int',
    '星星拾取数',
    'loot',
    '捡到的星星数量。星星直接升级，通常是优先级最高的道具。',
  ],
  [
    'baseAlive',
    '基地存活',
    'pct',
    '终局基地是否存活（1/0）',
    'base',
    '终局基地还在不在（1 = 在，0 = 没了）。这是底线读数，掉了基本就结束了。',
  ],
  [
    'baseWallTotal',
    '基地墙总数',
    'int',
    '基地环墙总格数',
    'base',
    '基地周围一圈保护墙的总格数。只是分母，会随关卡地形变化。',
  ],
  [
    'baseWallIntact',
    '基地墙完好',
    'int',
    '终局基地环墙完好格数',
    'base',
    '终局还剩多少格基地保护墙。越多说明防守越完整。',
  ],
  [
    'basePressureSum',
    '基地压力累计',
    'int',
    '基地压力逐采样累计值',
    'base',
    '敌人靠近基地产生的压迫感，按采样累加。越高说明基地越常被威胁。',
  ],
  [
    'basePressureSamples',
    '基地压力采样',
    'int',
    '基地压力采样次数',
    'base',
    '基地压迫感的采样次数。单独看没有意义，用来给「基地压力累计」做平均。',
  ],
  [
    'firstKillTick',
    '首杀 tick',
    'int',
    '首次击杀的 tick；-1 = 本局无击杀',
    'result',
    '第一次击杀发生在第几拍（-1 = 整局没开张）。看开局进入状态快不快。',
  ],
  [
    'playerDeaths',
    '阵亡',
    'int',
    '玩家阵亡次数',
    'result',
    '这一局死了几次。越低越稳；如果频繁死，先去看承伤和站位。',
  ],
  [
    'cellsVisited',
    '到访格',
    'int',
    '玩家到访过的格子数',
    'movement',
    '整局走过多少不同的格子。太少说明老在原地打转或不敢动。',
  ],
  [
    'playerLevel',
    '等级',
    'int',
    '终局玩家等级',
    'result',
    '结束时的等级（吃星星升级）。等级越高，通常说明资源吃得更足、后面更好打。',
  ],
  [
    'enemyTotal',
    '敌总数',
    'int',
    '本关敌人总数',
    'result',
    '本关一共有多少敌人。它只是分母：这张图变了不说明变强，只说明打的关不一样。',
  ],
  [
    'puSpawnBomb',
    '炸弹掉落',
    'int',
    '炸弹道具掉落数',
    'loot',
    '炸弹道具掉了几个。看这一关给不给清场资源。',
  ],
  [
    'puSpawnTank',
    '坦克掉落',
    'int',
    '加命道具掉落数',
    'loot',
    '加命道具掉了几个。加命是稀缺资源，掉落多时更值得冒险去拿。',
  ],
  [
    'puSpawnFreeze',
    '冻结掉落',
    'int',
    '冻结道具掉落数',
    'loot',
    '冻结道具掉了几个。冻结能换出一段安全的输出窗口。',
  ],
  [
    'puSpawnShield',
    '护盾掉落',
    'int',
    '护盾道具掉落数',
    'loot',
    '护盾道具掉了几个。护盾能顶一次致命伤，价值很高。',
  ],
  [
    'puGotBomb',
    '炸弹拾取',
    'int',
    '炸弹道具拾取数',
    'loot',
    '捡到炸弹的次数。和「炸弹掉落」一起看，才知道捡没捡。',
  ],
  [
    'puGotTank',
    '加命拾取',
    'int',
    '加命道具拾取数',
    'loot',
    '捡到加命道具的次数。和掉落对比，看有没有漏掉这种稀缺资源。',
  ],
  [
    'puGotFreeze',
    '冻结拾取',
    'int',
    '冻结道具拾取数',
    'loot',
    '捡到冻结道具的次数。看会不会主动拿防守型资源。',
  ],
  [
    'puGotShield',
    '护盾拾取',
    'int',
    '护盾道具拾取数',
    'loot',
    '捡到护盾道具的次数。看会不会主动拿保命资源。',
  ],
  [
    'puSpawnStar',
    '星星掉落',
    'int',
    '星星道具掉落数',
    'loot',
    '星星道具掉了几个。星星直接升级，掉落多时优先级最高。',
  ],
  [
    'clearTick',
    '清场 tick',
    'int',
    '敌人首次全灭的 tick；-1 = 未清场',
    'result',
    '敌人第一次全部清空的时刻（-1 = 没清场）。看从开局到收尾推进得顺不顺。',
  ],
  [
    'killsBasic',
    '击杀·基础',
    'int',
    '击杀基础敌车数',
    'combat',
    '打死基础坦克的数量。用来看输出在不同敌人类型上的分布。',
  ],
  [
    'killsFast',
    '击杀·快速',
    'int',
    '击杀快速敌车数',
    'combat',
    '打死快速坦克的数量。快速目标更难打中，这个数能看出跟枪能力。',
  ],
  [
    'killsPower',
    '击杀·火力',
    'int',
    '击杀火力敌车数',
    'combat',
    '打死火力坦克的数量。火力车威胁大，清掉它能直接减轻正面压力。',
  ],
  [
    'killsArmor',
    '击杀·装甲',
    'int',
    '击杀装甲敌车数',
    'combat',
    '打死装甲坦克的数量。装甲要挨多发，能看出持续输出和耐心。',
  ],
  [
    'hitsBasic',
    '命中·基础',
    'int',
    '命中基础敌车数',
    'combat',
    '打中基础坦克的次数。基础车是主要分母，先看准头稳不稳。',
  ],
  [
    'hitsFast',
    '命中·快速',
    'int',
    '命中快速敌车数',
    'combat',
    '打中快速坦克的次数。快速车爱乱跑，这个数更能反映跟枪和预判。',
  ],
  [
    'hitsPower',
    '命中·火力',
    'int',
    '命中火力敌车数',
    'combat',
    '打中火力坦克的次数。火力车露头时威胁最大，能打中就有机会先手。',
  ],
  [
    'hitsArmor',
    '命中·装甲',
    'int',
    '命中装甲敌车数',
    'combat',
    '打中装甲坦克的次数。装甲要连续命中才死，这个数看持续输出。',
  ],
  [
    'puGotOther',
    '其它拾取',
    'int',
    '四桶之外的其它道具拾取数',
    'loot',
    '四类常用道具之外（围栏/船/修理等）的拾取数。用于对账，避免拾取总数对不上。',
  ],
  [
    'pickupDist',
    '拾取距离',
    'int',
    '到最近可达拾取中心的路径距离；-1 = 无',
    'loot',
    '离最近一个能走到的道具还有多远（-1 = 场上没有）。越小说明越主动去拿。',
  ],
  [
    'playerHpRatio',
    '玩家血量',
    'pct',
    '终局玩家血量比例（0–1）',
    'damage',
    '终局还剩多少血（0–1）。比单看命数更细，能看出赢下来时有多虚。',
  ],
  [
    'dangerTicks',
    '危险时长',
    'int',
    'hpRatio < 0.4 的累计 tick 数（越低越稳）',
    'damage',
    '血量低于 40% 的时长。越短越稳；长时间低血说明一直在刀尖上走。',
  ],
  [
    'threatTicks',
    '威胁时长',
    'int',
    '处于威胁射界内的累计 tick 数',
    'survival',
    '暴露在敌人弹道或炮口线上的时长（冻结/护盾期间不算）。越短越安全。',
  ],
  [
    'dmgFirst600',
    '前600承伤',
    'int',
    '开局前 600 tick 的承伤总量',
    'damage',
    '开局前 600 拍（约 10 秒）挨的伤害。看开局稳不稳，避免一上来就被打崩。',
  ],
  [
    'backHits',
    '背击',
    'int',
    '背刺命中次数',
    'combat',
    '从背后打中敌人的次数。背击越多，说明绕后和抓转身做得越好。',
  ],
  [
    'sideHits',
    '侧击',
    'int',
    '侧击命中次数',
    'combat',
    '从侧面打中敌人的次数。侧击是不把正面暴露给敌人的折中打法。',
  ],
  [
    'frontHitsExempt',
    '正面豁免',
    'int',
    '正面命中但处于冻/盾豁免的次数',
    'combat',
    '正面命中、但当时敌人处于冻结或护盾豁免的次数。单独拿出来，避免把白打算成正面硬刚。',
  ],
  [
    'farHits',
    '流弹命中',
    'int',
    '开火时物理打不到、目标自己走进弹道的命中数',
    'combat',
    '开火时其实打不到、目标自己走进弹道的命中。只作观察，不代表瞄准能力。',
  ],
  [
    'geoFallback',
    '几何回退',
    'int',
    '开火记录反查失败的命中数',
    'combat',
    '反查不到开火记录的命中（例如交棒时已经在飞的弹）。这是读数质量，不是打法。',
  ],
  [
    'onLaneTicks',
    '在线时长',
    'int',
    '在敌坦射界线上的累计 tick 数',
    'survival',
    '待在敌人射界线上、可能被一枪穿到的时长。看走位有没有长期暴露。',
  ],
  [
    'onLaneExemptTicks',
    '在线豁免',
    'int',
    '在线但处于冻结/护盾豁免的累计 tick 数',
    'survival',
    '在射界线上但处于冻结/护盾豁免的时长。单独拿出来，避免把无敌时间也算成冒险。',
  ],
  [
    'onLaneMoveTicks',
    '在线移动',
    'int',
    '在线上且中心格发生变化的累计 tick 数',
    'movement',
    '在敌人射界线上还在移动的时长。移动更难被瞄准，是正面的走位信号。',
  ],
  [
    'onLaneHoldFireTicks',
    '在线憋火',
    'int',
    '在线且静止/未移动但开火的累计 tick 数',
    'movement',
    '在敌人射界线上停住开火的时长。高可能是站桩对枪，值不值要配胜负和承伤看。',
  ],
  [
    'stopTicks',
    '停止时长',
    'int',
    '无移动输出的累计 tick 数',
    'movement',
    '策略主动选择停下的累计时长。防守架枪会变高，不一定是坏事。',
  ],
  [
    'fireHeldTicks',
    '按住开火',
    'int',
    '开火输出为真的累计 tick 数',
    'movement',
    '开火键处于按下状态的时长。看压制是否持续，不等于实际打出的子弹数。',
  ],
  [
    'idleTicks',
    '静止时长',
    'int',
    'player.moving=false 的累计 tick 数',
    'movement',
    '玩家实际没动的时长（想动但被地形挡住也算）。高说明走不动或被卡住。',
  ],
  [
    'enemyDist',
    '敌距',
    'dec1',
    '最近已激活敌车中心距（px）；-1 = 无',
    'survival',
    '离最近一个已激活敌人的距离（像素；-1 = 场上没有）。看贴身程度。',
  ],
  [
    'nearEnemy4Ticks',
    '近敌时长',
    'int',
    '最近敌距 ≤4 格的累计 tick 数',
    'survival',
    '最近敌人距离在 4 格以内的时长。贴得越久，被夹击的风险越高。',
  ],
  [
    'damageBursts',
    '伤害簇',
    'int',
    '相邻两笔扣血间隔 ≤120t 的次数',
    'damage',
    '两次挨打间隔不到 2 秒的次数。越高说明挨打越密集、越难缓过来。',
  ],
  [
    'maxDamage120',
    '120t最大承伤',
    'int',
    '任意 120t 滑窗内最大累计承伤',
    'damage',
    '任意 2 秒窗口里挨的最大伤害。看最凶的一波爆发有多疼。',
  ],
  [
    'damageWhileLow',
    '低血承伤',
    'int',
    '低血期（hpRatio<0.4）承受的伤害累计',
    'damage',
    '血量低于 40% 时挨的伤害。低血还继续掉，通常说明撤退或躲闪没做好。',
  ],
  [
    'encl1Ticks',
    '单轴包围',
    'int',
    '同拍同行/同列敌车数 = 1 的 tick 数',
    'survival',
    '同一行或同一列只有 1 个敌人的时长。轻度包围，通常还能绕。',
  ],
  [
    'encl2Ticks',
    '双轴包围',
    'int',
    '同拍同行/同列敌车数 = 2 的 tick 数',
    'survival',
    '同一行或同一列有 2 个敌人的时长。两个方向被夹，走位开始受限。',
  ],
  [
    'encl3pTicks',
    '三轴包围',
    'int',
    '同拍同行/同列敌车数 ≥3 的 tick 数',
    'survival',
    '同一行或同一列有 3 个以上敌人的时长。重度包围，很容易被交叉火力打穿。',
  ],
  [
    'enclMax',
    '包围峰值',
    'int',
    '本局同拍包围敌数峰值',
    'survival',
    '这一局同时被几个方向的敌人夹住的最大值。峰值比平均更能说明最险的时刻。',
  ],
  [
    'aimHits',
    '瞄准命中',
    'int',
    '玩家弹命中数（含致死）',
    'combat',
    '玩家子弹命中敌人的总数（含致死）。和开火数一起算，就是命中率。',
  ],
  [
    'aimHitDistSum',
    '命中距离和',
    'dec1',
    '命中拍切比雪夫格距之和',
    'combat',
    '每次命中时的距离加起来。同样命中数下，距离更远通常说明预判更好；要配命中数看。',
  ],
  [
    'aimBricks',
    '拆砖',
    'int',
    '真拆砖/破钢次数',
    'combat',
    '主动打砖或破钢的次数。拆地形是开路手段，太多也可能是打不到车在浪费火力。',
  ],
  [
    'aimIgnited',
    '弹弹对消',
    'int',
    '玩家弹与敌弹对消次数',
    'combat',
    '玩家子弹和敌人子弹对消的次数。防守型操作，能减轻正面压力。',
  ],
  [
    'aimMisses',
    '射失',
    'int',
    '未命中（含打钢/出界/局末在飞）次数',
    'combat',
    '没打中任何东西的子弹数（打钢没破、出界、局末还在飞）。越低越好，但要和打法一起看。',
  ],
  [
    'hurtWeight',
    '火线承伤',
    'int',
    '敌弹火线承伤加权累计',
    'damage',
    '按敌人子弹离你多近加权的挨打总量。越近的威胁权重越高，比单纯数伤害更敏感。',
  ],
  [
    'enclWeightTicks',
    '包围加权',
    'int',
    '包围敌数加权 tick 累计（拌入豁免）',
    'survival',
    '按敌人离你多近加权的被包围时长。越近、围得越多，涨得越快。',
  ],
  [
    'cornerWeightTicks',
    '角落加权',
    'int',
    '四角距离加权 tick 累计（拌入豁免）',
    'survival',
    '在角落里被逼近的加权时长（离四角越近权重越高）。角落容易被堵死。',
  ],
  [
    'nearSqSum',
    '近距平方和',
    'int',
    '近敌距离核 (3−d)² 的逐拍累计',
    'survival',
    '所有敌人靠近程度的逐拍累计（2 格内才算，越近涨得越快）。看整体压迫感。',
  ],
  [
    'postHitLaneTicks',
    '受击后在线',
    'int',
    '被击中后仍留在该源火线轴上的 tick 数',
    'survival',
    '被命中后还留在同一条火线上的时长。挨打后没换位，容易被连续打。',
  ],
  [
    'freezeShieldWindowTicks',
    '冻/盾窗口',
    'int',
    '冻结 ∪ 道具盾的累计 tick 数',
    'survival',
    '冻结或道具护盾生效的时长。看防守窗口拿到了多少。',
  ],
  [
    'hitsInFreezeShieldWindow',
    '窗口内命中',
    'int',
    '冻/盾窗口内的玩家命中数',
    'combat',
    '自己处于冻结或护盾窗口时打中敌人的次数。看有没有抓住无敌时间输出。',
  ],
  [
    'deathDerivedShieldTicks',
    '死亡派生盾',
    'int',
    '复活盾/星盾 grace 的累计 tick 数',
    'survival',
    '复活盾、星盾等死亡派生护盾的时长。只作观察，通常不算主动防守能力。',
  ],
  [
    'thBreach',
    '破墙射界',
    'int',
    '基地破墙射界打开、炮口未对准的累计 tick 数',
    'base',
    '敌人透过被打穿的墙已经能打到基地的时长（炮口还没对准）。看防线有没有被打开。',
  ],
  [
    'thBreachAimed',
    '破墙对准',
    'int',
    '基地破墙射界打开且炮口已对准的累计 tick 数',
    'base',
    '破墙射界已经打开、而且炮口对准基地的时长。比上一项更危险，是快丢基地的信号。',
  ],
  [
    'thDirect',
    '直射基地',
    'int',
    '直接射界覆盖基地的累计 tick 数',
    'base',
    '敌人不用破墙、直接就能打到基地的时长。越低越安全。',
  ],
  [
    'thDirectAimed',
    '直射对准',
    'int',
    '直射射界且炮口对准鹰所在轴的累计 tick 数',
    'base',
    '直射射界存在、且炮口已经对准基地的时长。最危险的基地威胁读数。',
  ],
  [
    'thShotsAtBaseCum',
    '朝基地开火',
    'int',
    '敌弹朝鹰/环墙发射的累计次数',
    'base',
    '敌人朝基地或保护墙开火的累计次数。看对方有没有在集火基地。',
  ],
  [
    'thBulletsAtBase',
    '在飞弹',
    'int',
    '当前在飞、目标为鹰/环墙的敌弹数',
    'base',
    '当前正在飞、目标是基地的敌弹数量。这是瞬时值，图上看波动。',
  ],
  [
    'thCostBreach',
    '破墙成本',
    'int',
    '破墙射界的累计成本流',
    'base',
    '破墙射界打开时按持续时长累计的威胁成本。越久越危险（已封顶）。',
  ],
  [
    'thCostBreachAimed',
    '破墙对准成本',
    'int',
    '破墙且对准的累计成本流',
    'base',
    '破墙且炮口已对准基地的累计威胁成本。比上一项更该压下去。',
  ],
  [
    'thCostDirect',
    '直射成本',
    'int',
    '直射基地的累计成本流',
    'base',
    '直射基地射界的累计威胁成本。数值越高，基地越常被直接瞄准。',
  ],
  [
    'thCostDirectAimed',
    '直射对准成本',
    'int',
    '直射且对准的累计成本流',
    'base',
    '直射且已对准基地的累计威胁成本。最高危的一档。',
  ],
  [
    'wallPressTicks',
    '贴墙时长',
    'int',
    '贴墙推进的累计 tick 数',
    'movement',
    '有前进意图但被墙顶住、一步没挪的时长。越高说明越容易顶墙空转。',
  ],
  [
    'aliveTicks',
    '存活时长',
    'int',
    '玩家存活累计 tick 数（eval 专属）',
    'survival',
    '玩家活着的累计时长（仅 eval 有读数）。用来把其它读数换算成每分钟或每千拍的速率。',
  ],
  [
    'postThawDamageRate',
    '解冻承伤',
    'pct',
    '解冻后 60 拍内的承伤占比（eval 专属）',
    'damage',
    '从冻结里解冻后 60 拍内挨的伤害占比（仅 eval）。看解冻瞬间有没有被白打。',
  ],
] as const

export type BehaviorMetricKey = (typeof METRIC_DEFS)[number][0]

export interface BehaviorMetricDef {
  /** 逐局账本的键名（与 metrics 族同名）。 */
  key: BehaviorMetricKey
  label: string
  /** 表头 / 图标题的 hover 口径。 */
  title: string
  /** 展示格式：整数 tick / 一位小数 / 0–1 百分比。 */
  fmt: BehaviorMetricFmt
  /** 配置弹窗里的分组（七个大类，互斥）。 */
  group: BehaviorMetricGroupKey
  /** 配置弹窗底部的大白话释义（是什么、拿它看什么）。 */
  hint: string
}

/** 完整逐局指标集（90 个 rollout metrics + 2 个 eval 专属读数）。 */
export const BEHAVIOR_METRICS: readonly BehaviorMetricDef[] = METRIC_DEFS.map(
  ([key, label, fmt, title, group, hint]) => ({ key, label, fmt, title, group, hint }),
)

/** @deprecated 旧名（本文件最初只接 eval）；新代码用 `BEHAVIOR_METRICS`。 */
export const EVAL_BEHAVIOR_METRICS = BEHAVIOR_METRICS

export const BEHAVIOR_METRIC_KEYS: readonly BehaviorMetricKey[] = BEHAVIOR_METRICS.map((m) => m.key)

/** @deprecated 旧名；新代码用 `BEHAVIOR_METRIC_KEYS`。 */
export const EVAL_BEHAVIOR_METRIC_KEYS = BEHAVIOR_METRIC_KEYS

/** 逐指标统计量偏好（key = 指标 key；默认全部均值）。 */
export type BehaviorMetricStatMap = Record<BehaviorMetricKey, MetricStatKey>

export const DEFAULT_BEHAVIOR_METRIC_STATS: BehaviorMetricStatMap = Object.fromEntries(
  BEHAVIOR_METRICS.map((m) => [m.key, 'mean' as MetricStatKey]),
) as BehaviorMetricStatMap

/** localStorage → 逐指标统计量。
 *  兼容旧版单值（如 `'median'`，铺到全部指标）；新格式 `{metricKey: stat}`；
 *  坏 JSON / 未知键 / 非法值退默认均值。 */
export function parseBehaviorMetricStats(raw: string | null | undefined): BehaviorMetricStatMap {
  const out = { ...DEFAULT_BEHAVIOR_METRIC_STATS }
  if (!raw) return out
  if (isMetricStat(raw)) {
    for (const m of BEHAVIOR_METRICS) out[m.key] = raw
    return out
  }
  try {
    const o = JSON.parse(raw) as Record<string, unknown> | null
    if (!o || typeof o !== 'object') return out
    for (const m of BEHAVIOR_METRICS) {
      const v = o[m.key]
      if (isMetricStat(v)) out[m.key] = v
    }
  } catch {
    /* 坏 JSON 退默认 */
  }
  return out
}

const BEHAVIOR_BY_KEY = new Map<string, BehaviorMetricDef>(BEHAVIOR_METRICS.map((m) => [m.key, m]))

export function behaviorMetricDef(key: string): BehaviorMetricDef | undefined {
  return BEHAVIOR_BY_KEY.get(key)
}

/** 统计值展示（表格与趋势轴共用；缺值 = —，不冒充 0）。 */
export function fmtMetricStat(v: number | null | undefined, def: BehaviorMetricDef): string {
  if (v == null || !Number.isFinite(v)) return '—'
  if (def.fmt === 'pct') return `${(v * 100).toFixed(1)}%`
  if (def.fmt === 'dec1') return v.toFixed(1)
  return Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1)
}

// ────────────────────────── 走势图显示配置 ──────────────────────────

/** 现有六项（默认全部显示；它们不是逐局分布，统计值切换只作用于逐局指标）。 */
export const TREND_BASE_CHARTS = [
  {
    key: 'winRate',
    label: '胜率',
    hint: 'rollout 采样胜率：训练时边打边算，含探索噪声。看整体水平，别拿单点当判决。',
  },
  {
    key: 'dmgPerKill',
    label: '承伤/杀',
    hint: '每击杀一次要挨多少伤害（按命数×满血归一）。越低说明越会周旋，不是靠换血赢。',
  },
  {
    key: 'winTicks',
    label: '胜局耗时',
    hint: '赢下来的局平均用了多久（ticks）。太长说明能赢但拖；要和胜率一起看。',
  },
  {
    key: 'kills',
    label: '击杀',
    hint: '歼灭率 = 总击杀 / 关卡总敌数。看这一轮把场上敌人清掉了多少。',
  },
  {
    key: 'winHp',
    label: '胜局残血',
    hint: '赢的局结束时还剩多少血（按满血归一）。越高说明赢得越从容。',
  },
  {
    key: 'pu',
    label: '道具',
    hint: '每局平均拾取 / 掉落道具数。看有没有主动吃资源。',
  },
] as const

export type TrendBaseKey = (typeof TREND_BASE_CHARTS)[number]['key']
/** 逐局指标走势键：带 `bm:` 前缀，避开与六项基础图（如 `kills`）的键冲突。 */
export type BehaviorTrendKey = `bm:${BehaviorMetricKey}`
export type TrendChartKey = TrendBaseKey | BehaviorTrendKey

export function behaviorTrendKey(key: BehaviorMetricKey): BehaviorTrendKey {
  return `bm:${key}`
}

export const DEFAULT_TREND_VISIBILITY: Record<TrendChartKey, boolean> = Object.fromEntries([
  ...TREND_BASE_CHARTS.map((c) => [c.key, true]),
  ...BEHAVIOR_METRICS.map((m) => [behaviorTrendKey(m.key), false]),
]) as Record<TrendChartKey, boolean>

/** localStorage JSON → 显示表；缺键/坏值一律退默认，未知键忽略（旧版本残留）。 */
export function parseTrendVisibility(
  raw: string | null | undefined,
): Record<TrendChartKey, boolean> {
  const out: Record<TrendChartKey, boolean> = { ...DEFAULT_TREND_VISIBILITY }
  if (!raw) return out
  try {
    const o = JSON.parse(raw) as Record<string, unknown> | null
    if (!o || typeof o !== 'object') return out
    for (const k of Object.keys(out) as TrendChartKey[]) {
      if (typeof o[k] === 'boolean') out[k] = o[k]
    }
  } catch {
    /* 坏 JSON 退默认 */
  }
  return out
}

/** 浮窗里六条统计曲线的配色（与卡片单图区分；颜色只用于 SVG 属性）。 */
export const METRIC_STAT_COLORS: Record<MetricStatKey, string> = {
  mean: 'var(--accent)',
  median: 'var(--green)',
  min: 'var(--yellow)',
  max: 'var(--red)',
  p90: '#8b5cf6',
  p99: '#0ea5e9',
}
