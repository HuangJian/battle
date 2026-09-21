/* conv_feats_native_main.c —— 与 src/nn/wasm/conv_feats.c 同算法的原生基准入口。
 * 不链接 JS；随机权重/输入，只测 features 计算墙钟。
 *
 * 构建（Windows x64，仓根）:
 *   clang -O3 -mavx2 -msse4.2 -o tmp/conv_native.exe src/nn/native/conv_feats_native_main.c
 * Linux/macOS/Termux:
 *   clang -O3 -march=native -o tmp/conv_native src/nn/native/conv_feats_native_main.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <time.h>
#endif

typedef float f32;
#define B 26
#define SP (B * B)
#define H 64
#define D 8
#define IN_CH 18
#define PAD3 (28 * 28 * IN_CH)
#define PAD5 (30 * 30 * H)

static f32 scratch[PAD3 + PAD5] __attribute__((aligned(64)));

static inline void pad3(const f32* in, int inCh, f32* p) {
  const int w = B + 2;
  for (int c = 0; c < inCh; c++) {
    const f32* src = in + c * SP;
    f32* dst = p + c * w * w;
    memset(dst, 0, (size_t)w * w * sizeof(f32));
    for (int r = 0; r < B; r++) {
      f32* drow = dst + (r + 1) * w + 1;
      memcpy(drow, src + r * B, (size_t)B * sizeof(f32));
    }
  }
}
static inline void pad5(const f32* in, f32* p) {
  const int w = B + 4;
  for (int c = 0; c < H; c++) {
    const f32* src = in + c * SP;
    f32* dst = p + c * w * w;
    memset(dst, 0, (size_t)w * w * sizeof(f32));
    for (int r = 0; r < B; r++) {
      memcpy(dst + (r + 2) * w + 2, src + r * B, (size_t)B * sizeof(f32));
    }
  }
}
static inline void relu_(f32* a, int n) {
  for (int i = 0; i < n; i++)
    if (a[i] < 0) a[i] = 0;
}

/* 与 wasm conv_feats.c conv3 相同累加顺序（外积 oc×ic×像素）— 利于自动向量化。 */
static void conv3(const f32* pad, const f32* w, const f32* b, f32* out, int inCh, int outCh) {
  const int w3 = B + 2;
  for (int oc = 0; oc < outCh; oc++) {
    f32* o = out + oc * SP;
    const f32 bias = b[oc];
    for (int p = 0; p < SP; p++) o[p] = bias;
  }
  for (int ic = 0; ic < inCh; ic++) {
    const f32* src = pad + ic * w3 * w3;
    for (int oc = 0; oc < outCh; oc++) {
      const f32* wrow = w + (oc * inCh + ic) * 9;
      f32* o = out + oc * SP;
      for (int oh = 0; oh < B; oh++) {
        const f32* r0 = src + oh * w3;
        const f32* r1 = r0 + w3;
        const f32* r2 = r1 + w3;
        for (int ow = 0; ow < B; ow++) {
          f32 acc = 0.f;
          acc += wrow[0] * r0[ow] + wrow[1] * r0[ow + 1] + wrow[2] * r0[ow + 2];
          acc += wrow[3] * r1[ow] + wrow[4] * r1[ow + 1] + wrow[5] * r1[ow + 2];
          acc += wrow[6] * r2[ow] + wrow[7] * r2[ow + 1] + wrow[8] * r2[ow + 2];
          o[oh * B + ow] += acc;
        }
      }
    }
  }
  for (int oc = 0; oc < outCh; oc++) relu_(out + oc * SP, SP);
}

static void conv5dw(const f32* pad, const f32* w, const f32* b, f32* out) {
  const int w5 = B + 4;
  for (int oc = 0; oc < H; oc++) {
    f32* o = out + oc * SP;
    const f32 bias = b[oc];
    for (int p = 0; p < SP; p++) o[p] = bias;
    const f32* src = pad + oc * w5 * w5;
    const f32* wrow = w + oc * 25;
    for (int oh = 0; oh < B; oh++) {
      for (int ow = 0; ow < B; ow++) {
        f32 acc = 0.f;
        for (int kh = 0; kh < 5; kh++) {
          const f32* r = src + (oh + kh) * w5;
          acc += wrow[kh * 5 + 0] * r[ow + 0] + wrow[kh * 5 + 1] * r[ow + 1] +
                 wrow[kh * 5 + 2] * r[ow + 2] + wrow[kh * 5 + 3] * r[ow + 3] +
                 wrow[kh * 5 + 4] * r[ow + 4];
        }
        o[oh * B + ow] += acc;
      }
    }
  }
  for (int oc = 0; oc < H; oc++) relu_(out + oc * SP, SP);
}

/* pointwise：与 wasm 相同「4oc×4px，从 bias 起按 ic 累加，relu 后再加 residual」。
 * 本文件用标量/编译器自动向量化；native AVX2 下通常仍快于 wasm SIMD。 */
