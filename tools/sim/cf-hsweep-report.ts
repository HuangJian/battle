/**
 * cf-hsweep-report.ts — §11.8 H 扫描判读（不依赖胜率，便宜且直接）。
 *
 * 输入：export-counterfactual-goals.ts --windows 60,120,240,480 的 shard 目录。
 * 对每档窗口 W，从 cand_s_w{W}.npy 构造软目标 argmax（λ·k 代价同 §9.4.3），
 * 判三个已知更优行为是否被复现：
 *   ① 追尾：argmax 落在敌后格候选（src=ENEMY_REAR）
 *   ② 守家（原型一）：argmax 落在防守锚点候选（src=ANCHOR）
 *   ③ 凿墙近路（原型二）：argmax 落在 carve 候选（k>0）或 brick 对位格（src=BRICK）
 * 另报：argmax=godTarget（保底重合率，过高 = 网络没学到东西的旁证——BC 阶段
 * 用作"标签 ≠ God-AI 选择"的健康检查）。
 *
 * Usage:
 *   bun tools/sim/cf-hsweep-report.ts --data tmp/cf-goals-pilot [--lambda 0.5]
 */
import { readFileSync, existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const UNREACH = 65535

function readNpyF32(path: string): { data: Float32Array; shape: number[] } {
  return readNpy(path) as { data: Float32Array; shape: number[] }
}
function readNpyU(path: string): { data: Uint16Array | Uint8Array; shape: number[] } {
  return readNpy(path) as { data: Uint16Array | Uint8Array; shape: number[] }
}

/** 最小 .npy 读取器（u1/u2/f4，C-order；与 src/nn/npy.ts 的写出端对齐）。 */
function readNpy(path: string): { data: Float32Array | Uint16Array | Uint8Array; shape: number[] } {
  const buf = readFileSync(path)
  // magic \x93NUMPY + version(2) + hdr_len(u2 LE) + header（dict repr）
  const hdrLen = buf[8] | (buf[9] << 8)
  const header = Buffer.from(buf.subarray(10, 10 + hdrLen)).toString('latin1')
  const descr = /'descr': '([^']+)'/.exec(header)![1]
  const shapeM = /'shape': \(([^)]*)\)/.exec(header)![1]
  const shape = shapeM
    .split(',')
    .map((x) => parseInt(x.trim(), 10))
    .filter((x) => !isNaN(x))
  const dataStart = 10 + hdrLen
  let n = 1
  for (const d of shape) n *= d
  let data: Float32Array | Uint16Array | Uint8Array
  if (descr === '<f4') {
    data = new Float32Array(n)
    for (let i = 0; i < n; i++) {
      const o = dataStart + i * 4
      const v = buf[o] | (buf[o + 1] << 8) | (buf[o + 2] << 16) | (buf[o + 3] << 24)
      data[i] = new Float32Array(new Int32Array([v]).buffer)[0]
    }
  } else if (descr === '<u2') {
    data = new Uint16Array(n)
    for (let i = 0; i < n; i++) {
      const o = dataStart + i * 2
      data[i] = buf[o] | (buf[o + 1] << 8)
    }
  } else if (descr.endsWith('u1')) {
    data = new Uint8Array(n)
    data.set(buf.subarray(dataStart, dataStart + n))
  } else {
    throw new Error(`unsupported dtype ${descr} in ${path}`)
  }
  return { data, shape }
}

function main(): void {
  const args = process.argv.slice(2)
  let dataDir = 'tmp/cf-goals-pilot'
  let lambda = 0.5
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--data') dataDir = args[++i]
    else if (args[i] === '--lambda') lambda = parseFloat(args[++i])
  }
  // 两层遍历：根目录或 replanXX 分组子目录下的 cf_s* shard。
  let shardDirs = readdirSync(dataDir).filter((d) => d.startsWith('cf_s'))
  if (shardDirs.length === 0) {
    for (const sub of readdirSync(dataDir)) {
      const subPath = join(dataDir, sub)
      try {
        if (readdirSync(subPath).some((d) => d.startsWith('cf_s'))) {
          for (const d of readdirSync(subPath))
            if (d.startsWith('cf_s')) shardDirs.push(`${sub}/${d}`)
        }
      } catch {
        /* 非目录，跳过 */
      }
    }
  }
  if (shardDirs.length === 0) {
    console.error(`[hsweep] no shards under ${dataDir}`)
    process.exit(2)
  }
  // 从第一个 shard 的 manifest 读窗口档
  const manifest = JSON.parse(
    readFileSync(join(dataDir, shardDirs[0], 'manifest.json'), 'utf8'),
  ) as {
    windows: number[]
    K: number
    firePolicy: string
  }
  const windows = manifest.windows
  const K = manifest.K
  console.log(`[hsweep] shards=${shardDirs.length} windows=${windows.join(',')} K=${K} λ=${lambda}`)

  const stats: Record<
    number,
    {
      rear: number
      anchor: number
      carve: number
      brick: number
      god: number
      cur: number
      n: number
    }
  > = {}
  for (const w of windows)
    stats[w] = { rear: 0, anchor: 0, carve: 0, brick: 0, god: 0, cur: 0, n: 0 }

  for (const sd of shardDirs) {
    const dir = join(dataDir, sd)
    const cellF = readNpyU(join(dir, 'cand_cell.npy'))
    const srcF = readNpyU(join(dir, 'cand_src.npy'))
    const kF = readNpyU(join(dir, 'cand_k.npy'))
    const N = cellF.shape[0]
    for (const w of windows) {
      const sPath = join(dir, `cand_s_w${w}.npy`)
      if (!existsSync(sPath)) continue
      const sF = readNpyF32(sPath)
      for (let i = 0; i < N; i++) {
        // argmax over (s_i − λ·k_i)，padding 排除
        let best = -1
        let bestV = -Infinity
        for (let j = 0; j < K; j++) {
          const cell = cellF.data[i * K + j]
          if (cell === UNREACH) continue
          const k = kF.data[i * K + j]
          const v = sF.data[i * K + j] - lambda * k
          if (v > bestV) {
            bestV = v
            best = j
          }
        }
        if (best < 0) continue
        const st = stats[w]
        st.n++
        const src = srcF.data[i * K + best]
        const bestK = kF.data[i * K + best]
        if (src === 2) st.rear++
        else if (src === 4) st.anchor++
        else if (src === 3) st.brick++
        if (bestK > 0) st.carve++
        if (src === 0) st.god++
        else if (src === 1) st.cur++
      }
    }
  }

  console.log('\n=== §11.8 H 扫描判读（argmax 落点占比，%） ===')
  console.log('window  n      enemyRear  anchor  brick  carve(k>0)  godTarget  current')
  for (const w of windows) {
    const st = stats[w]
    if (st.n === 0) continue
    const pct = (x: number): string => ((100 * x) / st.n).toFixed(1)
    console.log(
      `${String(w).padEnd(7)} ${String(st.n).padEnd(6)} ${pct(st.rear).padEnd(10)} ${pct(st.anchor).padEnd(7)} ${pct(st.brick).padEnd(6)} ${pct(st.carve).padEnd(11)} ${pct(st.god).padEnd(10)} ${pct(st.cur)}`,
    )
  }
  console.log(
    '\n判据（§11.8）：H 多大时 argmax 开始落在 ①敌后格（追尾）②防守锚点（原型一）③carve/brick（原型二）。',
  )
  console.log(
    '注意：①②③是"复现已知更优行为"的存在性指标，占比低 ≠ H 不合适；对照各窗口的相对变化读。',
  )
}

main()
