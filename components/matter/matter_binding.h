#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "matter_endpoints.h"
#include "matter_thread.h"

#include <esp_matter.h>

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <utility>
#include <vector>

// Binding framework core. See docs/dev/binding-framework.md for the contract
// and the migration order.
//
// Threads: "main" is the ESPHome main loop, "CHIP" is the CHIP task (which
// holds the CHIP stack lock while it runs ScheduleWork callbacks and esp-matter
// attribute callbacks).
namespace esphome::matter {

// ---------------------------------------------------------------------------
// Platform seam. Everything the framework needs from the outside world, so the
// state machines can be driven deterministically by host tests.
// ---------------------------------------------------------------------------
class BindingPlatform {
public:
  virtual ~BindingPlatform() = default;

  // Any thread. Queue `fn(arg)` on the CHIP task (PlatformMgr().ScheduleWork).
  // Returns false if the CHIP event queue is full. Must not allocate.
  virtual bool schedule_chip(void (*fn)(intptr_t), intptr_t arg) = 0;
  // Any thread. Run `f` on the next main loop iteration.
  virtual void defer_main(std::function<void()> &&f) = 0;
  // Main loop only. Run `f` on the main loop after `delay_ms`.
  virtual void main_after(uint32_t delay_ms, std::function<void()> &&f) = 0;
  // Monotonic milliseconds, wraps at 2^32.
  virtual uint32_t now_ms() = 0;
  // CHIP thread. Arms / disarms the legacy global MatterPushGuard.
  virtual void echo_guard_enter() = 0;
  virtual void echo_guard_exit() = 0;
  // Main loop, after esp_matter::start(). `cb` runs on the CHIP thread for
  // every non-echo store update of the attribute.
  virtual void register_attribute_watch(
      uint16_t endpoint_id, uint32_t cluster_id, uint32_t attribute_id,
      std::function<void(const esp_matter_attr_val_t &)> &&cb) = 0;
  // CHIP thread. Reads the attribute from the store.
  virtual bool read_attribute(uint16_t endpoint_id, uint32_t cluster_id,
                              uint32_t attribute_id,
                              esp_matter_attr_val_t &out) = 0;
  // CHIP thread, inside a PushScope. Writes the attribute store:
  // esp_matter::attribute::report() when `report` (no attribute callbacks,
  // value unchanged = no report), else attribute::update(). Not pure so
  // existing platforms keep compiling; the default fails.
  virtual esp_err_t write_attribute(uint16_t /*endpoint_id*/,
                                    uint32_t /*cluster_id*/,
                                    uint32_t /*attribute_id*/,
                                    esp_matter_attr_val_t & /*value*/,
                                    bool /*report*/) {
    return ESP_FAIL;
  }
};

// The active platform: the one installed by set_binding_platform(), else the
// device implementation (ScheduleWork, MatterComponent::defer). Not available
// in host test builds (MATTER_HOST_TEST) unless one was installed.
BindingPlatform *binding_platform();
void set_binding_platform(BindingPlatform *platform);

// ---------------------------------------------------------------------------
// One CHIP work item per owner, retried when the CHIP queue is full.
// ---------------------------------------------------------------------------
class ChipWorkSlot {
public:
  static constexpr uint32_t RETRY_MS = 50;

  ChipWorkSlot(void (*fn)(intptr_t), intptr_t ctx) : fn_(fn), ctx_(ctx) {}

  // Main loop only. Idempotent while work is outstanding. If ScheduleWork
  // fails the request is retried every RETRY_MS instead of being dropped.
  void request();
  // CHIP, first thing in `fn`. Clears the pending flag *before* the work reads
  // its input, so a request() racing with the read schedules one more run.
  void begin_run() { this->pending_.store(false); }

  bool pending() const { return this->pending_.load(); }
  uint32_t failures() const { return this->failures_; }

private:
  void attempt_();

  void (*fn_)(intptr_t);
  intptr_t ctx_;
  std::atomic<bool> pending_{false};
  uint32_t failures_{0}; // main loop only
};

// ---------------------------------------------------------------------------
// Binding policy
// ---------------------------------------------------------------------------
enum class BootSync : uint8_t {
  MATTER_WINS,  // replay the stored attributes through on_attribute_()
  ESPHOME_WINS, // push the entity state into Matter
  NONE,
};

struct BindingPolicy {
  BootSync boot{BootSync::ESPHOME_WINS};
  // After applying a controller write, wait this long for the entity's own
  // publish (request_push()) before reconciling. 0 reconciles immediately.
  uint16_t settle_ms{0};
  // Push the entity state back after every applied controller write, so an
  // entity that refused or clamped the command ends with Matter == ESPHome.
  bool reconcile_after_apply{true};
};

// ---------------------------------------------------------------------------
// MatterBindingBase
// ---------------------------------------------------------------------------
class MatterBindingBase : public MatterEndpointMappingBase {
public:
  MatterBindingBase(uint16_t endpoint_id, BindingPolicy policy);

