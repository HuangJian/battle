/* gap-text-table: 把 gap2-compare 的全局表翻译成大白话 text 表格（reported-grade）。
 *
 * Usage:
 *   bun tools/gap-text-table.ts <compare.txt> [human.jsonl nn.jsonl]
 *     <compare.txt> = `bun tmp/gap2-compare.ts <human.jsonl> <nn.jsonl> <label>` 的 stdout 落盘。
 *     panel 两件套可选：给了就对零中位行显示均值（标“均值”），不给就打 ※零膨胀标。
 *   bun tools/gap-text-table.ts --means human.jsonl NAME1=nn1.jsonl [NAME2=nn2.jsonl ...]
 *     多权重均值对照表（等宽纯文本，中英混排按显示宽度对齐；注=既定结论，与具体哪几个NN无关）。
 *   bun tools/gap-text-table.ts --smart <compare.txt> human.jsonl nn.jsonl
 *     智能选型：逐行在“中位/均值”里自动选（规则见 smartPick，行尾打标），
 *     专治中位误导（零幻觉/方向反转/量级差两倍以上）。
 * 输出：大局行 + 闪避/进攻/走位三表 + 收尾三句（火力/最大gap/NN内部分层），与归档版式逐字同构。
 * 行规格写死在本文件 SPECS（特征名精确匹配 compare 输出；compare 加列不影响旧行）。
 * 防误读（2026-10-10 冰冻误判前科）：中位为 0 不代表没有。MEANFN 与 gap2-compare.ts
 * 的 Feat get() 逐字同义（rate 分母 aliveTicks、null 跳过；口径见该文件 ~124-199 行），
 * 漂移了以 gap2-compare 为准，改这里时必须对着改。
 * 订正（2026-10-11，it165 gap 实证）：D06 非重炮——每中伤害 94/96/105 三组同款，
 * gap 全系 D07 命中率 + 局短虚高（perBullet 与局长 corr −0.65）；D08 距离中介不成立
 * （组内逃脱率 vs 平均敌距 corr≈0/−0.4），不定价（分母威胁暴露可操纵 + 无动作梯度）。
 */
import { readFileSync } from 'fs'

type Spec = { code: string; feat: string; plain: string }
type Axis = { title: string; rows: Spec[] }

