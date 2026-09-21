/* conv_feats_native.h —— native features 内核的 C ABI（生产共享库 + 对拍 CLI **共用同一份**）。
 *
 * 为什么要单独抽一个核心：`src/nn/wasm/conv_feats.c`（wasm SIMD 内核，产品路径字节的
 * 定义者）与 native 内核是**两份独立实现**——wasm 那份用 wasm_simd128 intrinsics，
 * 动它就是动产品字节（= 新 era），不可共享源码。所以 native 侧至少要做到自己内部单源：
 * 共享库与 CLI 都从这里取算法，禁止再复制一份到 CLI。
 *
 * 累加顺序契约（与 wasm `features()` 同序；tests/native-parity.test.ts 逐字节钉死）：
 *   pad3 → conv3(stem, 逐 ic 累加) → pad5 → D×(conv5dw → conv1x1_res 4oc×4px,
 *   bias 起按 ic 累加，relu 后加 residual) → GAP（每通道先求和再除 SP）。
 *
 * 单线程契约：内部 scratch 为静态区，一个进程一个内核实例（rollout 子进程即此粒度）。
 */
#ifndef CONV_FEATS_NATIVE_H
#define CONV_FEATS_NATIVE_H

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

#endif /* CONV_FEATS_NATIVE_H */
