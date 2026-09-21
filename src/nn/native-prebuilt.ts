/**
 * native-prebuilt.ts —— 随仓库分发的 native features 共享库矩阵（**唯一事实来源**）。
 *
 * ## 为什么要有 prebuilt（2026-09-21 用户提问）
 *
 * rollout/eval 节点是**异质**的（win x64 self / mac / Android-Termux arm64 …），而它们
 * 多数**没有 clang 工具链**：原设计「节点上 `bun tools/agent/native-build.ts` 自己编」
 * 只在训练机成立，节点只会得到一句「编译器不可用」⇒ native 臂在远端永远开不起来。
 * 于是把 6 个目标平台的库**在训练机交叉编译好、随仓库入库**，节点 `git pull` 直接拿到
 * （节点升级本来就是 `git pull` 同一分支，见 docs/goal-nn-handoff.md §4）。
 *
 * ## 三条设计约束
 *
 * ① **免 libc（freestanding）**：交叉编译时手上只有 Windows 的 MSVC 头/库，没有目标平台
 *    的 sysroot —— 只要内核碰 libc 就链不出来（`src/nn/native/conv_feats_native.c` 里
 *    的 `cf_zero`/`cf_copy` 就是这个原因的产物）。反过来这带来一个好处：
 *    产物**零动态依赖**（无 DT_NEEDED / LC_LOAD_DYLIB / 导入表），于是同一份 linux-arm64
 *    库在 glibc / musl / **bionic（Android-Termux）** 上都能 dlopen，不必为 Termux 单独
 *    出一个目标。
 * ② **x64 只到 AVX1**：本机实测（`tmp/…`，x20-clutch.it176，ffi 微基准）
 *    SSE2/SSE4.2 = 3.46–3.53 ms · AVX1 = 2.60–2.64 ms · AVX2 = 2.51–2.59 ms
 *    ⇒ AVX1 已拿到全部收益（约 27%），AVX2 只多 ~2%。而 prebuilt 是**发给别人**用的：
 *    `-mavx2` 的代价是「2013 年前的 x64 CPU 直接 SIGILL」，收益却是噪声级。
 *    故 x64 一律 `-mavx -msse4.2`（Sandy Bridge 2011+），不用 `-march=native`/`-mavx2`。
 * ③ **不做运行期「源码 sha 是否变过」的检查**：那份判断的归宿是仓库侧门禁
 *    （`tests/native-prebuilt.test.ts` + `--check-prebuilt`）——提交里 prebuilt 与源码
 *    必然同源；而**运行期的正确性闸门始终是首用 attestation**（真实权重下 native 与 wasm
 *    逐字节对拍，不过就关 native 并响亮回落，见 src/nn/native-conv.ts）。节点上多读 3 个
 *    源文件哈希换不来更安全的结论，只会把「文件在 bundle 里、源码不在」这类正常情形
 *    误判成不可用。
 *
 * 产物布局（入库，进 codeHash —— `src/nn/` 在 tools/agent/codehash-files.txt 里）：
 *
 *   src/nn/native/prebuilt/<platform>-<arch>/conv_feats_native.{dll,so,dylib}
 *   src/nn/native/prebuilt/manifest.json      # 源码 sha + flags + cc 版本 + 每目标产物 sha
 *
 * 先例：`src/nn/wasm/conv_feats.wasm` 就是入库的构建产物（受跟踪、在 codeHash 集内）。
 *
 * ## 可重现性（实测，避免下一个 agent 重踩）
 *
 * | 目标 | 同一份源码两次 `--cross` 是否逐字节相同 | 原因 |
 * |---|---|---|
 * | linux-x64 / linux-arm64 | ✅ 相同 | ELF 无时间戳/路径字段 |
 * | darwin-x64 / darwin-arm64 | ✅ 相同 | 需显式 `-no_uuid` + `-install_name`（否则 LC_ID_DYLIB 带 pid） |
 * | win32-x64 / win32-arm64 | ❌ 9–13 字节不同 | lld-link 的 `/Brepro` 把 TimeDateStamp 换成**输入（含临时 .o 路径）哈希**，
 *   clang 给 MSVC 目标编译时用的是随机临时对象名，故哈希随构建而变。试过 `-fno-temp-file` 也无效 ⇒
 *   不追这一项：正确性由 attestation 保证，新鲜度由 manifest 里的 sha256 钉住，两者都不靠「重建字节相同」。
 */