const SPECS: Axis[] = [
  {
    title: '闪避（挨打相关）',
    rows: [
      { code: 'D01', feat: '承伤/千tick', plain: '每千拍挨多少血' },
      { code: 'D02', feat: '扣血次数/千tick', plain: '每千拍扣血几次' },
      { code: 'D03', feat: '危险lane/千tick', plain: '在危险格子里的时间' },
      { code: 'D04', feat: '被打→脱离暴露延迟(tick, 均值)', plain: '挨打后多久跑出危险区（拍）' },
      { code: 'D05', feat: '被打后300t内贴脸/千tick', plain: '挨打后还贴脸的时间' },
      { code: 'D06', feat: '每发敌弹均伤', plain: '敌人每发子弹咬掉多少血' },
      { code: 'D07', feat: '被弹率(非致命口径) = 扣血/敌弹', plain: '敌人子弹命中率' },
      { code: 'D08', feat: '极限逃脱率(威胁弹未中)', plain: '必死弹躲开的比例' },
      { code: 'D09', feat: '4格内主动接近/千tick', plain: '4格内主动往上凑' },
      { code: 'D10', feat: '4格内后撤/千tick', plain: '4格内往后撤' },
      { code: 'D11', feat: '4格内后撤占比 (后撤/(接近+后撤))', plain: '进退里后撤占几成' },
      { code: 'D12', feat: '冰盾豁免(冻∨盾道具)/千tick', plain: '冻住/护盾免掉的伤' },
      { code: 'D13', feat: '· 其中冻结/千tick', plain: '其中“冻住”免的' },
      { code: 'D14', feat: '出生/复活盾(非豁免)/千tick', plain: '出生保护罩占比' },
      { code: 'D15', feat: '无头乱闯率(非威胁弹命中)', plain: '没威胁的子弹打中你' },
      { code: 'D16', feat: '敌弹近失未中(曾≤1.5格)/千tick', plain: '威胁弹擦身而过没中' },
    ],
  },
  {
    title: '进攻（开火相关）',
    rows: [
      { code: 'A01', feat: '命中率 = 中/弹', plain: '开100炮中几炮' },
      { code: 'A02', feat: '对准率 = 对准开火/开火', plain: '开火时炮口对准敌人的比例' },
      { code: 'A03', feat: '对准开火/千tick', plain: '瞄准了才开火的次数' },
      { code: 'A04', feat: '未对准开火/千tick', plain: '没瞄准就开火的次数' },
      { code: 'A05', feat: '开火时敌距(格)', plain: '开火时离敌人几格' },
      { code: 'A06', feat: '≤4格开火占比', plain: '贴脸（≤4格）开火占几成' },
      { code: 'A07', feat: '>6格开火占比', plain: '远射（>6格）占几成' },
      { code: 'A08', feat: '移动中开火占比', plain: '跑着开火占几成' },
      { code: 'A09', feat: '预判开火：盲射命中率', plain: '蒙一枪中的率' },
      { code: 'A10', feat: '打出伤害/千tick', plain: '每千拍打出的伤害' },
      { code: 'A11', feat: '击杀/千tick', plain: '每千拍杀几个' },
      { code: 'A12', feat: '击杀间隔(tick, 均值)', plain: '平均几拍杀一个' },
      { code: 'A13', feat: '首杀 tick', plain: '第一个击杀在第几拍' },
      { code: 'A14', feat: '每中伤害', plain: '每发命中伤害' },
    ],
  },
  {
    title: '走位（站位相关）',
    rows: [
      { code: 'W01', feat: '停(无输入)/千tick', plain: '每千拍停下几拍' },
      { code: 'W02', feat: '停∧≤4格/千tick', plain: '危险区里停下几拍' },
      { code: 'W03', feat: '被包围(n≥2敌火线)/千tick', plain: '被两面以上包夹的时间' },
      { code: 'W04', feat: '被包围占比(n≥2/存活)', plain: '包围时间占存活比' },
      { code: 'W05', feat: '角落·加权(0/1/2格=3/2/1)/千tick', plain: '蹲角落加权时间' },
      { code: 'W06', feat: '角落·贴角(d=0)tick/千tick', plain: '真正贴角的时间' },
      { code: 'W07', feat: '撞墙(有输入没动)/千tick', plain: '撞墙（推摇杆不动）' },
      { code: 'W08', feat: '撞墙 episode 均长(tick)', plain: '一次顶住平均几拍' },
      { code: 'W09', feat: '顶住/移动输入', plain: '顶住占移动输入' },
      { code: 'W10', feat: '转向原地/千tick', plain: '原地打转' },
      { code: 'W11', feat: '撞墙 episode 数/千tick', plain: '撞墙次数（碰一下就算）' },
      { code: 'W12', feat: '平均敌距(格)', plain: '平均离敌人几格' },
      { code: 'W13', feat: '首个≤4格接触 tick', plain: '第一次贴脸在第几拍' },
      { code: 'W14', feat: '去重格数/局', plain: '逛过的格子数/局' },
      { code: 'W15', feat: '重复格占比 = (换格-去重)/换格', plain: '重复逛占比' },
    ],
  },
]

const pad = (s: string, w: number): string => (s.length >= w ? s : s + ' '.repeat(w - s.length))
const rpad = (s: string, w: number): string => (s.length >= w ? s : ' '.repeat(w - s.length) + s)

type Row = Record<string, any>

// 与 gap2-compare.ts 同义（~114-199 行）：分母 aliveTicks，null/非有限跳过。
const rate = (r: Row, v: number | null | undefined): number | null =>
  r.aliveTicks > 0 && v !== null && v !== undefined ? (v / r.aliveTicks) * 1000 : null

