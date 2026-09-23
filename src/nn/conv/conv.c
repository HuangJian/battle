/* conv.c —— 卷积内核的**唯一源码**，native 与 wasm32 两个目标共用（2026-09-22 单源化）。
 *
 * 单源的前提与代价（决策见 DECISIONS.md「conv 单源 + CF_PW_PX」条）：
 *   · 这里只有**纯 C**，没有 intrinsics：native（conv_native.h）与 wasm（conv_wasm.h）
 *     只是同一份算术的两个代码生成目标；唯一的差异量是目标条件常量 `CF_PW_PX`
 *     （wasm 8 / 其余 16，理由见 conv_native.h），它只决定「哪些像素进同一条向量寄存器」，
 *     **不改变任何元素的累加次序** ⇒ 两目标输出逐位相同（tests/native-parity.test.ts）。
 *   · 2026-09-08 起 wasm 侧曾手写 wasm_simd128 intrinsics（4oc×4px，DECISIONS §311）：
 *     当时纯 C 的 4px 形态在 wasm 后端会被 SROA 成标量（实测 0 条向量指令）。纯 C 加宽到
 *     8px 后 wasm32 出 56 条向量 FP（16px 会溢写），所以才不必再维护第二份实现。
 *
 * 四项循环重排（conv-optimize.plan.md §2；全部**逐位不变** ⇒ 不是 new era）：
 *   ① pad5 只清边框（原实现先 cf_zero(整张 30×30) 再覆盖内部 26×26 ⇒ 每通道白写 1576 float）
 *   ② conv3 stem 4oc 分组 + 权标量预取（9 个输入值被 4 个 oc 共用 ⇒ 每组只载一次输入）
 *   ③ conv5dw 权预取 kw[25]（原实现每像素从 wrow 重载 25 个权标量）
 *   ④ conv1x1_res 像素块 4 → CF_PW_PX（1×1 无 tap ⇒ 一个权广播可喂整个像素块）
 *
 * 免 libc（freestanding）：本内核零外部符号（`-nostdlib` 的硬要求）。
 * 为什么（2026-09-21 交叉编译 prebuilt 库时需要）：节点机器上未必有 clang，于是共享库要
 * **本机交叉编译**后随仓库分发（tools/agent/native-build.ts --cross）。而交叉编译时手上只有
 * Windows 的 MSVC 头/库、没有 Linux/macOS 的 sysroot —— 一旦 include <string.h> 或引用
 * memset/memcpy，链接期就没有可用的实现。padding 的零填与整行拷贝是内核唯一的两个 libc 用途，
 * 本地实现掉即可换来「零未定义符号」的产物：
 *   · ELF/Darwin 由链接期 `--no-undefined` 强制（有 libcall 直接链接失败，不静默）；
 *   · 因此这些产物**不带 DT_NEEDED/LC_LOAD_DYLIB**，glibc/musl/bionic/libSystem
 *     都能 dlopen —— Android/Termux（bionic）也在覆盖内。
 * 语义与 memset/memcpy 逐字节等价（float 按值赋值，无类型双关），故不影响
 * 「native 与 wasm 逐位一致」这一契约。
 *
 * 构建：bun tools/agent/native-build.ts（本机库 + CLI）· --cross（6 目标 prebuilt）
 *       · --wasm（prebuilt/wasm/conv.wasm）。flags 钉死在 native-prebuilt.ts（本目录）。
 */
#if defined(__wasm__) || defined(__wasm32__)
#include "conv_wasm.h"
#define CF_ENTRY_EXPORT CF_KERNEL_EXPORT
#define CF_ABI_ENTRY_EXPORT CF_ABI_EXPORT
#else
#include "conv_native.h"
#define CF_ENTRY_EXPORT CF_EXPORT
#define CF_ABI_ENTRY_EXPORT CF_EXPORT
#endif

typedef float f32;

/* 平铺形状的前提（改网络定义时会真的坏掉的地方，见 conv-optimize.plan.md §4.3）：
 *   · pw 的 oc 分组步长 4 **无余数路径** ⇒ CF_H 必须是 4 的倍数（当前 64 ✓）；
 *   · pw 假定 H×H 方阵（CF_SP 即平铺上界）；
 *   · dw 假定 25 个 tap、conv3 假定 9 个 tap + CF_IN_CH。 */