/** 链接目标 ABI 族（决定产物文件名与链接参数）。 */
export type NativeLinkKind = 'elf' | 'macho' | 'coff'

/** 一个要分发的目标平台。`id` 用 Node 的口径命名（`process.platform-process.arch`）。 */
export interface NativeTarget {
  id: string
  /** clang `--target=` 三元组。 */
  triple: string
  kind: NativeLinkKind
  /** 产物文件名。 */
  lib: string
  /** 微架构 flags（见文件头 ②；arm64 交给 -O3 自动向量化）。 */
  vector: string[]
}

/** ABI 版本：内核签名/权重布局变更时必须 +1（不符即拒用 native，回落 wasm）。 */
export const NATIVE_ABI = 1

/** prebuilt 根目录（相对仓根，posix）。 */
export const PREBUILT_DIR = 'src/nn/native/prebuilt'
export const PREBUILT_MANIFEST_NAME = 'manifest.json'

/**
 * 分发矩阵。**顺序 = 生成顺序**；`--cross` 全量重建，缺一个即失败（矩阵不许悄悄缩水）。
 */
export const NATIVE_TARGETS: readonly NativeTarget[] = [
  {
    id: 'win32-x64',
    triple: 'x86_64-pc-windows-msvc',
    kind: 'coff',
    lib: 'conv_feats_native.dll',
    vector: ['-mavx', '-msse4.2'],
  },
  {
    id: 'win32-arm64',
    triple: 'aarch64-pc-windows-msvc',
    kind: 'coff',
    lib: 'conv_feats_native.dll',
    vector: [],
  },
  {
    id: 'linux-x64',
    triple: 'x86_64-unknown-linux-gnu',
    kind: 'elf',
    lib: 'conv_feats_native.so',
    vector: ['-mavx', '-msse4.2'],
  },
  {
    id: 'linux-arm64',
    triple: 'aarch64-unknown-linux-gnu',
    kind: 'elf',
    lib: 'conv_feats_native.so',
    vector: [],
  },
  {
    id: 'darwin-x64',
    triple: 'x86_64-apple-darwin',
    kind: 'macho',
    lib: 'conv_feats_native.dylib',
    vector: ['-mavx', '-msse4.2'],
  },
  {
    id: 'darwin-arm64',
    triple: 'arm64-apple-darwin',
    kind: 'macho',
    lib: 'conv_feats_native.dylib',
    vector: [],
  },
] as const

/** 本平台的库文件名（不依赖汇编：这里只按平台命名）。 */
export function nativeLibBasename(platform: string): string {
  return platform === 'win32'
    ? 'conv_feats_native.dll'
    : platform === 'darwin'
      ? 'conv_feats_native.dylib'
      : 'conv_feats_native.so'
}

export function nativeTargetFor(platform: string, arch: string): NativeTarget | null {
  const id = `${platform}-${arch}`
  return NATIVE_TARGETS.find((t) => t.id === id) ?? null
}

/** 某目标的 prebuilt 目录 / 库文件（相对仓根，posix；无该目标返回 ''）。 */
export function prebuiltTargetDir(platform: string, arch: string): string {
  return nativeTargetFor(platform, arch) ? `${PREBUILT_DIR}/${platform}-${arch}` : ''
}

export function prebuiltLibPath(platform: string, arch: string): string {
  const dir = prebuiltTargetDir(platform, arch)
  return dir ? `${dir}/${nativeLibBasename(platform)}` : ''
}

