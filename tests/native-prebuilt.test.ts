/**
 * native-prebuilt.test.ts —— 「随仓库分发的 native 库」门禁（rollout-eval-opt.plan.md T2）。
 *
 * 为什么要有这个测试：节点机器多数**没有 clang**，所以 native 库改成训练机交叉编译后随
 * 仓库分发（`src/nn/native/prebuilt/`，见 src/nn/native-prebuilt.ts）。于是「分发物」本身
 * 成了一个必须被机械核对的契约，分四块：
 *
 *  ① **同源 / 齐全**：manifest 里每个目标的 sha256 与盘上文件一致、ABI 对、源码未变
 *     （这一条 = `--check-prebuilt`，也就是「改了内核忘了重建 prebuilt」的拦网）；
 *  ② **格式与依赖**：每个产物的文件头对得上它的目标（ELF/Mach-O/PE + 架构），且**零 libc/CRT
 *     依赖** —— 这是「同一份 linux-arm64 库在 glibc / musl / bionic(Termux) 上都能 dlopen」
 *     的机械依据；同时断言两个导出符号还在（免得 optimize 掉了导出）。
 *  ③ **解析优先级**：env → prebuilt → 本机构建（与 src/nn/native-conv.ts 一致）。
 *  ④ **跨平台执行证据**：WSL + python3 ctypes **真加载** linux-x64 产物，与 wasm 逐字节比对。
 *     本机没有 mac/arm64 机器 ⇒ 那两个目标只能靠「节点首用 attestation」把关（见 native-conv.ts），
 *     但 linux-x64 这一份可以在这里真跑 —— 它是「prebuilt 不只是编出来了，而是真的算对」
 *     唯一可复现的证据通道。
 *
 * 免责：缺 wsl / python3 / llvm 工具链时，相关用例**响亮跳过**（console.warn），不静默变绿。
 */
import { describe, expect, it } from 'bun:test'
import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
process.chdir(ROOT)

const {
  NATIVE_ABI,
  NATIVE_TARGETS,
  PREBUILT_DIR,
  prebuiltLibPath,
  nativeTargetFor,
  nativeLibBasename,
} = await import('../src/nn/native-prebuilt.ts')
const { prebuiltStaleReason, resolveNativeLib, sha256File } =
  await import('../tools/agent/native-build.ts')
const { runStudentConvWasm } = await import('../src/nn/conv-wasm.ts')

const PREBUILT_DIR_ABS = path.join(ROOT, PREBUILT_DIR)
const BOARD = 26
const SP = BOARD * BOARD
const H = 64
const D = 8
const IN_CH = 18

/* ───────────────────────────── 小工具 ───────────────────────────── */

function u16le(b: Buffer, off: number): number {
  return b.readUInt16LE(off)
}
function u32le(b: Buffer, off: number): number {
  return b.readUInt32LE(off)
}

/** 文件里是否出现这段 ASCII（找不到返回 false；用于「零依赖」与符号存在性检查）。 */
function hasAscii(b: Buffer, s: string): boolean {
  return b.includes(Buffer.from(s, 'ascii'))
}

/** 逻辑目标 → 期望的架构编码（各家 magic 的 machine/cputype 字段）。 */
const ARCH_CODE: Record<string, { elf: number; macho: number; coff: number }> = {
  x64: { elf: 62 /* EM_X86_64 */, macho: 0x01000007 /* CPU_TYPE_X86_64 */, coff: 0x8664 },
  arm64: { elf: 183 /* EM_AARCH64 */, macho: 0x0100000c /* CPU_TYPE_ARM64 */, coff: 0xaa64 },
}

function targetArch(id: string): 'x64' | 'arm64' {
  return id.endsWith('arm64') ? 'arm64' : 'x64'
}

/* ───────────────────────── ① 同源 / 齐全 ───────────────────────── */

describe('prebuilt 分发物（同源/齐全）', () => {
  it('manifest 与盘上产物同源（= bun tools/agent/native-build.ts --check-prebuilt）', () => {
    const why = prebuiltStaleReason(ROOT)
    expect(why).toBe('')
  })

  it('矩阵 6 目标齐全，manifest 记的 sha256 与文件一致、ABI 一致', () => {
    const m = JSON.parse(fs.readFileSync(path.join(PREBUILT_DIR_ABS, 'manifest.json'), 'utf8')) as {
      abi: number
      targets: Array<{ id: string; sha256: string; bytes: number; lib: string }>
    }
    expect(m.abi).toBe(NATIVE_ABI)
    expect(m.targets.map((t) => t.id).sort()).toEqual(NATIVE_TARGETS.map((t) => t.id).sort())
    for (const t of m.targets) {
      const p = path.join(PREBUILT_DIR_ABS, t.id, t.lib)
      expect(fs.existsSync(p)).toBe(true)
      expect(sha256File(p)).toBe(t.sha256)
      expect(fs.statSync(p).size).toBe(t.bytes)
    }
  })
})

