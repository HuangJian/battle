/**
 * conv.ts —— 卷积加速后端的**生产咽喉**（`StudentModel.features` 的唯一接入点）。
 *
 * 选择链（rollout 与 eval 都走这里，DECISIONS §311 / rollout-eval-opt.plan.md §4）：
 *   native（共享库 + bun:ffi，仅 bun）→ wasm32 SIMD（prebuilt/wasm/conv.wasm）→ TS（调用方兜底）。
 *
 * 两个后端都在本目录下，且都从**同一份内核源码** `conv.c` 编译：
 *   · ./conv_native_adapter.ts  —— bun:ffi + 首用 attestation（真实权重下 native↔wasm 逐字节对拍，
 *                                  不过就本进程关闭并响亮记一行）；
 *   · ./conv_wasm_adapter.ts    —— wasm32 后端（clang --target=wasm32 -msimd128，CF_PW_PX=8）。
 * 两者输出逐位一致由 tests/native-parity.test.ts 机械守护（不是靠"同序"的口头约定）。
 *
 * native 的准入不是「文件在就上」：首次调用会做 3 次 native↔wasm 逐字节 attestation，
 * 不过就关 native 并打一行 warning。返回 true = pooled+bufA 已填（由 native 或 wasm 之一完成）。
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
