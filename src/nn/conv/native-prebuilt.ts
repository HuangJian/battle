/**
 * native-prebuilt.ts —— 随仓库分发的 features 内核产物矩阵（**唯一事实来源**）：
 * 9 个 native 共享库目标（x64 三平台各两条：基线 + `-fma` 变体；arm64 三平台各一条）
 * + 1 个 wasm32 目标（都是同一份 `src/nn/conv/conv.c` 编出来的）。
 *
 * ## 为什么要有 prebuilt（2026-09-21 用户提问）
 *
 * rollout/eval 节点是**异质**的（win x64 self / mac / Android-Termux arm64 …），而它们
 * 多数**没有 clang 工具链**：原设计「节点上 `bun tools/agent/native-build.ts` 自己编」
 * 只在训练机成立，节点只会得到一句「编译器不可用」⇒ native 臂在远端永远开不起来。
 * 于是把 6 个目标平台的库**在训练机交叉编译好、随仓库入库**，节点 `git pull` 直接拿到
 * （节点升级本来就是 `git pull` 同一分支，见 docs/goal-nn-handoff.md §4）。
 *
 * **同源（2026-09-22）**：native 与 wasm 现在是同一份 `src/nn/conv/conv.c` 的两个目标 ——
 * 唯一差异是目标条件常量 `CF_PW_PX`（wasm 8 / native 16，理由与实测见 conv_native.h），
 * 它不动每元素的累加次序 ⇒ 两侧输出逐位相同（tests/native-parity.test.ts）。
 * 因此 wasm 产物也进本矩阵/ manifest：**「改了 conv.c 忘了重编哪个目标」由唯一的门禁
 * `--check-prebuilt` 一次抓完**（此前 wasm 漏编只能靠人记得，是仓库记录过的最危险失败模式）。
 *
 * ## 三条设计约束
 *
 * ① **免 libc（freestanding）**：交叉编译时手上只有 Windows 的 MSVC 头/库，没有目标平台
 *    的 sysroot —— 只要内核碰 libc 就链不出来（`src/nn/conv/conv.c` 里
 *    的 `cf_zero`/`cf_copy` 就是这个原因的产物）。反过来这带来一个好处：
 *    产物**零动态依赖**（无 DT_NEEDED / LC_LOAD_DYLIB / 导入表），于是同一份 linux-arm64
 *    库在 glibc / musl / **bionic（Android-Termux）** 上都能 dlopen，不必为 Termux 单独
 *    出一个目标。
 * ② **x64 到 AVX1+FMA**：本机实测（`tmp/…`，x20-clutch.it176，ffi 微基准）
 *    SSE2/SSE4.2 = 3.46–3.53 ms · AVX1 = 2.60–2.64 ms · AVX2 = 2.51–2.59 ms
 *    ⇒ AVX1 已拿到全部收益（约 27%），AVX2 只多 ~2%。而 prebuilt 是**发给别人**用的：
 *    `-mavx2` 的代价是「2013 年前的 x64 CPU 直接 SIGILL」，收益却是噪声级。
 *    **FMA（2026-11-16，用户「训练提速都要」）**：`-mfma -ffp-contract=fast` 实测 0.9228×
 *    时间（+8.4% 吞吐，100 轮成对；`vmulps 151→35 / vaddps 158→42 / vfmadd 0→166`）。
 *    代价：`-mfma` 要求 **Haswell（2013）**，而基线只要 Sandy Bridge（2011）——更老的 CPU 首次
 *    调用就 SIGILL（硬异常拦不住，见 conv_native_adapter.ts 头注 ①b）。**所以 x64 发两条产物**
 *    （2026-11-16 用户提问）：`<platform>-x64` 不带 `-mfma` 的基线与 `<platform>-x64-fma` 变体，
 *    运行时由 `native-variant.ts` 探一下本人机器能不能跑（探测不过就整条用基线），既不抬基线
 *    也不白丢 8.4%。AVX2 仍不用（噪声级收益 vs 更大基线）。arm64 不需要 `-mfma`（FMA 属基础
 *    ISA），只靠 `-ffp-contract=fast` 就会自动融合，所以只有一条。
 * ③ **不做运行期「源码 sha 是否变过」的检查**：那份判断的归宿是仓库侧门禁
 *    （`tests/native-prebuilt.test.ts` + `--check-prebuilt`）——提交里 prebuilt 与源码 * 必然同源；而**运行期的正确性闸门始终是首用 attestation**（真实权重下 native 与 wasm
 *    容差对拍，不过就关 native 并响亮回落，见 src/nn/conv/conv_native_adapter.ts）。节点上多读 3 个
 *    源文件哈希换不来更安全的结论，只会把「文件在 bundle 里、源码不在」这类正常情形
 *    误判成不可用。
 *
 * 产物布局（入库，进 codeHash —— `src/nn/` 在 tools/agent/codehash-files.txt 里）：
 *
 *   src/nn/conv/prebuilt/<platform>-<arch>/conv_native.{dll,so,dylib}
 *   src/nn/conv/prebuilt/<platform>-x64-fma/conv_native.{dll,so,dylib}   # x64 的融合变体
 *   src/nn/conv/prebuilt/wasm/conv.wasm       # wasm32 目标（JS 生产路径的字节定义者）
 *   src/nn/conv/prebuilt/manifest.json        # 源码 sha + flags + cc 版本 + 每目标产物 sha
 *
 * 先例：`src/nn/wasm/conv_feats.wasm`（今 `prebuilt/wasm/conv.wasm`）就是入库的构建产物
 * （受跟踪、在 codeHash 集内）。
 *
 * ## 可重现性（实测，避免下一个 agent 重踩）
 *
 * | 目标 | 同一份源码两次 `--cross` 是否逐字节相同 | 原因 |
 * |---|---|---|
 * | linux-x64 / linux-arm64 | ✅ 相同 | ELF 无时间戳/路径字段 |
 * | darwin-x64 / darwin-arm64 | ✅ 相同 | 需显式 `-no_uuid` + `-install_name`（否则 LC_ID_DYLIB 带 pid） |
 * | wasm32 | ✅ 相同 | **必须**带 `-Wl,--strip-all`（见 `wasmBuildFlags`）：否则 `name` 段的 module name
 *   就是输出文件名，而构建写的是 `${out}.tmp-<pid>` ⇒ 每次重建字节都不同（2026-09-23 实测） |
 * | win32-x64 / win32-arm64 | ❌ 9–13 字节不同 | lld-link 的 `/Brepro` 把 TimeDateStamp 换成**输入（含临时 .o 路径）哈希**，
 *   clang 给 MSVC 目标编译时用的是随机临时对象名，故哈希随构建而变。试过 `-fno-temp-file` 也无效 ⇒
 *   不追这一项：正确性由 attestation 保证，新鲜度由 manifest 里的 sha256 钉住，两者都不靠「重建字节相同」。
 */