/* ───────────────────────── ② 格式与依赖 ───────────────────────── */

describe('prebuilt 产物格式 + 零外部依赖', () => {
  for (const t of NATIVE_TARGETS) {
    it(`${t.id}：文件头/架构/导出符号/无 libc 依赖`, () => {
      const p = path.join(PREBUILT_DIR_ABS, t.id, t.lib)
      expect(fs.existsSync(p), `缺 ${path.relative(ROOT, p)}`).toBe(true)
      const b = fs.readFileSync(p)
      const arch = ARCH_CODE[targetArch(t.id)]!

      if (t.kind === 'elf') {
        expect([b[0], b[1], b[2], b[3]]).toEqual([0x7f, 0x45, 0x4c, 0x46])
        expect(b[4]).toBe(2) // ELFCLASS64
        expect(b[5]).toBe(1) // ELFDATA2LSB
        expect(u16le(b, 16)).toBe(3) // ET_DYN（可 dlopen 的共享对象）
        expect(u16le(b, 18)).toBe(arch.elf)
      } else if (t.kind === 'macho') {
        // little-endian 64-bit Mach-O：magic 为 0xFEEDFACF（文件里是 cf fa ed fe）
        expect(u32le(b, 0)).toBe(0xfeedfacf)
        expect(u32le(b, 4)).toBe(arch.macho)
        expect(u32le(b, 12)).toBe(6) // MH_DYLIB
      } else {
        expect(b.subarray(0, 2).toString('ascii')).toBe('MZ')
        const pe = u32le(b, 0x3c)
        expect(b.subarray(pe, pe + 4).toString('ascii')).toBe('PE\u0000\u0000')
        expect(u16le(b, pe + 4)).toBe(arch.coff)
        expect(u16le(b, pe + 22) & 0x2000).toBe(0x2000) // IMAGE_FILE_DLL
      }

      // 导出两个符号（在 .dynstr / 导出 trie / PE 导出名表里都是明文字节）
      for (const sym of ['cf_abi', 'cf_student_features']) {
        expect(hasAscii(b, sym), `${t.id} 缺导出符号 ${sym}`).toBe(true)
      }
      // 零动态依赖：出现任何一个都说明链进了宿主 libc/CRT（Termux/bionic 上会加载失败）
      for (const marker of [
        'libc.so',
        'ld-linux',
        'libSystem',
        'KERNEL32',
        'api-ms-win',
        'ucrtbase',
        'VCRUNTIME',
      ]) {
        expect(hasAscii(b, marker), `${t.id} 链进了宿主库: ${marker}`).toBe(false)
      }
    })
  }

  const nm =
    spawnSync('llvm-nm', ['--version'], { encoding: 'utf8', windowsHide: true }).status === 0
  const ro =
    spawnSync('llvm-readobj', ['--version'], { encoding: 'utf8', windowsHide: true }).status === 0
  if (!nm) console.warn('[native-prebuilt] 无 llvm-nm → 未定义符号深检跳过（非静默变绿）')
  if (!ro) console.warn('[native-prebuilt] 无 llvm-readobj → DT_NEEDED 深检跳过（非静默变绿）')

  it.skipIf(!nm)('llvm-nm：ELF/Mach-O 产物无未定义符号（-nostdlib 下真无 libcall）', () => {
    for (const t of NATIVE_TARGETS) {
      if (t.kind === 'coff') continue // COFF 走 PE 导出/导入表，不是 nm 的强项
      const p = path.join(PREBUILT_DIR_ABS, t.id, t.lib)
      const r = spawnSync('llvm-nm', ['-u', p], { encoding: 'utf8', windowsHide: true })
      expect(r.status).toBe(0)
      expect(r.stdout.trim(), `${t.id} 有未定义符号:\n${r.stdout}`).toBe('')
    }
  })

  it.skipIf(!ro)('llvm-readobj：ELF 产物 NeededLibraries 为空（无 DT_NEEDED）', () => {
    for (const t of NATIVE_TARGETS) {
      if (t.kind !== 'elf') continue
      const r = spawnSync(
        'llvm-readobj',
        ['--needed-libs', path.join(PREBUILT_DIR_ABS, t.id, t.lib)],
        {
          encoding: 'utf8',
          windowsHide: true,
        },
      )
      expect(r.status).toBe(0)
      // 输出形如 `NeededLibraries [\n]`：有依赖时括号里会多出 `Name: libc.so.6` 之类
      expect(r.stdout).toMatch(/NeededLibraries \[\s*\]/)
    }
  })
})