_Static_assert(CF_H % 4 == 0, "pw 的 4oc 分组没有余数路径：CF_H 必须是 4 的倍数");
_Static_assert(CF_PW_PX == 8 || CF_PW_PX == 16, "CF_PW_PX 只允许 8 / 16（见 conv_native.h）");

#if defined(_WIN32) && defined(CF_FREESTANDING)
/* clang-cl/MSVC x64：**任何**浮点使用都会让编译器引用 `_fltused`（CRT 靠它决定是否
 * 加载浮点支持）。免 CRT 的 DLL 必须自带这个符号，否则 lld-link 报
 * `undefined symbol: _fltused`（arm64 不需要——该引用是 x64 代码生成的产物）。 */
int _fltused = 0x9875;
#endif

static void cf_zero(f32* p, int n) {
  for (int i = 0; i < n; i++) p[i] = 0.f;
}

static void cf_copy(f32* dst, const f32* src, int n) {
  for (int i = 0; i < n; i++) dst[i] = src[i];
}

#define W3 (CF_BOARD + 2) /* 28 */
#define W5 (CF_BOARD + 4) /* 30 */
#define PAD3 (W3 * W3 * CF_IN_CH)
#define PAD5 (W5 * W5 * CF_H)

static f32 scratch[PAD3 + PAD5];
/* bufB 必须与 pad5 分开：conv5dw 每通道「先读满 pad5 的该通道、再写整张 bufB」，
 * 与 pad5 重叠会把尚未读到的通道冲掉（CLI 版用的是独立 malloc）。 */
static f32 bufB_store[CF_H * CF_SP];

static void pad3(const f32* in, int inCh, f32* p) {
  const int w = W3;
  for (int c = 0; c < inCh; c++) {
    const f32* src = in + c * CF_SP;
    f32* dst = p + c * w * w;
    cf_zero(dst, w * w);
    for (int r = 0; r < CF_BOARD; r++) cf_copy(dst + (r + 1) * w + 1, src + r * CF_BOARD, CF_BOARD);
  }
}

/* ① 只清边框：原实现先 cf_zero(整张 30×30) 再 cf_copy(内部 26×26) ⇒ 先把内部写一遍零、
 * 再立刻覆盖。每通道写 1576 float 而最小只需 900（8 个块合计 3.2 MB/forward）。
 * 只动「写哪些字节」，最终内容逐位相同（内部整行原样拷贝）。 */
static void pad5(const f32* in, f32* p) {
  const int w = W5;
  for (int c = 0; c < CF_H; c++) {
    const f32* src = in + c * CF_SP;
    f32* dst = p + c * w * w;
    cf_zero(dst, 2 * w);                      /* 上 2 行 */
    cf_zero(dst + (2 + CF_BOARD) * w, 2 * w); /* 下 2 行 */
    for (int r = 0; r < CF_BOARD; r++) {
      f32* d = dst + (r + 2) * w;
      d[0] = 0.f; /* 左右各 2 列 */
      d[1] = 0.f;
      d[w - 2] = 0.f;
      d[w - 1] = 0.f;
      cf_copy(d + 2, src + r * CF_BOARD, CF_BOARD);
    }
  }
}

/* ② 4oc 分组 + 权标量预取：9 个输入值被 4 个 oc 共用 ⇒ 每组只载一次输入、
 * 36 个权标量进局部数组（wr）。收益小（−2.7~−4.4%）但代价是 arm64 上栈引用增多
 * （普查 18→102）⇒ 若 arm64 实机显示变慢，**只回退这一项**，其余三项保留。
 * 累加序不变：每个输出元素仍是 bias → ic 升序，9 个乘积的联结次序逐字保持。 */
