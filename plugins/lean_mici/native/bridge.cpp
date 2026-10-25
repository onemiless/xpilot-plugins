#include "warp_pack.h"
#include <cmath>
#include <cstdint>

extern "C" int lean_abi() { return 1; }
extern "C" int lean_warp(const uint8_t *src, int width, int height, int stride, int uv_offset,
                         const float *matrix, uint8_t *dst) {
  if (width <= 0 || height <= 0 || stride < width || uv_offset < stride * height) return 0;
  for (int i = 0; i < 9; ++i) if (!std::isfinite(matrix[i])) return 0;
  return chipmunk::warpNv12({src, src + uv_offset, width, height, stride, stride}, matrix,
                            {dst, dst + 512 * 256, 512, 512});
}

#ifndef LEAN_CAMERA
// A host-only build must never pass the store's camera readiness check.
extern "C" int lean_camera_available() { return 0; }
extern "C" int lean_capture(const void *, int, void *, int) { return -1; }
#endif