/* ───────────────────────── ③ 解析优先级 ───────────────────────── */

describe('共享库解析优先级（env → prebuilt → 本机构建）', () => {
  const withEnv = <T>(value: string | undefined, fn: () => T): T => {
    const prev = process.env.NN_NATIVE_LIB
    if (value === undefined) delete process.env.NN_NATIVE_LIB
    else process.env.NN_NATIVE_LIB = value
    try {
      return fn()
    } finally {
      if (prev === undefined) delete process.env.NN_NATIVE_LIB
      else process.env.NN_NATIVE_LIB = prev
    }
  }

  it('本机目标在矩阵内：解析到入库 prebuilt', () => {
    const host = `${process.platform}-${process.arch}`
    const inMatrix = nativeTargetFor(process.platform, process.arch) !== null
    const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'npb-'))
    if (inMatrix) {
      const dir = path.join(repo, PREBUILT_DIR, host)
      fs.mkdirSync(dir, { recursive: true })
      fs.writeFileSync(path.join(dir, nativeLibBasename(process.platform)), 'FAKE-LIB')
    }
    const r = withEnv(undefined, () => resolveNativeLib(repo)) as ReturnType<
      typeof resolveNativeLib
    >
    if (!inMatrix) {
      expect(r.path).toBeNull()
      expect(r.reason).toMatch(/矩阵无/)
    } else {
      expect(r.kind).toBe('prebuilt')
      expect((r.path ?? '').replace(/\\/g, '/')).toContain(PREBUILT_DIR)
    }
    fs.rmSync(repo, { recursive: true, force: true })
  })

  it('NN_NATIVE_LIB 最优先（节点上手工指路）', () => {
    const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'npb-'))
    const fake = path.join(repo, 'hand-placed.bin')
    fs.writeFileSync(fake, 'FAKE-LIB')
    const r = withEnv(fake, () => resolveNativeLib(repo)) as ReturnType<typeof resolveNativeLib>
    expect(r.kind).toBe('env')
    expect(r.path).toBe(fake)
    fs.rmSync(repo, { recursive: true, force: true })
  })

  it('无 prebuilt 时回落本机构建（tmp/native）', () => {
    const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'npb-'))
    const dir = path.join(repo, 'tmp', 'native')
    fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(path.join(dir, nativeLibBasename(process.platform)), 'FAKE-LIB')
    // 仓里没有 prebuilt 文件（矩阵里有没有本机目标都一样）⇒ 落到本机构建
    const r = withEnv(undefined, () => resolveNativeLib(repo)) as ReturnType<
      typeof resolveNativeLib
    >
    expect(r.kind).toBe('local')
    fs.rmSync(repo, { recursive: true, force: true })
  })

  it('既无 prebuilt 也无本机构建 ⇒ 响亮原因（不抛）', () => {
    const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'npb-'))
    const r = withEnv(undefined, () => resolveNativeLib(repo)) as ReturnType<
      typeof resolveNativeLib
    >
    expect(r.path).toBeNull()
    expect(r.reason).toMatch(/prebuilt|矩阵/)
    fs.rmSync(repo, { recursive: true, force: true })
  })

  it('路径映射：prebuiltLibPath / nativeLibBasename 与目标 id 一致', () => {
    expect(prebuiltLibPath('darwin', 'arm64')).toBe(
      `${PREBUILT_DIR}/darwin-arm64/conv_feats_native.dylib`,
    )
    expect(prebuiltLibPath('linux', 'arm64')).toBe(
      `${PREBUILT_DIR}/linux-arm64/conv_feats_native.so`,
    )
    expect(prebuiltLibPath('win32', 'x64')).toBe(`${PREBUILT_DIR}/win32-x64/conv_feats_native.dll`)
    expect(prebuiltLibPath('freebsd', 'x64')).toBe('')
    expect(nativeTargetFor('plan9', 'x64')).toBeNull()
  })
})

/* ───────────────────── ④ 跨平台执行证据（WSL） ───────────────────── */

/** Windows 盘符路径 → WSL /mnt/<drive>/… */
function toWslPath(p: string): string {
  const m = /^([A-Za-z]):[\\/](.*)$/.exec(p)
  if (!m) return p.replace(/\\/g, '/')
  return `/mnt/${m[1]!.toLowerCase()}/${m[2]!.replace(/\\/g, '/')}`
}

function makeRng(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6d2b79f5) >>> 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const WSL_OK =
  process.platform === 'win32' &&
  spawnSync('wsl.exe', ['-e', 'python3', '-c', 'import ctypes'], { timeout: 60_000 }).status === 0
