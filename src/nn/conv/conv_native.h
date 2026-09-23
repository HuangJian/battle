/* conv_native.h —— 卷积内核的**共享契约**：CF_* 常量 + 权重 blob 顺序 + ABI 版本。
 *
 * 本头是常量与 blob 布局的**唯一事实来源**，native 与 wasm 两个目标共用：
 *   · native：conv.c 的 native 分支 + 对拍 CLI（conv_cli.c）+ 生产共享库；
 *   · wasm32 ：conv_wasm.h（`#include` 本头后只补 wasm 的导出属性）。
 * 两侧的 blob 顺序与 JS 侧（conv_native_adapter.ts::ensureBlob）逐字段一致。
 *
 * 累加顺序契约（native ↔ wasm 逐字节；tests/native-parity.test.ts 钉死）：
 *   pad3 → conv3(stem, 逐 ic 累加) → pad5 → D×(conv5dw → conv1x1_res 4oc×CF_PW_PX,
 *   bias 起按 ic 累加，relu 后加 residual) → GAP（每通道先求和再除 SP）。
 *
 * 单线程契约：内核内部 scratch 为静态区，一个进程一个内核实例（rollout 子进程即此粒度）。
 */
#ifndef CONV_NATIVE_H
#define CONV_NATIVE_H

#define CF_BOARD 26
#define CF_SP (CF_BOARD * CF_BOARD)
#define CF_H 64
#define CF_D 8
#define CF_IN_CH 18
#define CF_STEM_W (CF_IN_CH * 576) /* 18×3×3×64 */
#define CF_DW_W (CF_D * CF_H * 25)
#define CF_PW_W (CF_D * CF_H * CF_H)
/** 权重 blob 的 float 总数（顺序见下）；JS 侧按同一顺序拼接。 */
#define CF_BLOB_FLOATS (CF_STEM_W + CF_H + CF_DW_W + (CF_D * CF_H) + CF_PW_W + (CF_D * CF_H))

/** ABI 版本：内核签名/布局变更时必须 +1（JS 侧不符即拒用 native，回落 wasm）。 */
#define CF_ABI 1

/* pointwise 1×1 的像素平铺宽度（CF_PW_PX；2026-09-22 拍板，见 conv-optimize.plan.md §4.2）。
 *
 * **不要"简化"成单一常量**：
 *   · wasm 只有 16×v128 = 64 个 float 的寄存器容量 ⇒ 16px 需 20 个 v128（16 累加 + 4 输入）
 *     ⇒ **溢写**（实测 locals 373→455）；
 *   · x64 AVX1（16 ymm / 128 float）与 arm64 NEON（32 q / 128 float）都装得下 16px。
 * 实测（单层 iso，AVX1）：4px = 17.5–19.2 GMAC/s · 8px = 31.8–33.0 · 16px = 35.1–37.4；
 * 纯 C 在 wasm32 上 8px 出 56 条向量 FP、16px 出 88 条，但 16px 在 wasm 上溢写。
 * 改成单一常量会让 native 白吐 ~5pp；把 wasm 也设成 16px 会溢写。
 *
 * 为什么不破坏"单源"的实质：CF_PW_PX 只决定「哪些像素被放进同一条向量寄存器」，
 * **不改变任何元素的累加次序**（每元素仍是 bias → ic 升序 → relu → +residual）
 * ⇒ native(16px) 与 wasm(8px) 的输出逐位相同 ⇒ native-parity 契约成立。
 *
 * 余数路径对两档都必须保留：CF_SP = 676，对 8 与 16 的余数**都是 4**
 * （676 = 84×8+4 = 42×16+4）。 */
#if defined(__wasm__) || defined(__wasm32__)
#define CF_PW_PX 8
#else
#define CF_PW_PX 16
#endif

/* 导出宏。wasm 目标不用 __declspec，导出名由 conv_wasm.h 指定。 */
#if defined(_WIN32)
#define CF_EXPORT __declspec(dllexport)
#else
#define CF_EXPORT __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/** ABI 版本（JS 加载后先问一次）。 */
CF_EXPORT unsigned cf_abi(void);

/**
 * Student features 前向（h=64/d=8/board=26/IN_CH=18）。
 *
 * wblob  权重，顺序（f32，连续）：stemW[CF_STEM_W] stemB[CF_H]
 *        dwW[CF_D][CF_H*25] dwB[CF_D][CF_H] pwW[CF_D][CF_H*H] pwB[CF_D][CF_H]
 * in16   输入 18 通道（前 16 = obs，后 2 = coords），长度 CF_IN_CH*CF_SP
 * pooled 输出 [CF_H]；bufA 输出 [CF_H*CF_SP]（GAP 前空间特征，目标热图头唯一消费）
 * 返回 0 成功。
 */
CF_EXPORT int cf_student_features(const float* wblob, const float* in16, float* pooled, float* bufA);

#ifdef __cplusplus
}
#endif

#endif /* CONV_NATIVE_H */