// compare 特征名 → panel 行取值（逐字对 gap2-compare.ts Feat get()；漂移以那边为准）。
const MEANFN = new Map<string, (r: Row) => number | null>([
  ['承伤/千tick', (r) => rate(r, r.dmgTotal)],
  ['扣血次数/千tick', (r) => rate(r, r.dmgEvents)],
  ['危险lane/千tick', (r) => rate(r, r.threatTicks)],
  ['[旧混列]freeze∨任意盾/千tick', (r) => rate(r, r.exemptAnyTicks)],
  ['冰盾豁免(冻∨盾道具)/千tick', (r) => rate(r, r.exIceShieldTicks)],
  ['· 其中冻结/千tick', (r) => rate(r, r.exFreezeTicks)],
  ['· 其中护盾道具/千tick', (r) => rate(r, r.exShieldPUTicks)],
  ['出生/复活盾(非豁免)/千tick', (r) => rate(r, r.exSpawnTicks)],
  ['星盾类哨兵(hard 应=0)/千tick', (r) => rate(r, r.exStarLikeTicks)],
  ['被打→脱离暴露延迟(tick, 均值)', (r) => r.escapeLatency],
  ['可结算逃逸窗/千tick(被打时在险内)', (r) => rate(r, r.escapeN)],
  ['局终仍未脱离窗口数/局', (r) => r.escapeOpenAtEnd],
  ['被打后300t内贴脸/千tick', (r) => rate(r, r.postHitNear300)],
  ['被弹率(非致命口径) = 扣血/敌弹', (r) => (r.enemyShots > 0 ? r.dmgEvents / r.enemyShots : null)],
  ['每发敌弹均伤', (r) => (r.enemyShots > 0 ? r.dmgTotal / r.enemyShots : null)],
  [
    '4格内后撤占比 (后撤/(接近+后撤))',
    (r) =>
      r.approachTicks + r.retreatTicks > 0
        ? r.retreatTicks / (r.approachTicks + r.retreatTicks)
        : null,
  ],
  ['极限逃脱率(威胁弹未中)', (r) => (r.threatShots > 0 ? r.threatDodged / r.threatShots : null)],
  [
    '极限逃脱率·非冰(开火刻无冰盾)',
    (r) => {
      const s = (r.threatShots ?? 0) - (r.threatShotsIce ?? 0)
      return s > 0 ? ((r.threatDodged ?? 0) - (r.threatDodgedIce ?? 0)) / s : null
    },
  ],
  ['极限逃脱(曾≤1.5格)/千tick', (r) => rate(r, r.threatDodgeNarrow)],
  [
    '无头乱闯率(非威胁弹命中)',
    (r) => (r.recklessShots > 0 ? r.recklessHits / r.recklessShots : null),
  ],
  ['无头乱闯命中/千tick', (r) => rate(r, r.recklessHits)],
  ['敌弹近失未中(曾≤1.5格)/千tick', (r) => rate(r, r.enemyNearMiss)],
  ['4格内主动接近/千tick', (r) => rate(r, r.approachTicks)],
  ['4格内后撤/千tick', (r) => rate(r, r.retreatTicks)],
  ['命中率 = 中/弹', (r) => (r.shots > 0 ? r.hits / r.shots : null)],
  ['对准率 = 对准开火/开火', (r) => (r.shots > 0 ? r.alignedShots / r.shots : null)],
  ['持向对准率(每10t采样, K无关)', (r) => r.aimAlignedShare],
  ['对准开火/千tick', (r) => rate(r, r.alignedShots)],
  ['未对准开火/千tick', (r) => rate(r, r.shots - r.alignedShots)],
  ['开火时敌距(格)', (r) => r.shotRangeMean],
  ['≤4格开火占比', (r) => (r.shots > 0 ? r.shotsNear4 / r.shots : null)],
  ['>6格开火占比', (r) => (r.shots > 0 ? r.shotsFar / r.shots : null)],
  ['移动中开火占比', (r) => (r.shots > 0 ? r.shotsMoving / r.shots : null)],
  ['预判开火：盲射命中率', (r) => (r.leadShots > 0 ? r.leadHits / r.leadShots : null)],
  [
    '· 盲射带外有敌命中率',
    (r) => (r.leadShotsAheadOff > 0 ? r.leadHitsAheadOff / r.leadShotsAheadOff : null),
  ],
  [
    '· 盲射前方全空命中率',
    (r) => {
      const d = r.leadShots - r.leadShotsAheadOff
      return d > 0 ? (r.leadHits - r.leadHitsAheadOff) / d : null
    },
  ],
  ['· 盲射命中/千tick', (r) => rate(r, r.leadHits)],
  ['打出伤害/千tick', (r) => rate(r, r.dmgDealt)],
  ['每中伤害', (r) => (r.hits > 0 ? r.dmgDealt / r.hits : null)],
  ['击杀/千tick', (r) => rate(r, r.kills)],
  ['击杀间隔(tick, 均值)', (r) => r.killSpacing],
  ['首杀 tick', (r) => (r.firstKillTick < 0 ? null : r.firstKillTick)],
  // v6：stopTicks = hold=1 或 legacy 无输入（两代回放/策略同一口径，见 idle-rate.ts）。
  ['停(无输入/hold)/千tick', (r) => rate(r, r.stopTicks)],
  ['停∧≤4格/千tick', (r) => rate(r, r.stopNear4Ticks)],
  ['停中≤4格占比', (r) => (r.stopTicks > 0 ? r.stopNear4Ticks / r.stopTicks : null)],
  ['被包围(n≥2敌火线)/千tick', (r) => rate(r, r.surroundTicks)],
  ['被包围·数目加权/千tick', (r) => rate(r, r.surroundW)],
  ['被包围·冰盾豁免后/千tick', (r) => rate(r, r.surroundTicksEx)],
  ['被包围加权·冰盾豁免后/千tick', (r) => rate(r, r.surroundWEx)],
  ['被包围占比(n≥2/存活)', (r) => (r.aliveTicks > 0 ? r.surroundTicks / r.aliveTicks : null)],
  ['角落·加权(0/1/2格=3/2/1)/千tick', (r) => rate(r, r.cornerW)],
  ['角落·加权·冰盾豁免后/千tick', (r) => rate(r, r.cornerWEx)],
  ['角落·加权豁免保留占比(净/raw)', (r) => (r.cornerW > 0 ? r.cornerWEx / r.cornerW : null)],
  ['角落·贴角(d=0)tick/千tick', (r) => rate(r, r.cornerTicks)],
  ['角落·贴角·冰盾豁免后/千tick', (r) => rate(r, r.cornerTicksEx)],
  ['撞墙(有输入没动)/千tick', (r) => rate(r, r.blockedTicks)],
  ['撞墙 episode 数/千tick', (r) => rate(r, r.blockedRuns)],
  ['最长撞墙 episode(tick)', (r) => r.blockedMax],
  ['同向顶住(no-move)/千tick', (r) => rate(r, r.pressNoMoveTicks)],
  ['转向原地/千tick', (r) => rate(r, r.turnNoMoveTicks)],
  ['顶住/移动输入', (r) => (r.moveTicks > 0 ? r.pressNoMoveTicks / r.moveTicks : null)],
  ['撞墙 episode 均长(tick)', (r) => (r.blockedRuns > 0 ? r.blockedTicks / r.blockedRuns : null)],
  ['撞墙/移动输入', (r) => (r.moveTicks > 0 ? r.blockedTicks / r.moveTicks : null)],
  ['重复格占比 = (换格-去重)/换格', (r) => r.revisitRatio],
  ['≤4格近敌/千tick', (r) => rate(r, r.near4)],
  ['平均敌距(格)', (r) => r.distMean],
  ['首个≤4格接触 tick', (r) => (r.firstContactTick < 0 ? null : r.firstContactTick)],
  ['去重格数/局', (r) => r.cells],
])