if (!WSL_OK)
  console.warn('[native-prebuilt] 无 WSL+python3 → linux-x64 真机执行证据跳过（非静默变绿）')

describe('linux-x64 prebuilt 真执行（WSL + python3 ctypes）vs wasm 逐字节', () => {
  it.skipIf(!WSL_OK)('WSL 里 dlopen 入库 .so，pooled+bufA 与 wasm 逐字节相同', () => {
    const so = path.join(PREBUILT_DIR_ABS, 'linux-x64', 'conv_feats_native.so')
    expect(fs.existsSync(so)).toBe(true)

    // 确定性输入（LCG，同 native-parity 口径）：in16 + 权重 blob，直接写 float32 小端
    const rng = makeRng(20260921)
    const fill = (n: number, scale = 0.4): Float32Array => {
      const a = new Float32Array(n)
      for (let i = 0; i < n; i++) a[i] = (rng() - 0.5) * scale
      return a
    }
    const in16 = fill(IN_CH * SP, 2)
    const stemW = fill(IN_CH * H * 9)
    const stemB = fill(H)
    const dwW = Array.from({ length: D }, () => fill(H * 25))
    const dwB = Array.from({ length: D }, () => fill(H))
    const pwW = Array.from({ length: D }, () => fill(H * H))
    const pwB = Array.from({ length: D }, () => fill(H))
    const buf = (a: Float32Array): Buffer => Buffer.from(a.buffer, a.byteOffset, a.byteLength)
    const wblob = Buffer.concat([stemW, stemB, ...dwW, ...dwB, ...pwW, ...pwB].map(buf))

    // 参考：wasm（同一进程）
    const model = {
      in16,
      stemW,
      stemB,
      dwW,
      dwB,
      pwW,
      pwB,
      pooled: new Float32Array(H),
      bufA: new Float32Array(H * SP),
    }
    expect(runStudentConvWasm(model)).toBe(true)
    const expectBytes = Buffer.concat([buf(model.pooled), buf(model.bufA)])

    const work = fs.mkdtempSync(path.join(ROOT, 'tmp', 'npb-wsl-'))
    const inFile = path.join(work, 'in.bin')
    const outFile = path.join(work, 'out.bin')
    const pyFile = path.join(work, 'run.py')
    fs.writeFileSync(inFile, Buffer.concat([buf(in16), wblob]))
    fs.writeFileSync(
      pyFile,
      [
        'import ctypes, hashlib, sys',
        'so, fin, fout = sys.argv[1], sys.argv[2], sys.argv[3]',
        'N16, NH, NSP, NCH = 18 * 676, 64, 676, 18',
        'lib = ctypes.CDLL(so)',
        'lib.cf_abi.restype = ctypes.c_uint32',
        'abi = lib.cf_abi()',
        'assert abi == 1, f"abi={abi}"',
        'data = open(fin, "rb").read()',
        'n_in = N16 * 4',
        'in16 = (ctypes.c_float * N16).from_buffer_copy(data[:n_in])',
        'blob = (ctypes.c_float * ((len(data) - n_in) // 4)).from_buffer_copy(data[n_in:])',
        'pooled = (ctypes.c_float * NH)()',
        'bufA = (ctypes.c_float * (64 * NSP))()',
        'P = ctypes.POINTER(ctypes.c_float)',
        'lib.cf_student_features.argtypes = [P, P, P, P]',
        'lib.cf_student_features.restype = ctypes.c_int',
        'rc = lib.cf_student_features(blob, in16, pooled, bufA)',
        'assert rc == 0, f"rc={rc}"',
        'out = bytes(pooled) + bytes(bufA)',
        'open(fout, "wb").write(out)',
        'print("abi=%d bytes=%d sha=%s" % (abi, len(out), hashlib.sha256(out).hexdigest()[:12]))',
      ].join('\n'),
      { encoding: 'utf8' },
    )

    const r = spawnSync(
      'wsl.exe',
      ['-e', 'python3', toWslPath(pyFile), toWslPath(so), toWslPath(inFile), toWslPath(outFile)],
      { encoding: 'utf8', timeout: 120_000 },
    )
    try {
      expect(r.status, `WSL python 失败: ${r.stdout}\n${r.stderr}`).toBe(0)
      console.log(`[native-prebuilt] WSL: ${r.stdout.trim()}`)
      const got = fs.readFileSync(outFile)
      expect(got.length).toBe(expectBytes.length)
      expect(got.equals(expectBytes)).toBe(true)
    } finally {
      // 只清自己的现场（失败也清：内容已在断言里）
      fs.rmSync(work, { recursive: true, force: true })
    }
  })
})