static void conv1x1_res(const f32* in, const f32* w, const f32* b, f32* resid) {
  for (int oc0 = 0; oc0 < H; oc0 += 4) {
    for (int p0 = 0; p0 < SP; p0 += 4) {
      f32 a0[4], a1[4], a2[4], a3[4];
      for (int k = 0; k < 4; k++) {
        a0[k] = b[oc0 + 0];
        a1[k] = b[oc0 + 1];
        a2[k] = b[oc0 + 2];
        a3[k] = b[oc0 + 3];
      }
      const f32* w0 = w + (oc0 + 0) * H;
      const f32* w1 = w + (oc0 + 1) * H;
      const f32* w2 = w + (oc0 + 2) * H;
      const f32* w3 = w + (oc0 + 3) * H;
      for (int ic = 0; ic < H; ic++) {
        const f32* v = in + ic * SP + p0;
        const f32 s0 = w0[ic], s1 = w1[ic], s2 = w2[ic], s3 = w3[ic];
        for (int k = 0; k < 4; k++) {
          a0[k] += s0 * v[k];
          a1[k] += s1 * v[k];
          a2[k] += s2 * v[k];
          a3[k] += s3 * v[k];
        }
      }
      f32* o0 = resid + (oc0 + 0) * SP + p0;
      f32* o1 = resid + (oc0 + 1) * SP + p0;
      f32* o2 = resid + (oc0 + 2) * SP + p0;
      f32* o3 = resid + (oc0 + 3) * SP + p0;
      for (int k = 0; k < 4; k++) {
        o0[k] += a0[k] > 0 ? a0[k] : 0;
        o1[k] += a1[k] > 0 ? a1[k] : 0;
        o2[k] += a2[k] > 0 ? a2[k] : 0;
        o3[k] += a3[k] > 0 ? a3[k] : 0;
      }
    }
  }
}

static void features(const f32* in16, const f32* stemW, const f32* stemB, const f32* dwW,
                     const f32* dwB, const f32* pwW, const f32* pwB, f32* bufA, f32* bufB,
                     f32* bufC, f32* pooled) {
  (void)bufC;
  f32* pad3_ = scratch;
  f32* pad5_ = scratch + PAD3;
  pad3(in16, IN_CH, pad3_);
  conv3(pad3_, stemW, stemB, bufA, IN_CH, H);
  for (int i = 0; i < D; i++) {
    pad5(bufA, pad5_);
    conv5dw(pad5_, dwW + i * H * 25, dwB + i * H, bufB);
    conv1x1_res(bufB, pwW + i * H * H, pwB + i * H, bufA);
  }
  for (int c = 0; c < H; c++) {
    const f32* a = bufA + c * SP;
    f32 sum = 0.f;
    for (int i = 0; i < SP; i++) sum += a[i];
    pooled[c] = sum / (f32)SP;
  }
}

static double now_ms(void) {
#ifdef _WIN32
  LARGE_INTEGER f, c;
  QueryPerformanceFrequency(&f);
  QueryPerformanceCounter(&c);
  return 1000.0 * (double)c.QuadPart / (double)f.QuadPart;
#else
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1e6;
#endif
}

#define ALLOC(n) malloc(n)

int main(int argc, char** argv) {
  int iters = argc > 1 ? atoi(argv[1]) : 2000;
  int warmup = argc > 2 ? atoi(argv[2]) : 80;

  size_t n_in = (size_t)IN_CH * SP;
  size_t n_stem_w = (size_t)IN_CH * 576;
  size_t n_dw = (size_t)D * H * 25;
  size_t n_pw = (size_t)D * H * H;
  f32* in16 = (f32*)ALLOC(n_in * 4);
  f32* stemW = (f32*)ALLOC(n_stem_w * 4);
  f32* stemB = (f32*)ALLOC(H * 4);
  f32* dwW = (f32*)ALLOC(n_dw * 4);
  f32* dwB = (f32*)ALLOC((size_t)D * H * 4);
  f32* pwW = (f32*)ALLOC(n_pw * 4);
  f32* pwB = (f32*)ALLOC((size_t)D * H * 4);
  f32* bufA = (f32*)ALLOC((size_t)H * SP * 4);
  f32* bufB = (f32*)ALLOC((size_t)H * SP * 4);
  f32* bufC = (f32*)ALLOC((size_t)H * SP * 4);
  f32* pooled = (f32*)ALLOC((size_t)H * 4);

  unsigned s = 20260921u;
  for (size_t i = 0; i < n_in; i++) { s = s * 1664525u + 1013904223u; in16[i] = (f32)(s >> 8) / (f32)0x7fffff - 1.f; }
  for (size_t i = 0; i < n_stem_w; i++) { s = s * 1664525u + 1013904223u; stemW[i] = ((f32)(s >> 8) / (f32)0x7fffff - 1.f) * 0.1f; }
  for (int i = 0; i < H; i++) stemB[i] = 0.f;
  for (size_t i = 0; i < n_dw; i++) { s = s * 1664525u + 1013904223u; dwW[i] = ((f32)(s >> 8) / (f32)0x7fffff - 1.f) * 0.1f; }
  for (size_t i = 0; i < (size_t)D * H; i++) dwB[i] = 0.f;
  for (size_t i = 0; i < n_pw; i++) { s = s * 1664525u + 1013904223u; pwW[i] = ((f32)(s >> 8) / (f32)0x7fffff - 1.f) * 0.1f; }
  for (size_t i = 0; i < (size_t)D * H; i++) pwB[i] = 0.f;

  for (int i = 0; i < warmup; i++)
    features(in16, stemW, stemB, dwW, dwB, pwW, pwB, bufA, bufB, bufC, pooled);

  double t0 = now_ms();
  for (int i = 0; i < iters; i++)
    features(in16, stemW, stemB, dwW, dwB, pwW, pwB, bufA, bufB, bufC, pooled);
  double t1 = now_ms();
  double mean = (t1 - t0) / (double)iters;
  /* 校验输出有限，防 DCE */
  double chk = 0.0;
  for (int i = 0; i < H; i++) chk += pooled[i];
  printf("native_features iters=%d mean_ms=%.4f pooled_sum=%.6f\n", iters, mean, chk);
  free(in16); free(stemW); free(stemB); free(dwW); free(dwB);
  free(pwW); free(pwB); free(bufA); free(bufB); free(bufC); free(pooled);
  return 0;
}