  // Main loop, after esp_matter::start(): setup_(), then the boot sync.
  void initialize() final;

  // CHIP thread. True while any binding writes endpoint `ep` inside a
  // PushScope. Consulted by endpoint_attribute_update_cb and by code-driven
  // delegate callbacks (`if (is_echo(endpoint_id())) return;`).
  static bool is_echo(uint16_t ep);

  // Diagnostics / tests.
  uint32_t inbound_seq() const { return this->inbound_seq_.load(); }
  uint32_t applied_seq() const { return this->applied_seq_.load(); }
  bool push_pending() const { return this->push_slot_.pending(); }
  bool is_settling() const;
  const BindingPolicy &policy() const { return this->policy_; }

  // CHIP task RAII. While alive, is_echo(ep) is true and the legacy global
  // MatterPushGuard is armed, so every store write inside it (attribute
  // report/update, code-driven cluster setters) is recognised as ESPHome's own.
  class PushScope {
  public:
    explicit PushScope(uint16_t ep);
    ~PushScope();
    PushScope(const PushScope &) = delete;
    PushScope &operator=(const PushScope &) = delete;

  private:
    uint16_t ep_;
  };

protected:
  // Main loop. Register entity callbacks and watch_attribute()s here.
  virtual void setup_() = 0;

  // ---- ESPHome -> Matter. Main loop. Captures now; at most one CHIP work item
  // is outstanding per binding no matter how often this is called.
  void request_push();

  // ---- Matter -> ESPHome. Main loop. Calls on_attribute_() on the CHIP thread
  // for every non-echo store update of (cluster, attribute) on this endpoint,
  // and for the stored value at boot with BootSync::MATTER_WINS.
  void watch_attribute(uint32_t cluster, uint32_t attribute);
  virtual void on_attribute_(uint32_t /*cluster*/, uint32_t /*attribute*/,
                             const esp_matter_attr_val_t & /*v*/) {}

  // Implemented by MatterBinding<>.
  virtual void run_push_on_chip_() = 0;
  virtual void run_apply_on_main_() = 0;
  // Capture the entity state into the outbound slot (main). Called by
  // request_push(); implemented by MatterBinding<>.
  virtual void capture_into_slot_() = 0;

  // CHIP: a controller write was queued. Call after the slot is filled.
  void mark_inbound_();
  void schedule_apply_();
  // Main: shared tail of run_apply_on_main_().
  void finish_apply_(uint32_t seq);

  uint32_t current_inbound_seq_() const { return this->inbound_seq_.load(); }
  bool inbound_pending_() const {
    return this->inbound_seq_.load() != this->applied_seq_.load();
  }
  void note_push_skipped_() { this->push_skipped_.store(true); }
  void clear_apply_scheduled_() { this->apply_scheduled_.store(false); }

  BindingPolicy policy_;

private:
  struct Watch {
    uint32_t cluster;
    uint32_t attribute;
  };

  static void push_trampoline_(intptr_t ctx);
  static void boot_trampoline_(intptr_t ctx);
  void run_boot_replay_on_chip_();
  void reconcile_();

  ChipWorkSlot push_slot_;
  ChipWorkSlot boot_slot_;
  std::vector<Watch> watches_;
  std::atomic<bool> apply_scheduled_{false};
  std::atomic<uint32_t> inbound_seq_{0}; // ++ on CHIP per controller write
  std::atomic<uint32_t> applied_seq_{0}; // = inbound_seq_ after apply (main)
  std::atomic<bool> push_skipped_{false};
  // Main loop only.
  bool settling_{false};
  uint32_t settle_gen_{0};
  uint32_t settle_until_ms_{0};

protected:
  // MatterBinding<> hooks into the same lock for both slots.
  Mutex mu_;
};

// ---------------------------------------------------------------------------
// MatterBinding<State, Inbound>
//
//   State    the ESPHome state mirrored into Matter (copyable, default
//            constructible); outbound slot content.
//   Inbound  the accumulated controller request; inbound slot content.
// ---------------------------------------------------------------------------
template <typename State, typename Inbound>
class MatterBinding : public MatterBindingBase {
public:
  using MatterBindingBase::MatterBindingBase;

protected:
  virtual State capture_() = 0;                           // main
  virtual void write_matter_(const State &s) = 0;         // CHIP, in PushScope
  virtual void merge_inbound_(Inbound &acc, const Inbound &in) { acc = in; } // CHIP
  virtual void apply_inbound_(const Inbound &in) = 0;     // main

