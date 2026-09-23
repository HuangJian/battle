/**
 * tools/perf/kernel-variants.ts —— 从**现役单源内核**派生「单项关闭 / 单项改档」的变体库，
 * 用来回答「这一项优化在**这台机器**上到底值多少」（跨平台归因的现成手段）。
 *
 * ## 为什么要它
 *
 * `tools/perf/conv-ab.ts` 只能回答「新内核 vs 旧内核」；但内核里四项优化的**跨平台**收益
 * 必须逐项量（先例：`8oc×8px` 在 AVX1 快 1.4×、在 AVX2 慢 2.9× ⇒ 整体结论会把这一项盖住）。
 * 2026-09-23 的触发场景：arm64（Termux）实测只有 **1.08–1.11×**，而 x64 是 1.40–1.59× ——
 * 差在哪一项，x64 上的数字回答不了（计划 §4.1 早把 `conv3` 的 4oc 分组列为 arm64 的中风险项）。
 *
 * ## 怎么用（不需要给探针加接口）
 *
 * 探针的「old 侧」本就能指**任意**库（`CONV_AB_OLD_LIB`），所以**一份变体库 = 一个问题**：
 *
 *     bun tools/perf/kernel-variants.ts                    # 生成 ctl/pw8/nog3 × win32-x64+linux-arm64
 *     bun tools/perf/kernel-variants.ts linux-arm64        # 只要某个目标
 *     CONV_AB_OLD_LIB=tmp/kernel-variants/<v>/conv_native-<target>.so bun tools/perf/conv-ab.ts 100 4
 *
 * 打印的 `speedup = 变体 / 现役`：
 *   · `ctl`（源码一字不改）**必须 ≈ 1.00** —— 它是偏置对照；不是 1.00 说明环境/管线有偏差，先查它。
 *   · `> 1` ⇒ 关掉该项更慢 ⇒ 该项在**这台机器**上是收益（保留）。
 *   · `< 1` ⇒ 关掉该项更快 ⇒ 该项在这台机器上是**亏损**，此时才动它（例如给该架构单列一档常量）。
 * 变体与现役库**逐位等价**（探针的 `bitexact` 必须为 OK）是读数的前提：两项都不重排累加序。
 *
 * ## 派生方式（不手抄、不复制算法）
 *
 * 读现役 `src/nn/conv/conv.c` + `conv_native.h` 做**断言过的文本替换**（命中数 ≠ 预期即中止、
 * 不写盘）；`conv3` 的「优化前版本」直接从 git 取（`git log --diff-filter=AM` 定位 —— **不要**
 * 用 `rev-list HEAD -- <路径>`，删除提交也算改动，重组后会命中删除提交）。派生源码留在产物目录里，
 * 便于复核 `diff -u` 确认「只改了想改的那一块」。
 *
 * 纪律：产物落 `tmp/`（gitignore），**不入库**、**不进 `src/`**（`src/nn/` 在 codehash 集内，
 * 放进去 = 触发节点升级波）。本文件在 `tools/perf/`（不在 codehash 集内），提交安全。
 */
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { NATIVE_TARGETS, nativeFlags, nativeLinkArgs } from '../../src/nn/conv/native-prebuilt'

const ROOT = path.resolve(import.meta.dir, '..', '..')
const OUT = path.join(ROOT, 'tmp', 'kernel-variants')
const SRC_DIR = path.join(ROOT, 'src', 'nn', 'conv')
/** 重组前（native 引擎落地）的那一版 —— conv3 的逐 oc 实现从这里取。 */
const OLD_REF = '6ed7f11f1bc3ea4ffbd38a6d44c6ff189b8bca7a'
const OLD_SRC = 'src/nn/native/conv_feats_native.c'

function gitShow(refFile: string): string {
  const r = spawnSync('git', ['show', refFile], {
    cwd: ROOT,
    encoding: 'utf8',
    maxBuffer: 64 << 20,
  })
  if (r.status !== 0) throw new Error(`git show ${refFile} 失败：${r.stderr}`)
  return r.stdout
}

/** 取出一个顶层 `static void <name>(...)` 函数的完整文本（到列 0 的 `}` 为止）。 */
function fnText(src: string, sig: string): string {
  const i = src.indexOf(sig)
  if (i < 0) throw new Error(`找不到 ${sig}`)
  const j = src.indexOf('\n}\n', i)
  if (j < 0) throw new Error(`找不到 ${sig} 的结尾`)
  return src.slice(i, j + 3)
}

/** conv3 里「逐 ic 累加」的主循环块（到 relu 尾循环之前）。 */
function icBlock(conv3: string): string {
  // 头部**必须带前导 \n**：否则 `  for (int oc …` 会匹配到缩进更深的嵌套行（踩过：
  // 4 空格的嵌套 oc 循环里含「2 空格 + 文本」这个子串），截出来的块花括号不平衡。
  const head = '\n  for (int ic = 0; ic < inCh; ic++) {\n'
  const tail = '\n  for (int oc = 0; oc < outCh; oc++) {\n'
  const a = conv3.indexOf(head)
  if (a < 0) throw new Error('conv3 里找不到 ic 循环头')
  const b = conv3.indexOf(tail, a + head.length)
  if (b < 0) throw new Error('conv3 里找不到 relu 尾循环')
  const block = conv3.slice(a + 1, b + 1) // 去掉前导 \n，保留尾部换行
  const open = (block.match(/\{/g) ?? []).length
  const close = (block.match(/\}/g) ?? []).length
  if (open !== close) throw new Error(`ic 块花括号不平衡（{ ${open} vs } ${close}）`)
  return block
}

