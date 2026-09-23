/* conv_wasm.h —— wasm32 目标的入口视图（单源 conv.c 的 wasm 分支只加这一层）。
 *
 * 与 conv_native.h 的分工：**常量 / 权重 blob 顺序 / CF_ABI 的唯一来源是 conv_native.h**
 * （native 与 wasm 上传的是同一个 blob，JS 侧只有一份拼接代码），本头只补 wasm 特有的两件事：
 *
 *  ① **导出名**：wasm-ld 只导出带 `export_name` 或链接期 `--export=` 的符号。JS 侧按名字取
 *     （`inst.exports['cf_student_features']`），所以入口必须显式标出；
 *  ② 目标条件常量 `CF_PW_PX`：定义在 conv_native.h 的 `#if defined(__wasm32__)` 分支里
 *     （wasm = 8，其余 = 16 —— 理由与实测见那里的注释），本头不重复定义。
 *
 * 编译（见 tools/agent/native-build.ts --wasm；`-ffp-contract=off` 是逐位一致的硬要求）：
 *   clang --target=wasm32 -msimd128 -O3 -ffp-contract=off -fno-fast-math -fno-builtin
 *         -fno-stack-protector -nostdlib -Wl,--no-entry -Wl,--export-memory
 *         -o prebuilt/wasm/conv.wasm conv.c
 */
#ifndef CONV_WASM_H
#define CONV_WASM_H

#include "conv_native.h"

/** 唯一生产入口的 wasm 导出（JS: `inst.exports['cf_student_features']`）。 */
#define CF_KERNEL_EXPORT \
  __attribute__((used, visibility("default"), export_name("cf_student_features")))

/** ABI 探针的 wasm 导出（对齐 native 侧的同名符号，便于两侧用同一套断言）。 */
#define CF_ABI_EXPORT __attribute__((used, visibility("default"), export_name("cf_abi")))

#endif /* CONV_WASM_H */
