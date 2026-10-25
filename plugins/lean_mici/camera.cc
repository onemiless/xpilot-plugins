#include <chrono>
#include <condition_variable>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <media/msm_media_info.h>
#include "system/loggerd/encoder/v4l_encoder.h"
#include "warp_pack.h"

namespace {
constexpr size_t LIMIT = 1024 * 1024;
thread_local std::string error;
#pragma pack(push, 1)
struct Meta {
  char magic[4];
  uint32_t version;
  uint64_t session;
  uint32_t id[2];
  uint64_t eof[2];
  float matrix[2][9], auxiliary[12];
};
#pragma pack(pop)
static_assert(sizeof(Meta) == 160);

struct Stream {
  VisionIpcClient camera;
  VisionBuf input;
  chipmunk::WarpLut warp;
  std::mutex mutex;
  std::condition_variable ready;
  std::vector<uint8_t> header, packet;
  bool released = true;
  std::unique_ptr<V4LEncoder> encoder;

  Stream(VisionStreamType type) : camera("camerad", type, true) {
    input.allocate(VENUS_BUFFER_SIZE(COLOR_FMT_NV12, 512, 256));
    input.init_yuv(512, 256, VENUS_Y_STRIDE(COLOR_FMT_NV12, 512),
                   VENUS_Y_STRIDE(COLOR_FMT_NV12, 512) * VENUS_Y_SCANLINES(COLOR_FMT_NV12, 256));
    EncoderInfo info{};
    info.publish_name = "lean_mici";
    info.fps = 20;
    info.get_settings = [](int) { return EncoderSettings{cereal::EncodeIndex::Type::FULL_H_E_V_C, 2'000'000, 20, 0}; };
    V4LEncoder::Options options;
    options.max_performance = true;
    options.packet_callback = [this](uint8_t *data, size_t size, int64_t, bool config, bool key) {
      std::lock_guard<std::mutex> lock(mutex);
      if (config) header.assign(data, data + size);
      else {
        packet = key ? header : std::vector<uint8_t>{};
        packet.insert(packet.end(), data, data + size);
        ready.notify_one();
      }
    };
    options.input_done_callback = [this](VisionBuf *) {
      std::lock_guard<std::mutex> lock(mutex);
      released = true;
      ready.notify_one();
    };
    encoder = std::make_unique<V4LEncoder>(info, 512, 256, options);
    encoder->encoder_open();
  }
  ~Stream() {
    encoder->encoder_close();
    encoder.reset();
    input.free();
  }
  bool capture(const Meta &meta, int side, VisionIpcBufExtra &extra) {
    if (!camera.is_connected() && !camera.connect(false)) return false;
    auto *buf = camera.recv(&extra, 5);
    if (!buf || extra.frame_id != meta.id[side] || extra.timestamp_eof != meta.eof[side] ||
        buf->get_frame_id() != extra.frame_id) return false;
    chipmunk::Nv12View src{buf->y, buf->uv, (int)buf->width, (int)buf->height, (int)buf->stride, (int)buf->stride};
    if (!warp.warp(src, meta.matrix[side], {input.y, input.uv, (int)input.stride, (int)input.stride}))
      throw std::runtime_error("invalid camera geometry");
    return buf->get_frame_id() == extra.frame_id;
  }
  void submit(VisionIpcBufExtra &extra, bool reset) {
    {
      std::lock_guard<std::mutex> lock(mutex);
      packet.clear();
      released = false;
    }
    if (reset) encoder->request_keyframe();
    input.sync(VISIONBUF_SYNC_TO_DEVICE);
    encoder->encode_frame(&input, &extra);
  }
  size_t collect(uint8_t *out) {
    std::unique_lock<std::mutex> lock(mutex);
    if (!ready.wait_for(lock, std::chrono::milliseconds(200), [this]{ return released && !packet.empty(); }))
      throw std::runtime_error("hardware encoder deadline");
    if (packet.size() > LIMIT) throw std::runtime_error("HEVC segment exceeds FRAME limit");
    std::memcpy(out, packet.data(), packet.size());
    return packet.size();
  }
};
struct Camera {
  Stream road{VISION_STREAM_NARROW_ROAD}, wide{VISION_STREAM_WIDE_ROAD};
  int sequence = 0;
};
}

extern "C" {
const char *lean_error() { return error.c_str(); }
void *lean_open() {
  try { return new Camera; }
  catch (const std::exception &e) { error = e.what(); return nullptr; }
}
void lean_close(Camera *camera) { delete camera; }
int lean_capture(Camera *camera, const Meta *meta, bool reset, uint8_t *road, uint8_t *wide, uint32_t *sizes) {
  try {
    VisionIpcBufExtra extra[2];
    if (!camera->road.capture(*meta, 0, extra[0]) || !camera->wide.capture(*meta, 1, extra[1])) return 0;
    if (std::abs((int64_t)extra[0].timestamp_sof - (int64_t)extra[1].timestamp_sof) > 10'000'000) return 0;
    if (reset) camera->sequence = 0;
    camera->road.submit(extra[0], reset);
    camera->wide.submit(extra[1], reset || camera->sequence == 10);
    ++camera->sequence;
    sizes[0] = camera->road.collect(road);
    sizes[1] = camera->wide.collect(wide);
    return 1;
  } catch (const std::exception &e) { error = e.what(); return -1; }
}
}