/** 链接目标 ABI 族（决定产物文件名与链接参数）。 */
export type NativeLinkKind = 'elf' | 'macho' | 'coff'

/**
 * 目标变体：`baseline` = 宽 CPU 基线（x64 只要 AVX1，Sandy Bridge 2011+）；
 * `fma` = 同平台加 `-mfma` 的乘加融合版本（x64 需 Haswell 2013+）。
 *
 * 为什么要有两个（2026-11-16 用户提问「不支持 FMA 的能不能用无 FMA 的那份」）：FMA 只值
 * +8.4% 内核吞吐，**不值得**把整个仓库的节点基线抬到 Haswell ⇒ 两个产物都发，运行时探测后挑。
 * 探针与偏好顺序见 `native-variant.ts`。
 */
export type NativeVariant = 'baseline' | 'fma'

/** 一个要分发的目标平台。`id` 用 Node 的口径命名（`process.platform-process.arch`）。
 *  变体目标多一个 `-fma` 后缀（`win32-x64-fma`）⇒ 目录/ manifest 键天然区分。 */
export interface NativeTarget {
  id: string
  /** clang `--target=` 三元组。 */
  triple: string
  kind: NativeLinkKind
  /** 产物文件名。 */
  lib: string
  /** 微架构 flags（见文件头 ②；arm64 交给 -O3 自动向量化，融合由 `-ffp-contract=fast` 提供）。 */
  vector: string[]
  /** 缺省 = 基线；`'fma'` = 该平台的乘加融合变体（更窄的 CPU 基线）。 */
  variant?: 'fma'
}