const meanOf = (rs: Row[], f: (r: Row) => number | null): number | null => {
  const vs = rs.map(f).filter((v): v is number => v !== null && Number.isFinite(v))
  if (vs.length === 0) return null
  return vs.reduce((a, b) => a + b, 0) / vs.length
}

const fmt = (v: number): string => {
  const a = Math.abs(v)
  if (a !== 0 && (a >= 10000 || a < 0.001)) return v.toExponential(2)
  return String(Math.round(v * 1000) / 1000)
}

const loadPanels = (
  hp: string | undefined,
  np: string | undefined,
): { h: Row[]; n: Row[] } | null => {
  if (!hp || !np) return null
  try {
    const load = (p: string): Row[] =>
      readFileSync(p, 'utf8')
        .trim()
        .split('\n')
        .filter(Boolean)
        .map((l) => JSON.parse(l))
    return { h: load(hp), n: load(np) }
  } catch {
    return null
  }
}

// 零膨胀处理：任一侧中位为 0 时，有 panel 就显示均值（标“均值”），
// 没 panel 就打 ※零膨胀标（2026-10-10 冰冻误判前科：中位 0 不代表没有）。
const zeroWarn = (h: string, n: string): string => {
  const fh = parseFloat(h)
  const fn = parseFloat(n)
  if (Number.isNaN(fh) || Number.isNaN(fn)) return ''
  return fh === 0 || fn === 0 ? '  ※零膨胀' : ''
}

