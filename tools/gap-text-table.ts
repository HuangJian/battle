/* gap-text-table: 把 gap2-compare 的全局表翻译成大白话 text 表格（reported-grade）。
 *
 * Usage: bun tools/gap-text-table.ts <compare.txt>
 *   <compare.txt> = `bun tmp/gap2-compare.ts <human.jsonl> <nn.jsonl> <label>` 的 stdout 落盘。
 * 输出：大局行 + 闪避/进攻/走位三表 + 收尾三句（火力/最大gap/NN内部分层），与归档版式逐字同构。
 * 行规格写死在本文件 SPECS（特征名精确匹配 compare 输出；compare 加列不影响旧行）。
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

const main = (): void => {
  const path = process.argv[2]
  if (!path) {
    console.error('usage: bun tools/gap-text-table.ts <compare.txt>')
    process.exit(2)
  }
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
      console.log(
        `${pad(r.code, 6)}${pad(r.plain, 30)}${rpad(v.h, 12)}${rpad(v.n, 12)}  cliff ${v.cliff}`,
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