/** ABI 版本：内核签名/权重布局变更时必须 +1（不符即拒用 native，回落 wasm）。 */
export const NATIVE_ABI = 2

/** prebuilt 根目录（相对仓根，posix）。 */
export const PREBUILT_DIR = 'src/nn/conv/prebuilt'
export const PREBUILT_MANIFEST_NAME = 'manifest.json'

/** wasm32 目标（与 9 个 native 目标同一份 conv.c，只是编到另一个目标）。 */
export const WASM_TARGET = {
  id: 'wasm32',
  /** prebuilt 下的子目录名。 */
  dir: 'wasm',
  lib: 'conv.wasm',
} as const

/**
 * wasm 编译/链接 flags（**唯一来源**：native-build.ts --wasm 与测试共用）。
 *
 * `-ffp-contract=off` 在 wasm 上仍是硬要求：wasm 指令集本身没有 FMA，这条把「wasm 产物字节
 * 不随编译器版本漂」变成**结构性事实**（哪天 wasm 加了乘加融合指令，缺这条就会静默改变
 * 产品字节）。注意它也是 **native 与 wasm 不再逐位相同**的原因：native 侧 2026-11-16 起用
 * `fast` 换 FMA（+8.4%），两侧只在 1e-6 量级上对得上，闸门是容差对拍而非逐字节。
 * `-nostdlib` + `--no-entry` = 无 libc、无入口点，默认导出（默认名
 * `features`）由 conv_wasm.h 的 `export_name` 改成 `cf_student_features`。
 * `--export-memory` 必须留：JS 侧要在同一块线性内存里放权重/输入/输出。
 *
 * `--strip-all` = **可重现性**（2026-09-23 实测发现）：wasm-ld 会把 `name` 自定义段的
 * **module name 写成输出文件名**，而构建为了原子落盘用的是 `${out}.tmp-<pid>` 临时名 ⇒
 * 每次重建的字节都不同（实测同一份源码连编三次三个 sha；直接 clang 对比确认差异就在文件尾
 * 的 `name` 段，内容就是 `w-a.wasm`/`w-b.wasm` 这种输出名）。`--name=` 这个参数 wasm-ld
 * 不认，所以用 `--strip-all` 把 `name` 段整体去掉：实测「换输出名 → 字节相同」，同时产物
 * 小 270B，导出表（`cf_abi`/`cf_student_features`/`memory`）不受影响（都在 export 段）。
 * 代价：wasm 栈追踪少函数名（内核 7KB，需要时本地去掉这个 flag 重编即可）。
 */
export function wasmBuildFlags(): string[] {
  return [
    '--target=wasm32',
    '-msimd128',
    '-O3',
    '-ffp-contract=off',
    '-fno-fast-math',
    '-fno-builtin',
    '-fno-stack-protector',
    '-nostdlib',
    '-Wl,--no-entry',
    '-Wl,--export-memory',
    '-Wl,--strip-all',
  ]
}

/**
 * 分发矩阵。**顺序 = 生成顺序**；`--cross` 全量重建，缺一个即失败（矩阵不许悄悄缩水）。
 *
 * x64 每个平台两条：基线（`-mavx -msse4.2`，Sandy Bridge 2011+）与 `-fma` 变体
 * （`+ -mfma`，Haswell 2013+）。arm64 只有一条：FMA 属基础 ISA，`-ffp-contract=fast` 一条 flag
 * 就融合（实测 `fmla 0→60`），没有「不支持 FMA 的 arm64」可兼容。
 */