// 零中位行取均值（panel 口径与 MEANFN 同源；任一侧无有效值即 null）。
const zeroMeans = (
  panels: { h: Row[]; n: Row[] } | null,
  feat: string,
  h: string,
  n: string,
): { hs: string; ns: string; tag: string } | null => {
  if (!panels) return null
  const fh = parseFloat(h)
  const fn = parseFloat(n)
  if (Number.isNaN(fh) || Number.isNaN(fn) || (fh !== 0 && fn !== 0)) return null
  const f = MEANFN.get(feat)
  if (!f) return null
  const mh = meanOf(panels.h, f)
  const mn = meanOf(panels.n, f)
  if (mh === null || mn === null) return null
  return { hs: fmt(mh), ns: fmt(mn), tag: '（均值）' }
}

// 等宽纯文本：中日韩字符按 2 列计宽（复制粘贴不对碎；等宽字体下对齐）。
const dw = (s: string): number => {
  let w = 0
  for (const ch of s) w += ch.codePointAt(0)! > 255 ? 2 : 1
  return w
}
const tpad = (s: string, w: number): string => {
  s = String(s)
  const p = w - dw(s)
  return s + ' '.repeat(Math.max(0, p))
}

const meansMode = (args: string[]): void => {
  // args: [human.jsonl, NAME1=p1, NAME2=p2, ...]
  const hp = args[0]
  const others: Array<{ label: string; path: string }> = []
  for (const a of args.slice(1)) {
    const i = a.indexOf('=')
    if (i < 0) {
      console.error(`bad panel arg (want NAME=path): ${a}`)
      process.exit(2)
    }
    others.push({ label: a.slice(0, i), path: a.slice(i + 1) })
  }
  const load = (p: string): Row[] =>
    readFileSync(p, 'utf8')
      .trim()
      .split('\n')
      .filter(Boolean)
      .map((l) => JSON.parse(l))
  let human: Row[]
  try {
    human = load(hp)
  } catch {
    console.error(`cannot read ${hp}`)
    process.exit(2)
  }
  const cols: Array<{ label: string; rows: Row[] }> = [{ label: '人', rows: human }]
  for (const o of others) {
    try {
      cols.push({ label: o.label, rows: load(o.path) })
    } catch {
      console.error(`cannot read ${o.path}`)
      process.exit(2)
    }
  }
  // 注栏：与具体哪几个 NN 无关的既定结论（定价/排除依据）。arm 相关的名次判断人写，脚本不出。
  const NOTES = new Map<string, string>([
    ['承伤/千tick', '账单本人'],
    ['扣血次数/千tick', '挨打频率'],
    ['危险lane/千tick', '威胁税方向对'],
    ['被打→脱离暴露延迟(tick, 均值)', '脱离是堵死的墙'],
    ['被打后300t内贴脸/千tick', '贴脸是绝症'],
    ['每发敌弹均伤', '命中率问题，局短虚高；炮弹同款'],
    ['被弹率(非致命口径) = 扣血/敌弹', '暴露多+不会躲'],
    ['极限逃脱率(威胁弹未中)', '结果率，分母可操纵，不定价'],
    ['4格内主动接近/千tick', '多动症'],
    ['4格内后撤/千tick', '瞎跑'],
    ['4格内后撤占比 (后撤/(接近+后撤))', '方向一样，量的问题'],
    ['冰盾豁免(冻∨盾道具)/千tick', '均值版基本无差'],
    ['· 其中冻结/千tick', '小差，“完全不用”是误判'],
    ['出生/复活盾(非豁免)/千tick', '局短虚高'],
    ['无头乱闯率(非威胁弹命中)', '小毛病'],
    ['敌弹近失未中(曾≤1.5格)/千tick', '无故事'],
    ['命中率 = 中/弹', '机更准，别加准头'],
    ['对准率 = 对准开火/开火', '端正没用'],
    ['对准开火/千tick', '开火量不是问题'],
    ['未对准开火/千tick', '人敢蒙，机太老实'],
    ['开火时敌距(格)', '人打远的'],
    ['≤4格开火占比', '近射税方向对'],
    ['>6格开火占比', '远射奖没反应'],
    ['移动中开火占比', '都跑着开；停≠开火'],
    ['预判开火：盲射命中率', '分母小别信'],
    ['· 盲射带外有敌命中率', '样本小别信'],
    ['· 盲射前方全空命中率', '样本小别信'],
    ['· 盲射命中/千tick', '样本小别信'],
    ['打出伤害/千tick', '赢靠活得久，不靠打得多'],
    ['击杀/千tick', '火力无辜，别加攻击'],
    ['击杀间隔(tick, 均值)', 'ns别解读'],
    ['首杀 tick', '开局慢热度'],
    ['每中伤害', '子弹同款'],
    ['停(无输入)/千tick', '停是必要不充分'],
    ['停∧≤4格/千tick', '只敢安全区停'],
    ['被包围(n≥2敌火线)/千tick', '包围频率不是gap'],
    ['被包围占比(n≥2/存活)', '同上'],
    ['被包围·数目加权/千tick', '同上'],
    ['被包围·冰盾豁免后/千tick', '同上'],
    ['被包围加权·冰盾豁免后/千tick', '同上'],
    ['角落·加权(0/1/2格=3/2/1)/千tick', '贴的是角落区不是角'],
    ['角落·加权·冰盾豁免后/千tick', '同上'],
    ['角落·加权豁免保留占比(净/raw)', '同上'],
    ['角落·贴角(d=0)tick/千tick', '绝对值都小'],
    ['角落·贴角·冰盾豁免后/千tick', '绝对值都小'],
    ['撞墙(有输入没动)/千tick', '下一轮硬骨头'],
    ['撞墙 episode 数/千tick', '罚碰不如罚顶住'],
    ['最长撞墙 episode(tick)', '顶住不撒手'],
    ['同向顶住(no-move)/千tick', '顶住不撒手'],
    ['转向原地/千tick', '小毛病'],
    ['顶住/移动输入', '1/6白推'],
    ['撞墙 episode 均长(tick)', '顶住不撒手'],
    ['撞墙/移动输入', '1/6白推'],
    ['重复格占比 = (换格-去重)/换格', '人绕最多，别罚'],
    ['≤4格近敌/千tick', '贴脸的另一面'],
    ['平均敌距(格)', '三机一样'],
    ['首个≤4格接触 tick', '开局rush度'],
    ['去重格数/局', '局短则少'],
  ])

  const NW = 6
  const PW = 28
  const VW = 9
  const ZW = 30
  console.log('三腿最优 vs 人类·等宽纯文本版（gap2 panel jsonl 均值；注=既定结论）')
  console.log('')
  console.log(
    tpad('编号', NW) +
      tpad('这是啥', PW) +
      cols.map((c) => tpad(c.label, VW)).join('') +
      tpad('注', ZW),
  )
  for (const ax of SPECS) {
    console.log(`---- ${ax.title} ----`)
    for (const r of ax.rows) {
      const f = MEANFN.get(r.feat)
      const cells = cols.map((c) => {
        if (!f) return tpad('—', VW)
        const v = meanOf(c.rows, f)
        return tpad(v === null ? '—' : fmt(v), VW)
      })
      console.log(
        tpad(r.code, NW) + tpad(r.plain, PW) + cells.join('') + tpad(NOTES.get(r.feat) ?? '', ZW),
      )
    }
    console.log('')
  }
  console.log('---- 大局（均值；通关为计数） ----')
  for (const c of cols) {
    const rs = c.rows
    const clear = rs.filter((x) => x.outcome === 'clear').length
    console.log(
      tpad(c.label === '人' ? '通关' : `通关${c.label}`, NW) +
        tpad('', PW) +
        tpad(String(clear), VW),
    )
  }
  const bigRow = (name: string, f: (x: Row) => number): void => {
    console.log(
      tpad('', NW) +
        tpad(name, PW) +
        cols
          .map((c) =>
            tpad(fmt(c.rows.reduce((a, x) => a + f(x), 0) / Math.max(1, c.rows.length)), VW),
          )
          .join(''),
    )
  }
  bigRow('局长拍', (x) => x.ticks ?? 0)
  bigRow('击杀', (x) => x.kills ?? 0)
  bigRow('承伤', (x) => x.dmgTotal ?? 0)
}