const convC = fs.readFileSync(path.join(SRC_DIR, 'conv.c'), 'utf8')
const convH = fs.readFileSync(path.join(SRC_DIR, 'conv_native.h'), 'utf8')

interface Variant {
  name: string
  desc: string
  apply(files: Map<string, string>): void
}

const PW_COND_BLOCK = [
  '#if defined(__wasm__) || defined(__wasm32__)',
  '#define CF_PW_PX 8',
  '#else',
  '#define CF_PW_PX 16',
  '#endif',
  '',
].join('\n')

const variants: Variant[] = [
  {
    // 对照组：源码**一字不改**走本脚本的管线。它量的是「非变体因素」的偏置（构建管线、
    // clang 调用序、dlopen 次序）。变体比值必须扣掉它 —— 在可重现的目标上它还会**逐字节相同**
    // 于入库产物（linux/darwin，实测 arm64 = `0909fd03…`），那是最好的自我证明。
    name: 'ctl',
    desc: '对照组：现役源码原样编译（不改一行）',
    apply() {},
  },
  {
    name: 'pw8',
    desc: 'pw 平铺 CF_PW_PX 16 → 8（问：16px 在本机是不是收益）',
    apply(files) {
      const h = files.get('conv_native.h')!
      const n = h.split(PW_COND_BLOCK).length - 1
      if (n !== 1) throw new Error(`conv_native.h 的 CF_PW_PX 块命中 ${n} 次（预期 1）`)
      files.set(
        'conv_native.h',
        h.replace(PW_COND_BLOCK, '#define CF_PW_PX 8 /* 变体 pw8：强制 8px */\n'),
      )
    },
  },
  {
    name: 'nog3',
    desc: 'conv3 4oc 分组 → 优化前的逐 oc 实现（其余三项保留）',
    apply(files) {
      const newBlock = icBlock(fnText(convC, 'static void conv3('))
      const oldBlock = icBlock(fnText(gitShow(`${OLD_REF}:${OLD_SRC}`), 'static void conv3('))
      const c = files.get('conv.c')!
      const n = c.split(newBlock).length - 1
      if (n !== 1) throw new Error(`conv.c 的 conv3 ic 块命中 ${n} 次（预期 1）`)
      files.set('conv.c', c.replace(newBlock, oldBlock))
    },
  },
]

// ── 目标选择 ────────────────────────────────────────────────────────────────
const args = process.argv.slice(2)
const defaultIds = ['win32-x64', 'linux-arm64']
const wanted = args.length
  ? NATIVE_TARGETS.filter((t) => args.includes(t.id))
  : NATIVE_TARGETS.filter((t) => defaultIds.includes(t.id))
if (wanted.length !== (args.length || defaultIds.length)) {
  console.error(
    `目标名不认识：${args.join(' ')}（可用：${NATIVE_TARGETS.map((t) => t.id).join(' / ')}）`,
  )
  process.exit(2)
}

const cc = process.env.NN_CC ?? 'clang'

for (const v of variants) {
  const dir = path.join(OUT, v.name)
  fs.mkdirSync(dir, { recursive: true })
  const files = new Map([
    ['conv.c', convC],
    ['conv_native.h', convH],
  ])
  v.apply(files)
  for (const [n, text] of files) fs.writeFileSync(path.join(dir, n), text)
  console.log(`\n[${v.name}] ${v.desc}`)
  for (const t of wanted) {
    const out = path.join(dir, `conv_native-${t.id}.${t.lib.split('.').pop()!}`)
    const cargs = [
      `--target=${t.triple}`,
      ...nativeFlags(t),
      ...nativeLinkArgs(t),
      '-I',
      dir,
      '-o',
      out,
      path.join(dir, 'conv.c'),
    ]
    const r = spawnSync(cc, cargs, { encoding: 'utf8', windowsHide: true })
    if (r.status !== 0 || !fs.existsSync(out)) {
      console.error(`  ✗ ${t.id} 编译失败 rc=${r.status}\n${r.stderr}`)
      process.exit(1)
    }
    const buf = fs.readFileSync(out)
    console.log(
      `  ✓ ${t.id.padEnd(12)} ${path.relative(ROOT, out).replace(/\\/g, '/')}  ` +
        `sha256=${createHash('sha256').update(buf).digest('hex').slice(0, 16)}  ${buf.length}B`,
    )
  }
}

console.log('\n对拍（把变体库拷到目标机器上跑；>1 = 关掉该项后更慢 ⇒ 该项是收益）：')
for (const v of variants)
  console.log(`  CONV_AB_OLD_LIB=<变体库> bun tools/perf/conv-ab.ts 100 4   # ${v.name}：${v.desc}`)