export const NATIVE_TARGETS: readonly NativeTarget[] = [
  {
    id: 'win32-x64',
    triple: 'x86_64-pc-windows-msvc',
    kind: 'coff',
    lib: 'conv_native.dll',
    vector: ['-mavx', '-msse4.2'],
  },
  {
    id: 'win32-x64-fma',
    triple: 'x86_64-pc-windows-msvc',
    kind: 'coff',
    lib: 'conv_native.dll',
    vector: ['-mavx', '-msse4.2', '-mfma'],
    variant: 'fma',
  },
  {
    id: 'win32-arm64',
    triple: 'aarch64-pc-windows-msvc',
    kind: 'coff',
    lib: 'conv_native.dll',
    vector: [],
  },
  {
    id: 'linux-x64',
    triple: 'x86_64-unknown-linux-gnu',
    kind: 'elf',
    lib: 'conv_native.so',
    vector: ['-mavx', '-msse4.2'],
  },
  {
    id: 'linux-x64-fma',
    triple: 'x86_64-unknown-linux-gnu',
    kind: 'elf',
    lib: 'conv_native.so',
    vector: ['-mavx', '-msse4.2', '-mfma'],
    variant: 'fma',
  },
  {
    id: 'linux-arm64',
    triple: 'aarch64-unknown-linux-gnu',
    kind: 'elf',
    lib: 'conv_native.so',
    vector: [],
  },
  {
    id: 'darwin-x64',
    triple: 'x86_64-apple-darwin',
    kind: 'macho',
    lib: 'conv_native.dylib',
    vector: ['-mavx', '-msse4.2'],
  },
  {
    id: 'darwin-x64-fma',
    triple: 'x86_64-apple-darwin',
    kind: 'macho',
    lib: 'conv_native.dylib',
    vector: ['-mavx', '-msse4.2', '-mfma'],
    variant: 'fma',
  },
  {
    id: 'darwin-arm64',
    triple: 'arm64-apple-darwin',
    kind: 'macho',
    lib: 'conv_native.dylib',
    vector: [],
  },
] as const

/** 本平台的库文件名（不依赖汇编：这里只按平台命名）。 */
export function nativeLibBasename(platform: string): string {
  return platform === 'win32'
    ? 'conv_native.dll'
    : platform === 'darwin'
      ? 'conv_native.dylib'
      : 'conv_native.so'
}

/** 该平台的目标（缺省 = 基线；`variant='fma'` 时返回变体，矩阵里没有返回 null）。 */
export function nativeTargetFor(
  platform: string,
  arch: string,
  variant: NativeVariant = 'baseline',
): NativeTarget | null {
  const id = variant === 'fma' ? `${platform}-${arch}-fma` : `${platform}-${arch}`
  return NATIVE_TARGETS.find((t) => t.id === id) ?? null
}

/**
 * 同平台的全部目标，**基线在前**（矩阵顺序）。x64 三个平台是 `[基线, fma]`；
 * arm64 只有一个元素。运行时偏好顺序由 `native-variant.ts` 决定（fma 探测通过就反过来）。
 */
export function nativeTargetVariants(platform: string, arch: string): NativeTarget[] {
  const base = `${platform}-${arch}`
  return NATIVE_TARGETS.filter((t) => t.id === base || t.id === `${base}-fma`)
}

/** 某目标的 prebuilt 目录 / 库文件（相对仓根，posix；无该目标返回 ''）。 */
export function prebuiltTargetDir(
  platform: string,
  arch: string,
  variant: NativeVariant = 'baseline',
): string {
  const t = nativeTargetFor(platform, arch, variant)
  return t ? `${PREBUILT_DIR}/${t.id}` : ''
}