// 智能选型（确定性规则，无超参玄学）：
//   规则0（零幻觉）：任一侧中位为 0 → 均值（中位塌了没信息）。
//   规则1（方向反转）：中位 gap 与均值 gap 异号 → 均值 + FLIP 标。
//   规则2（量级差两倍以上）：|gap_med| 与 |gap_mean| 比值超出 [0.5, 2] → 均值 + MAG 标。
//   否则中位（稳健默认）。gap 接近 0（<1e-9 两边）时不触发规则 1/2。
//   已知盲区：小样本幻觉（A09 盲射）三条全抓不住，看分母。
const smartPick = (
  mh: number,
  mn: number,
  gh: number,
  gn: number,
): { useMean: boolean; tag: string } => {
  if (mh === 0 || mn === 0) return { useMean: true, tag: '（均值·零中位）' }
  const gapMed = mh - mn
  const gapMean = gh - gn
  if (Math.abs(gapMed) < 1e-9 && Math.abs(gapMean) < 1e-9) return { useMean: false, tag: '' }
  if (gapMed * gapMean < 0) return { useMean: true, tag: '（均值·方向反转）' }
  const lo = Math.min(Math.abs(gapMed), Math.abs(gapMean))
  const hi = Math.max(Math.abs(gapMed), Math.abs(gapMean))
  if (lo < 1e-9 || hi / lo > 2) return { useMean: true, tag: '（均值·量级差2x+）' }
  return { useMean: false, tag: '' }
}