/**
 * 编译 flags（freestanding；全目标同一套，`vector` 除外）。
 *
 * `-ffp-contract=off` / `-fno-fast-math` 是**逐位一致**的必要条件（FMA 收缩会改变结果，
 * 累加顺序没变也救不了）。
 *
 * `forLib=false` = 参考 CLI 用：CLI 需要系统 libc（stdio/malloc）且要正常入口点，故不给
 * `-fvisibility=hidden`（避免把 `main` 藏进静态绑定）与 `-DCF_FREESTANDING=1`（Windows x64
 * 的 `_fltused` 由 CRT 提供；重复定义会撞符号）。
 */
export function nativeFlags(t: NativeTarget, forLib = true): string[] {
  const base = [
    '-O3',
    '-ffp-contract=off',
    '-fno-fast-math',
    '-fno-stack-protector',
    '-ffreestanding',
    '-fno-builtin',
    ...t.vector,
  ]
  return forLib ? [...base, '-fvisibility=hidden', '-DCF_FREESTANDING=1'] : base
}

/**
 * 共享库的链接参数。四条硬要求：
 *   · `-nostdlib`       —— 免 libc（无 DT_NEEDED / 导入表 ⇒ Termux/bionic 也能加载）。
 *   · `--no-undefined`  —— ELF 缺符号直接链接失败，不留下运行期才炸的洞。
 *   · `-Wl,-noentry` / `-Wl,/noimplib` —— Windows 免 CRT 的 DLL 没有 `_DllMainCRTStartup`，
 *     不写入口点（PE 规范允许 AddressOfEntryPoint=0，由加载器跳过 init）。
 *   · `-Wl,-no_uuid`    —— Mach-O 默认塞随机 UUID；去掉才能「重建 = 字节相同」。
 */
export function nativeLinkArgs(t: NativeTarget): string[] {
  switch (t.kind) {
    case 'elf':
      return ['-shared', '-fPIC', '-nostdlib', '-fuse-ld=lld', '-Wl,--no-undefined']
    case 'macho':
      // `-install_name` 必须显式给：不给的话 lld 用**输出文件名**填 LC_ID_DYLIB，
      // 而构建时用的是 `*.tmp-<pid>` 临时名 ⇒ 产物随 pid 变（实测只有 6 个字节不同，
      // 但足以让「重建 = 零 diff」不成立）。`-no_uuid` 同理：lld 默认塞随机 UUID。
      return [
        '-dynamiclib',
        '-fPIC',
        '-nostdlib',
        '-fuse-ld=lld',
        '-Wl,-no_uuid',
        `-Wl,-install_name,@rpath/${t.lib}`,
      ]
    case 'coff':
      // `-Brepro`（lld-link 的 reproducible）杀掉 PE 头里的 TimeDateStamp ——
      // 不写它，同一份源码两次构建产物字节不同，
      // 「重建 prebuilt = 零 diff」这个仓库侧不变量就立不住（实测踩过）。
      return [
        '-shared',
        '-nostdlib',
        '-fuse-ld=lld',
        '-Wl,-noentry',
        '-Wl,/noimplib',
        '-Wl,-Brepro',
      ]
  }
}

/** prebuilt manifest 里的一个目标条目。 */
export interface PrebuiltEntry {
  id: string
  triple: string
  kind: NativeLinkKind
  lib: string
  flags: string[]
  sha256: string
  bytes: number
}

/** `src/nn/native/prebuilt/manifest.json`（入库；仓库侧门禁按它核对产物与源码同源）。 */
export interface PrebuiltManifest {
  abi: number
  cc: string
  ccVersion: string
  sources: Array<{ path: string; sha256: string }>
  targets: PrebuiltEntry[]
  builtAt: string
}

/** 由 manifest 找目标条目（找不到返回 null）。 */
export function prebuiltEntry(
  m: Pick<PrebuiltManifest, 'targets'> | null | undefined,
  platform: string,
  arch: string,
): PrebuiltEntry | null {
  if (!m) return null
  return m.targets.find((t) => t.id === `${platform}-${arch}`) ?? null
}