export function prebuiltLibPath(
  platform: string,
  arch: string,
  variant: NativeVariant = 'baseline',
): string {
  const dir = prebuiltTargetDir(platform, arch, variant)
  return dir ? `${dir}/${nativeLibBasename(platform)}` : ''
}

/**
 * 编译 flags（freestanding；全目标同一套，`vector` 除外）。
 *
 * `-fno-fast-math` 仍是硬要求（重结合/近似除法会真的动累加次序）；而 `-ffp-contract`
 * **2026-11-16 起改成 `fast`**：乘加融合只把「mul+add 两次舍入」变成「一次舍入」，误差量级
 * 1e-7（实测 bufA 66.7% 元素变、max 1.5e-6），换来 +8.4% 内核吞吐。代价是 native 与 wasm
 * **不再逐位相同**（wasm 仍 `off`，指令集无 FMA）⇒ 逐位门改成容差门，见
 * `conv_native_adapter.ts` 的 `ATTEST_TOLERANCE` 与 `tests/native-parity.test.ts`。
 *
 * `forLib=false` = 参考 CLI 用：CLI 需要系统 libc（stdio/malloc）且要正常入口点，故不给
 * `-fvisibility=hidden`（避免把 `main` 藏进静态绑定）与 `-DCF_FREESTANDING=1`（Windows x64
 * 的 `_fltused` 由 CRT 提供；重复定义会撞符号）。
 */
export function nativeFlags(t: NativeTarget, forLib = true): string[] {
  const base = [
    '-O3',
    '-ffp-contract=fast',
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

/**
 * 只影响「重建可复现性」、不影响链接语义的参数子集（给**参考 CLI** 用）。
 *
 * CLI 链系统 libc（stdio/malloc），用不了 `nativeLinkArgs` 那套 freestanding 参数
 * （`-nostdlib` / `-noentry` 会直接编不出来），于是它一直**漏掉**了可复现性开关：
 * 2026-11-17 实测，同一份源码间隔 3 秒编两次，共享库已逐字节相同、**CLI 仍不同**
 * —— 差在 PE 头的 TimeDateStamp（`-Brepro` 才能钉住）。
 *
 * 所以把这三行拆出来给 CLI 复用，别再把「库的可复现」当成「整个构建的可复现」。
 */
export function reproducibleLinkArgs(t: NativeTarget): string[] {
  switch (t.kind) {
    case 'coff':
      // lld-link 的 reproducible：PE TimeDateStamp 写 0 而不是当前时间
      return ['-Wl,-Brepro']
    case 'macho':
      // lld 默认给可执行文件塞随机 LC_UUID
      return ['-Wl,-no_uuid']
    case 'elf':
      // ELF 默认不带时间戳；`--build-id` 也是内容哈希，天然可复现
      return []
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

/** wasm32 产物条目（同属 prebuilt 矩阵；门禁与 native 目标同规核对）。 */
export interface PrebuiltWasmEntry {
  id: string
  lib: string
  flags: string[]
  sha256: string
  bytes: number
}

/** `src/nn/conv/prebuilt/manifest.json`（入库；仓库侧门禁按它核对产物与源码同源）。 */
export interface PrebuiltManifest {
  abi: number
  cc: string
  ccVersion: string
  /** 源码条目：新格式只有 `digest`（剥注释摘要）；旧格式只有 `sha256`（原始字节）。 */
  sources: Array<{ path: string; digest?: string; sha256?: string }>
  targets: PrebuiltEntry[]
  /** wasm32 产物（与 6 个 native 目标**同一门禁**：改了 conv.c 就必须重编）。
   *  缺这个键 = 上一次构建没编成 wasm ⇒ `--check-prebuilt` 会响亮报「缺 wasm32 产物」。 */
  wasm?: PrebuiltWasmEntry
  /** 摘要口径（`tools/agent/native-build.ts` 的 `SOURCE_DIGEST_SCHEME`）。
   *  缺 = 旧 manifest，门禁按原始 sha256 判过期（改注释会误报，重建一次即迁移）。 */
  sourceDigest?: string
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