const smartMode = (comparePath: string, hp: string, np: string): void => {
  const lines = readFileSync(comparePath, 'utf8').split('\n')
  const byFeat = new Map<string, { h: string; n: string; cliff: string; p: string }>()
  for (const l of lines) {
    const m = l.match(
      /^(\S+)\s+(.+?)\s{2,}([0-9.<>=][0-9.eE<>=.\-]*)\s+([0-9.<>=][0-9.eE<>=.\-]*)\s+(-?[0-9.]+)\s+(\S+)\s*$/,
    )
    if (!m) continue
    const [, , feat, h, n, cliff, p] = m
    byFeat.set(feat.trim(), { h, n, cliff, p })
  }
  const load = (p: string): Row[] =>
    readFileSync(p, 'utf8')
      .trim()
      .split('\n')
      .filter(Boolean)
      .map((l) => JSON.parse(l))
  let human: Row[]
  let nn: Row[]
  try {
    human = load(hp)
    nn = load(np)
  } catch {
    console.error('cannot read panels')
    process.exit(2)
  }
  console.log('智能选型表（逐行自动选中位/均值；标说明原因；规则见 smartPick）：')
  console.log('')
  for (const ax of SPECS) {
    console.log(`---- ${ax.title} ----`)
    console.log(`${tpad('编号', 6)}${tpad('这是啥', 30)}${tpad('人', 12)}${tpad('机', 12)}  差`)
    for (const r of ax.rows) {
      const v = byFeat.get(r.feat)
      if (!v) {
        console.log(
          `${tpad(r.code, 6)}${tpad(r.plain, 30)}${tpad('—', 12)}${tpad('—', 12)}本批无此行`,
        )
        continue
      }
      const f = MEANFN.get(r.feat)
      const mh = parseFloat(v.h)
      const mn = parseFloat(v.n)
      let hs = v.h
      let ns = v.n
      let tag = `  cliff ${v.cliff}`
      if (f && !Number.isNaN(mh) && !Number.isNaN(mn)) {
        const gh = meanOf(human, f)
        const gn = meanOf(nn, f)
        if (gh !== null && gn !== null) {
          const pick = smartPick(mh, mn, gh, gn)
          if (pick.useMean) {
            hs = fmt(gh)
            ns = fmt(gn)
            tag = `  cliff ${v.cliff}${pick.tag}`
          }
        }
      }
      console.log(`${tpad(r.code, 6)}${tpad(r.plain, 30)}${tpad(hs, 12)}${tpad(ns, 12)}${tag}`)
    }
    console.log('')
  }
}

