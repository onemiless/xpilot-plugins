// Camera dimensions/stride/UV offset come from VisionBuf; warps come from host S1 metadata.
#include <array>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <memory>
#include <mutex>
#include <vector>

#include "msgq/visionipc/visionipc_client.h"
#include "openpilot/cereal/visionstream.h"
#include "system/camerad/cameras/nv12_info.h"
#include "system/loggerd/encoder/v4l_encoder.h"
#include "warp_pack.h"

// V4LEncoder's packet callback handles all output. Linking the usual base constructor
// would bind a cereal publisher even with that callback. This private DSO omits encoder.cc.
VideoEncoder::VideoEncoder(const EncoderInfo &info, int w, int h)
    : in_width(w), in_height(h), out_width(w), out_height(h), encoder_info(info) {}
void VideoEncoder::publisher_publish(int, uint32_t, VisionIpcBufExtra &, unsigned int,
                                    kj::ArrayPtr<capnp::byte>, kj::ArrayPtr<capnp::byte>) {
  std::abort();
}

namespace {
#pragma pack(push, 1)
struct Meta {
  char magic[4];
  uint32_t abi;
  uint64_t session;
  uint32_t road, wide;
  uint64_t eof, wide_eof;
  float warp[18], desire[8], traffic[2], action[2];
};
#pragma pack(pop)
static_assert(sizeof(Meta) == 160);

struct Camera {
  VisionIpcClient client;
  VisionBuf input;
  std::unique_ptr<V4LEncoder> encoder;
  std::mutex mutex;
  std::condition_variable cv;
  std::vector<uint8_t> header, packet;
  uint64_t eof = 0;
  bool keyframe = false, input_free = true;

  explicit Camera(VisionStreamType stream) : client("camerad", stream, true) {
    unsigned stride, y_height, uv_height, size;
    std::tie(stride, y_height, uv_height, size) = get_nv12_info(512, 256);
    input.allocate(size);
    input.init_yuv(512, 256, stride, stride * y_height);
    EncoderInfo info{};
    info.publish_name = "";
    info.record = false;
    info.frame_width = 512;
    info.frame_height = 256;
    info.fps = 20;
    info.get_settings = [](int) {
      return EncoderSettings{cereal::EncodeIndex::Type::FULL_H_E_V_C, 2'000'000, 20, 0};
    };
    V4LEncoder::Options options;
    options.packet_callback = [this](uint8_t *data, size_t size, int64_t pts, bool config, bool idr) {
      std::lock_guard<std::mutex> lock(mutex);
      if (size > 1024 * 1024) std::abort();
      if (config) {
        header.assign(data, data + size);
        return;
      }
      packet.clear();
      if (idr) packet.insert(packet.end(), header.begin(), header.end());
      packet.insert(packet.end(), data, data + size);
      eof = pts;
      keyframe = idr;
      cv.notify_all();
    };
    options.input_done_callback = [this](VisionBuf *) {
      std::lock_guard<std::mutex> lock(mutex);
      input_free = true;
      cv.notify_all();
    };
    encoder = std::make_unique<V4LEncoder>(info, 512, 256, options);
    encoder->encoder_open();
  }

  VisionBuf *receive(uint32_t id, uint64_t expected_eof, VisionIpcBufExtra &extra) {
    if (!client.connected && !client.connect(false)) return nullptr;
    auto *src = client.recv(&extra, 5);
    return src && extra.frame_id == id && extra.timestamp_eof == expected_eof ? src : nullptr;
  }

  bool capture(VisionBuf *src, VisionIpcBufExtra &extra, const float *matrix, bool reset, unsigned sequence) {
    {
      std::lock_guard<std::mutex> lock(mutex);
      if (!input_free) return false;
      input_free = false;
      packet.clear();
    }
    src->sync(VISIONBUF_SYNC_FROM_DEVICE);
    chipmunk::Nv12View view{src->y, src->uv, int(src->width), int(src->height), int(src->stride), int(src->stride)};
    chipmunk::Nv12Out dst{input.y, input.uv, int(input.stride), int(input.stride)};
    if (!chipmunk::warpNv12(view, matrix, dst)) std::abort();
    input.sync(VISIONBUF_SYNC_TO_DEVICE);
    if (reset || sequence == 10) encoder->request_keyframe();
    encoder->encode_frame(&input, &extra);
    return true;
  }

  bool finish(uint64_t expected_eof) {
    std::unique_lock<std::mutex> lock(mutex);
    return cv.wait_for(lock, std::chrono::milliseconds(100), [&] {
      return input_free && !packet.empty() && eof == expected_eof / 1000;
    });
  }
};
}

extern "C" int lean_camera_available() { return 1; }
extern "C" int lean_capture(const void *metadata, int reset, void *output, int capacity) {
  const auto &meta = *static_cast<const Meta *>(metadata);
  if (std::memcmp(meta.magic, "XMS1", 4) || meta.abi != 1) return -1;
  if (meta.warp[8] == 0 || meta.warp[17] == 0) return 0;
  static Camera road(VISION_STREAM_NARROW_ROAD), wide(VISION_STREAM_WIDE_ROAD);
  static unsigned sequence = 0;
  if (reset) sequence = 0;
  VisionIpcBufExtra road_extra, wide_extra;
  auto *road_src = road.receive(meta.road, meta.eof, road_extra);
  auto *wide_src = wide.receive(meta.wide, meta.wide_eof, wide_extra);
  if (!road_src || !wide_src) return 0;
  auto first = road_extra.timestamp_sof, second = wide_extra.timestamp_sof;
  if ((first > second ? first - second : second - first) > 10'000'000) return 0;
  if (!road.capture(road_src, road_extra, meta.warp, reset, 0) ||
      !wide.capture(wide_src, wide_extra, meta.warp + 9, reset, sequence)) return 0;
  if (!road.finish(meta.eof) || !wide.finish(meta.wide_eof)) return 0;
  ++sequence;
  std::scoped_lock lock(road.mutex, wide.mutex);
  if (road.packet.size() > 1024 * 1024 || wide.packet.size() > 1024 * 1024 ||
      road.packet.size() + wide.packet.size() + 8 > size_t(capacity)) return -1;
  auto *out = static_cast<uint8_t *>(output);
  uint32_t flags = unsigned(road.keyframe) | (unsigned(wide.keyframe) << 1);
  uint32_t length = road.packet.size();
  std::memcpy(out, &flags, 4);
  std::memcpy(out + 4, &length, 4);
  std::memcpy(out + 8, road.packet.data(), road.packet.size());
  std::memcpy(out + 8 + road.packet.size(), wide.packet.data(), wide.packet.size());
  return 8 + road.packet.size() + wide.packet.size();
}