  // CHIP: merge into the inbound slot, bump the sequence, schedule the apply.
  void submit_inbound(const Inbound &in) {
    MATTER_ASSERT_CHIP_THREAD();
    {
      LockGuard guard(this->mu_);
      if (this->in_valid_)
        this->merge_inbound_(this->in_, in);
      else
        this->in_ = in;
      this->in_valid_ = true;
      this->mark_inbound_();
    }
    this->schedule_apply_();
  }

private:
  void capture_into_slot_() override {
    State s = this->capture_();
    LockGuard guard(this->mu_);
    this->out_ = std::move(s);
    this->out_valid_ = true;
  }

  void run_push_on_chip_() override {
    MATTER_ASSERT_CHIP_THREAD();
    State s;
    {
      LockGuard guard(this->mu_);
      if (!this->out_valid_)
        return;
      s = this->out_;
    }
    // A controller write is waiting for the main loop: this snapshot is older
    // than the write and must not clobber it. The reconcile push after the
    // apply carries fresh state.
    if (this->inbound_pending_()) {
      this->note_push_skipped_();
      return;
    }
    PushScope scope(this->endpoint_id_);
    this->write_matter_(s);
  }

  void run_apply_on_main_() override {
    MATTER_ASSERT_MAIN_LOOP();
    Inbound in;
    uint32_t seq;
    {
      LockGuard guard(this->mu_);
      this->clear_apply_scheduled_();
      if (!this->in_valid_)
        return;
      in = this->in_;
      this->in_valid_ = false;
      seq = this->current_inbound_seq_();
    }
    this->apply_inbound_(in);
    this->finish_apply_(seq);
  }

  State out_{};
  Inbound in_{};
  bool out_valid_{false};
  bool in_valid_{false};
};

// ---------------------------------------------------------------------------
// Discrete events (switch presses, OperationCompletion): bounded FIFO, never
// coalesced. When full the oldest event is dropped with a warning.
// ---------------------------------------------------------------------------
template <typename Event, size_t N = 8> class MatterEventQueue {
public:
  using Sink = std::function<void(uint16_t, const Event &)>;

  // `sink` runs on the CHIP thread, once per event, in order.
  explicit MatterEventQueue(Sink sink)
      : sink_(std::move(sink)),
        slot_(&MatterEventQueue::trampoline_,
              reinterpret_cast<intptr_t>(this)) {}

  // Main loop. One CHIP work item per drain, not per event.
  void post(uint16_t ep, const Event &e) {
    MATTER_ASSERT_MAIN_LOOP();
    {
      LockGuard guard(this->mu_);
      if (this->count_ == N) {
        this->head_ = (this->head_ + 1) % N;
        this->count_--;
        this->dropped_++;
        if (this->dropped_ == 1 || this->dropped_ % 16 == 0)
          ESP_LOGW("matter.binding",
                   "Event queue full, dropped oldest event (%u dropped)",
                   static_cast<unsigned>(this->dropped_));
      }
      this->items_[(this->head_ + this->count_) % N] = Item{ep, e};
      this->count_++;
    }
    this->slot_.request();
  }

  uint32_t dropped() const { return this->dropped_; }
  size_t size() {
    LockGuard guard(this->mu_);
    return this->count_;
  }
  bool drain_pending() const { return this->slot_.pending(); }

private:
  struct Item {
    uint16_t ep{0};
    Event event{};
  };

  static void trampoline_(intptr_t ctx) {
    reinterpret_cast<MatterEventQueue *>(ctx)->drain_();
  }
  void drain_() {
    MATTER_ASSERT_CHIP_THREAD();
    this->slot_.begin_run();
    for (;;) {
      Item item;
      {
        LockGuard guard(this->mu_);
        if (this->count_ == 0)
          return;
        item = this->items_[this->head_];
        this->head_ = (this->head_ + 1) % N;
        this->count_--;
      }
      this->sink_(item.ep, item.event);
    }
  }

  Sink sink_;
  ChipWorkSlot slot_;
  Mutex mu_;
  std::array<Item, N> items_{};
  size_t head_{0};
  size_t count_{0};
  uint32_t dropped_{0};
};

} // namespace esphome::matter

#endif // USE_MATTER