const main = (): void => {
  if (process.argv[2] === '--smart') {
    const [, , , comparePath, hp, np] = process.argv
    if (!comparePath || !hp || !np) {
      console.error('usage: bun tools/gap-text-table.ts --smart <compare.txt> human.jsonl nn.jsonl')
      process.exit(2)
    }
    smartMode(comparePath, hp, np)
    return
  }
  if (process.argv[2] === '--means') {
    meansMode(process.argv.slice(3))
    return
  }
  const path = process.argv[2]
  if (!path) {
    console.error('usage: bun tools/gap-text-table.ts <compare.txt> [human.jsonl nn.jsonl]')
    process.exit(2)
  }
  const panels = loadPanels(process.argv[3], process.argv[4])
  const lines = readFileSync(path, 'utf8').split('\n')
  const byFeat = new Map<string, { h: string; n: string; cliff: string; p: string }>()
  for (const l of lines) {
    const m = l.match(
      /^(\S+)\s+(.+?)\s{2,}([0-9.<>=][0-9.eE<>=.\-]*)\s+([0-9.<>=][0-9.eE<>=.\-]*)\s+(-?[0-9.]+)\s+(\S+)\s*$/,
    )
    if (!m) continue
    const [, , feat, h, n, cliff, p] = m
    byFeat.set(feat.trim(), { h, n, cliff, p })
  }
  const after = (s: string, key: string): string => {
    const i = s.indexOf(key)
    return i < 0 ? '' : s.slice(i + key.length).trim()
  }
  const headClear = lines.find((l) => l.indexOf('通关：人类') === 0) ?? ''
  const headLen = lines.find((l) => l.indexOf('局长(tick)') === 0) ?? ''
  let clearH = ''
  let clearN = ''
  if (headClear) {
    const parts = headClear.split('·')
    clearH = after(parts[0] ?? '', '通关：人类')
    clearN = after(parts[1] ?? '', 'NN')
  }
  // “局长4959 · NN 2863 | 击杀…人17 · NN 10 | 承伤…人100 · NN 228”
  const cells: string[] = []
  for (const seg of headLen.split('|')) {
    for (const half of seg.split('·')) {
      const h = half.indexOf('人类')
      const n = half.indexOf('NN')
      if (h >= 0)
        cells.push(
          half
            .slice(h + 2)
            .trim()
            .split(/\s+/)
            .pop() ?? '',
        )
      else if (n >= 0)
        cells.push(
          half
            .slice(n + 2)
            .trim()
            .split(/\s+/)
            .pop() ?? '',
        )
    }
  }
  // cells = [局长人, 局长机, 击杀人, 击杀机, 承伤人, 承伤机]
  console.log(
    '口径：“/千tick”=每活1000拍里发生几次；中位=160局中间那局；cliff=差距大小（1封顶，负号表示机器人那边大）。',
  )
  console.log('')
  if (cells.length >= 6) {
    console.log(
      `【大局】 通关：人${clearH}，机${clearN}｜局长：${cells[0]} vs ${cells[1]}拍｜击杀：${cells[2]} vs ${cells[3]}｜挨的那点血：${cells[4]} vs ${cells[5]}`,
    )
    console.log('')
  }
  for (const ax of SPECS) {
    console.log(`---- ${ax.title} ----`)
    console.log(`${pad('编号', 6)}${pad('大白话', 30)}${rpad('人', 12)}${rpad('机', 12)}  差`)
    for (const r of ax.rows) {
      const v = byFeat.get(r.feat)
      if (!v) {
        console.log(
          `${pad(r.code, 6)}${pad(r.plain, 30)}${rpad('—', 12)}${rpad('—', 12)}本批无此行`,
        )
        continue
      }
      const zm = zeroMeans(panels, r.feat, v.h, v.n)
      const hs = zm ? zm.hs : v.h
      const ns = zm ? zm.ns : v.n
      const tag = zm ? zm.tag : zeroWarn(v.h, v.n)
      console.log(
        `${pad(r.code, 6)}${pad(r.plain, 30)}${rpad(hs, 12)}${rpad(ns, 12)}  cliff ${v.cliff}${tag}`,
      )
    }
    console.log('')
  }
  const inner = (re: RegExp): string[] | null => {
    const l = lines.find((x) => re.test(x))
    if (!l) return null
    const m = l.match(/中位\s+(\S+) vs 阵亡 中位\s+(\S+) \| cliff (\S+)/)
    return m ? [m[1], m[2], m[3]] : null
  }
  const pickup = inner(/拾取\/局/)
  const hurt = inner(/承伤\/千tick\s+通关/)
  console.log(
    '三句话收尾：火力没问题（A10/A11/A12 全没差，A01 机还更准）；最大的是停和撞墙；NN 自己局里生死只看两样：' +
      `拾取 ${pickup ? `${pickup[0]} vs ${pickup[1]}` : '—'}、挨打 ${hurt ? `${hurt[0]} vs ${hurt[1]}` : '—'}。`,
  )
}

main()
