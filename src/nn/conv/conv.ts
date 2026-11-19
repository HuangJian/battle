/**
 * conv.ts —— 卷积加速后端的**生产咽喉**（`StudentModel.features` 的唯一接入点）。
 *
 * 选择链（rollout 与 eval 都走这里，DECISIONS §311 / rollout-eval-opt.plan.md §4）：
 *   native（共享库 + bun:ffi，仅 bun）→ wasm32 SIMD（prebuilt/wasm/conv.wasm）→ TS（调用方兜底）。
 *
 * 两个后端都在本目录下，且都从**同一份内核源码** `conv.c` 编译：
 *   · ./conv_native_adapter.ts  —— bun:ffi + 首用 attestation（真实权重下 native↔wasm **容差对拍**，
 *                                  不过就本进程关闭并响亮记一行）；
 *   · ./conv_wasm_adapter.ts    —— wasm32 后端（clang --target=wasm32 -msimd128，CF_PW_PX=8）。
 *
 * ⚠ 两侧**不是逐位一致**（2026-11-16 起）：native 用 `-ffp-contract=fast`（x64 加 `-mfma`）换 +8.4%
 * 内核吞吐，FMA 把「乘、加各舍入一次」并成「只舍入一次」⇒ 末位差 ~1 ulp（实测 `bufA` 最大差 6e-5，
 * 该张量 max 152）。契约因此是**容差等价**：逐张量判
 * `max|native−wasm| ≤ ATTEST_TOLERANCE × max(1, max|wasm|)`，`ATTEST_TOLERANCE = 1e-5`
 * （阈值与理由见 `conv_native_adapter.ts` 同名常量）。机械守护在 tests/native-parity.test.ts，
 * 它断言的是**同一容差**、不是逐位 —— 别把这个门读成「两边逐位相同」。
 *
 * ⇒ 必须记住的下游推论：**同一份权重、同一份输入，两次采集的浮点产物本来就不会逐位相同**
 *   （同一局在 native 与 wasm 之间动态选后端，差 1 ulp 落在 f32 张量上）。
 *   任何「A/B 逐位无容差」的判据在这个后端组合下**构造上不可达**，
 *   口径应是「Δ ≤ max(实测噪声底, 2×ulp)」（reports/p2-verdict-c-20261117.md）。
 *
 * native 的准入不是「文件在就上」：首次调用会做 3 次 native↔wasm **容差** attestation
 * （真实权重 + 确定性伪随机输入，pooled 与 bufA 各判一次），不过就关 native 并打一行 warning。
 * 返回 true = pooled+bufA 已填（由 native 或 wasm 之一完成）。
 */
import { noteFeaturesEngine, runStudentConvNative } from './conv_native_adapter'
import { runStudentConvWasm } from './conv_wasm_adapter'

export function runStudentFeatures(model: unknown): boolean {
  if (runStudentConvNative(model, { runWasm: (m) => runStudentConvWasm(m) })) return true
  if (runStudentConvWasm(model)) {
    noteFeaturesEngine('wasm')
    return true
  }
  return false
}

/** TS 特征路径（调用方兜底）实际生效时记账 —— 让「以为开了加速其实在 TS」能被看见。 */
export function noteFeaturesTs(): void {
  noteFeaturesEngine('ts')
}

/* 引擎账本与两个后端的入口：工具/测试/导出器按名字取用（生产路径只需要 runStudentFeatures）。 */
export {
  benchNativeKernel,
  featuresEngine,
  nativeLibCandidates,
  nativeStatus,
  noteFeaturesEngine,
  resetNativeConvForTest,
  runStudentConvNative,
  setNativeConvEnabled,
  type FeaturesEngine,
  type NativeStatus,
} from './conv_native_adapter'
export { runStudentConvWasm, setStudentConvWasmEnabled, studentConvWasm } from './conv_wasm_adapter'
