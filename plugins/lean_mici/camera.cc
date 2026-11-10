#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
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

void allocate_nv12(VisionBuf &buf) {
  buf.allocate(VENUS_BUFFER_SIZE(COLOR_FMT_NV12, 512, 256));
  buf.init_yuv(512, 256, VENUS_Y_STRIDE(COLOR_FMT_NV12, 512),
               VENUS_Y_STRIDE(COLOR_FMT_NV12, 512) * VENUS_Y_SCANLINES(COLOR_FMT_NV12, 256));
}

struct Stream {
  VisionStreamType type;
  VisionBuf input, staged[2];  // staged: receive-thread warp double buffer; input: encoder input
  VisionIpcBufExtra staged_extra{};
  int front = 0;
  bool fresh = false;  // staged[front] holds a frame not yet encoded (guarded by Camera::mtx)
  chipmunk::WarpLut warp;
  std::mutex mutex;
  std::condition_variable ready;
  std::vector<uint8_t> header, packet;
  bool released = true;
  std::unique_ptr<V4LEncoder> encoder;

  Stream(VisionStreamType type, int bitrate) : type(type) {
    allocate_nv12(input);
    allocate_nv12(staged[0]);
    allocate_nv12(staged[1]);
    EncoderInfo info{};
    info.publish_name = "lean_mici";
    info.fps = 20;
    info.get_settings = [bitrate](int) { return EncoderSettings{cereal::EncodeIndex::Type::FULL_H_E_V_C, bitrate, 20, 0}; };
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
    staged[0].free();
    staged[1].free();
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

// dev-sp-npu bigmodeld: one receive thread per camera warps each frame on arrival into a single slot; lean_capture
// encodes the road/wide pair whose SOFs are within 10 ms, so camerad's publish order between the two never matters.
struct Camera {
  Stream road, wide;
  int sequence = 0;
  std::mutex mtx;
  std::condition_variable cv;
  float matrix[2][9] = {};
  bool have_matrix = false;
  std::atomic<bool> stop{false};
  std::thread threads[2];

  Camera(int bitrate) : road(VISION_STREAM_NARROW_ROAD, bitrate), wide(VISION_STREAM_WIDE_ROAD, bitrate) {
    threads[0] = std::thread(&Camera::receive, this, 0);
    threads[1] = std::thread(&Camera::receive, this, 1);
  }
  ~Camera() {
    stop = true;
    for (auto &t : threads) t.join();
  }
  void receive(int side) {
    Stream &s = side ? wide : road;
    // Created here: msgq wakes the thread that created the subscriber, so recv must run on this thread.
    VisionIpcClient camera("camerad", s.type, true);
    int back = 1;
    while (!stop) {
      if (!camera.is_connected() && !camera.connect(false)) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        continue;
      }
      VisionIpcBufExtra extra;
      VisionBuf *buf = camera.recv(&extra, 100);
      if (!buf || buf->get_frame_id() != extra.frame_id) continue;
      float m[9];
      {
        std::lock_guard<std::mutex> lock(mtx);
        if (!have_matrix) continue;
        std::memcpy(m, matrix[side], sizeof(m));
      }
      VisionBuf &dst = s.staged[back];
      chipmunk::Nv12View src{buf->y, buf->uv, (int)buf->width, (int)buf->height, (int)buf->stride, (int)buf->stride};
      // Invalid geometry or a buffer camerad recycled during the warp: drop the frame, its partner then goes unpaired.
      if (!s.warp.warp(src, m, {dst.y, dst.uv, (int)dst.stride, (int)dst.stride}) || buf->get_frame_id() != extra.frame_id) continue;
      {
        std::lock_guard<std::mutex> lock(mtx);
        s.front = back;
        s.staged_extra = extra;
        s.fresh = true;
      }
      back ^= 1;
      cv.notify_all();
    }
  }
};
}

extern "C" {
const char *lean_error() { return error.c_str(); }
void *lean_open(int bitrate) {
  try { return new Camera(bitrate); }
  catch (const std::exception &e) { error = e.what(); return nullptr; }
}
void lean_close(Camera *camera) { delete camera; }
void lean_set_bitrate(Camera *camera, int bitrate) {
  camera->road.encoder->set_bitrate(bitrate);
  camera->wide.encoder->set_bitrate(bitrate);
}
// meta supplies warp matrices and receives the encoded frame IDs/EOFs.
int lean_capture(Camera *camera, Meta *meta, bool reset, uint8_t *road, uint8_t *wide, uint32_t *sizes) {
  try {
    VisionIpcBufExtra extra[2];
    {
      std::unique_lock<std::mutex> lock(camera->mtx);
      std::memcpy(camera->matrix, meta->matrix, sizeof(camera->matrix));
      camera->have_matrix = true;
      bool paired = camera->cv.wait_for(lock, std::chrono::milliseconds(100), [camera] {
        Stream &r = camera->road, &w = camera->wide;
        if (!r.fresh || !w.fresh) return false;
        int64_t d = (int64_t)r.staged_extra.timestamp_sof - (int64_t)w.staged_extra.timestamp_sof;
        if (std::abs(d) <= 10'000'000) return true;
        (d > 0 ? w : r).fresh = false;  // the older frame's partner is gone
        return false;
      });
      if (!paired) { error = "no road/wide pair within 100 ms"; return 0; }
      for (Stream *s : {&camera->road, &camera->wide}) {
        std::memcpy(s->input.addr, s->staged[s->front].addr, s->input.len);
        s->fresh = false;
      }
      extra[0] = camera->road.staged_extra;
      extra[1] = camera->wide.staged_extra;
    }
    meta->id[0] = extra[0].frame_id; meta->id[1] = extra[1].frame_id;
    meta->eof[0] = extra[0].timestamp_eof; meta->eof[1] = extra[1].timestamp_eof;
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