static void conv3(const f32* pad, const f32* w, const f32* b, f32* out, int inCh, int outCh) {
  const int w3 = W3;
  for (int oc = 0; oc < outCh; oc++) {
    f32* o = out + oc * CF_SP;
    const f32 bias = b[oc];
    for (int p = 0; p < CF_SP; p++) o[p] = bias;
  }
  for (int ic = 0; ic < inCh; ic++) {
    const f32* src = pad + ic * w3 * w3;
    for (int oc0 = 0; oc0 < outCh; oc0 += 4) {
      f32 wr[4][9];
      for (int o = 0; o < 4; o++)
        for (int k = 0; k < 9; k++) wr[o][k] = w[((oc0 + o) * inCh + ic) * 9 + k];
      for (int oh = 0; oh < CF_BOARD; oh++) {
        const f32 *r0 = src + oh * w3, *r1 = r0 + w3, *r2 = r1 + w3;
        for (int ow = 0; ow < CF_BOARD; ow++) {
          const f32 x00 = r0[ow], x01 = r0[ow + 1], x02 = r0[ow + 2];
          const f32 x10 = r1[ow], x11 = r1[ow + 1], x12 = r1[ow + 2];
          const f32 x20 = r2[ow], x21 = r2[ow + 1], x22 = r2[ow + 2];
          for (int o = 0; o < 4; o++) {
            const f32* wrr = wr[o];
            f32 acc = 0.f;
            acc += wrr[0] * x00 + wrr[1] * x01 + wrr[2] * x02;
            acc += wrr[3] * x10 + wrr[4] * x11 + wrr[5] * x12;
            acc += wrr[6] * x20 + wrr[7] * x21 + wrr[8] * x22;
            out[(oc0 + o) * CF_SP + oh * CF_BOARD + ow] += acc; /* 累加序不变 */
          }
        }
      }
    }
  }
  for (int oc = 0; oc < outCh; oc++) {
    f32* o = out + oc * CF_SP;
    for (int i = 0; i < CF_SP; i++) o[i] = o[i] < 0 ? 0 : o[i];
  }
}

/* ③ 权预取 kw[25]：原实现每个像素都从 wrow 重载 25 个权标量；预取后载入次数下降，
 * 且各 ISA 方向一致（arm64 普查 mem 77→63、stack 21→8）。纯搬运，结果逐位不变。
 * 注："按输入行分块"（dw_rows2/rows4）与 kh 外提都是**实测负结果**，不要再试
 * （conv-optimize.plan.md §3.1：省的载入会被溢写/串行链吃回去）。 */
static void conv5dw(const f32* pad, const f32* w, const f32* b, f32* out) {
  const int w5 = W5;
  for (int oc = 0; oc < CF_H; oc++) {
    f32* o = out + oc * CF_SP;
    const f32 bias = b[oc];
    for (int p = 0; p < CF_SP; p++) o[p] = bias;
    const f32* src = pad + oc * w5 * w5;
    const f32* wrow = w + oc * 25;
    f32 kw[25];
    for (int k = 0; k < 25; k++) kw[k] = wrow[k];
    for (int oh = 0; oh < CF_BOARD; oh++) {
      for (int ow = 0; ow < CF_BOARD; ow++) {
        f32 acc = 0.f;
        for (int kh = 0; kh < 5; kh++) {
          const f32* r = src + (oh + kh) * w5;
          const f32* kk = kw + kh * 5;
          acc += kk[0] * r[ow + 0] + kk[1] * r[ow + 1] + kk[2] * r[ow + 2] + kk[3] * r[ow + 3] +
                 kk[4] * r[ow + 4];
        }
        o[oh * CF_BOARD + ow] += acc;
      }
    }
  }
  for (int oc = 0; oc < CF_H; oc++) {
    f32* o = out + oc * CF_SP;
    for (int i = 0; i < CF_SP; i++) o[i] = o[i] < 0 ? 0 : o[i];
  }
}

/* ④ pointwise 1×1 + relu + residual，4oc × CF_PW_PX 寄存器分块。
 * 1×1 没有 tap ⇒ 一个权广播可喂整个像素块：块越宽，「权广播摊销到像素」的比例越高，
 * 从 load-port 受限推向 FP 受限（实测 4px→8px：+75% GMAC/s；8px→16px：再 +10~14%）。
 * 累加序与逐元素语义完全不变：bias 起、ic 升序、relu 后 + residual。
 * 余数：CF_SP=676 = 42×16+4 = 84×8+4 ⇒ 两档都有 4 像素尾巴，标量循环兜底。 */
static void conv1x1_res(const f32* in, const f32* w, const f32* b, f32* resid) {
  for (int oc0 = 0; oc0 < CF_H; oc0 += 4) {
    const f32 *w0 = w + (oc0 + 0) * CF_H, *w1 = w + (oc0 + 1) * CF_H;
    const f32 *w2 = w + (oc0 + 2) * CF_H, *w3 = w + (oc0 + 3) * CF_H;
    int p0 = 0;
    for (; p0 + CF_PW_PX <= CF_SP; p0 += CF_PW_PX) {
      f32 a0[CF_PW_PX], a1[CF_PW_PX], a2[CF_PW_PX], a3[CF_PW_PX];
      for (int k = 0; k < CF_PW_PX; k++) {
        a0[k] = b[oc0 + 0];
        a1[k] = b[oc0 + 1];
        a2[k] = b[oc0 + 2];
        a3[k] = b[oc0 + 3];
      }
      for (int ic = 0; ic < CF_H; ic++) {
        const f32* v = in + ic * CF_SP + p0;
        const f32 s0 = w0[ic], s1 = w1[ic], s2 = w2[ic], s3 = w3[ic];
        for (int k = 0; k < CF_PW_PX; k++) {
          a0[k] += s0 * v[k];
          a1[k] += s1 * v[k];
          a2[k] += s2 * v[k];
          a3[k] += s3 * v[k];
        }
      }
      f32 *o0 = resid + (oc0 + 0) * CF_SP + p0, *o1 = resid + (oc0 + 1) * CF_SP + p0;
      f32 *o2 = resid + (oc0 + 2) * CF_SP + p0, *o3 = resid + (oc0 + 3) * CF_SP + p0;
      for (int k = 0; k < CF_PW_PX; k++) {
        const f32 x0 = a0[k], x1 = a1[k], x2 = a2[k], x3 = a3[k];
        o0[k] += x0 > 0 ? x0 : 0;
        o1[k] += x1 > 0 ? x1 : 0;
        o2[k] += x2 > 0 ? x2 : 0;
        o3[k] += x3 > 0 ? x3 : 0;
      }
    }
    for (; p0 < CF_SP; p0++) { /* 余数 4 像素（CF_SP 不是 CF_PW_PX 的倍数） */
      f32 a[4] = {b[oc0 + 0], b[oc0 + 1], b[oc0 + 2], b[oc0 + 3]};
      for (int ic = 0; ic < CF_H; ic++) {
        const f32 v = in[ic * CF_SP + p0];
        a[0] += w0[ic] * v;
        a[1] += w1[ic] * v;
        a[2] += w2[ic] * v;
        a[3] += w3[ic] * v;
      }
      for (int o = 0; o < 4; o++) {
        f32* op = resid + (oc0 + o) * CF_SP + p0;
        *op += a[o] > 0 ? a[o] : 0;
      }
    }
  }
}

CF_ABI_ENTRY_EXPORT unsigned cf_abi(void) { return (unsigned)CF_ABI; }

CF_ENTRY_EXPORT int cf_student_features(const float* wblob, const float* in16, float* pooled,
                                        float* bufA) {
  if (!wblob || !in16 || !pooled || !bufA) return 1;
  const f32* stemW = wblob;
  const f32* stemB = stemW + CF_STEM_W;
  const f32* dwW = stemB + CF_H;
  const f32* dwB = dwW + CF_DW_W;
  const f32* pwW = dwB + CF_D * CF_H;
  const f32* pwB = pwW + CF_PW_W;

  f32* pad3_ = scratch;
  f32* pad5_ = scratch + PAD3;
  f32* bufB = bufB_store;

  pad3(in16, CF_IN_CH, pad3_);
  conv3(pad3_, stemW, stemB, bufA, CF_IN_CH, CF_H);
  for (int i = 0; i < CF_D; i++) {
    pad5(bufA, pad5_);
    conv5dw(pad5_, dwW + i * CF_H * 25, dwB + i * CF_H, bufB);
    conv1x1_res(bufB, pwW + i * CF_H * CF_H, pwB + i * CF_H, bufA);
  }
  for (int c = 0; c < CF_H; c++) {
    const f32* a = bufA + c * CF_SP;
    f32 sum = 0.f;
    for (int i = 0; i < CF_SP; i++) sum += a[i];
    pooled[c] = sum / (f32)CF_SP;
  }
  return 0;
}
