#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "matter_binding.h"

#include "esphome/core/log.h"

#ifndef MATTER_HOST_TEST
#include "esphome/core/hal.h"
#include "matter_attributes.h"
#include "matter_component.h"

#include <platform/CHIPDeviceLayer.h>

#include <new>
#include <optional>
#endif

static const char *const TAG = "matter.binding";

namespace esphome::matter {

namespace {

BindingPlatform *installed_platform = nullptr;

// Endpoints currently written by a PushScope. CHIP task only, so no locking.
// Nested scopes on the same endpoint share one entry.
struct EchoEntry {
  uint16_t ep;
  uint8_t depth;
};
constexpr size_t MAX_ECHO_ENTRIES = 8;
std::array<EchoEntry, MAX_ECHO_ENTRIES> echo_entries{};
size_t echo_count = 0;
// Scopes that did not fit in the table. While non-zero every endpoint counts as
// echo, which is the safe direction (never feed an echo back to ESPHome).
uint8_t echo_overflow = 0;

} // namespace

#ifndef MATTER_HOST_TEST

namespace {

class DeviceBindingPlatform final : public BindingPlatform {
public:
  bool schedule_chip(void (*fn)(intptr_t), intptr_t arg) override {
    return chip::DeviceLayer::PlatformMgr().ScheduleWork(fn, arg) ==
           CHIP_NO_ERROR;
  }
  void defer_main(std::function<void()> &&f) override {
    global_matter_component->defer_to_main_loop(std::move(f));
  }
  void main_after(uint32_t delay_ms, std::function<void()> &&f) override {
    global_matter_component->run_on_main_loop_after(delay_ms, std::move(f));
  }
  uint32_t now_ms() override { return millis(); }

  // PushScope nests on the CHIP task; the legacy guards live in a small
  // CHIP-task-only stack so they can be released in order.
  void echo_guard_enter() override {
    if (this->depth_ < GUARDS)
      this->guards_[this->depth_].emplace();
    this->depth_++;
  }
  void echo_guard_exit() override {
    this->depth_--;
    if (this->depth_ < GUARDS)
      this->guards_[this->depth_].reset();
  }

  void register_attribute_watch(
      uint16_t endpoint_id, uint32_t cluster_id, uint32_t attribute_id,
      std::function<void(const esp_matter_attr_val_t &)> &&cb) override {
    global_matter_component->register_attribute_callback(
        endpoint_id, cluster_id, attribute_id, std::move(cb));
  }
  bool read_attribute(uint16_t endpoint_id, uint32_t cluster_id,
                      uint32_t attribute_id,
                      esp_matter_attr_val_t &out) override {
    // Optional attributes may be absent: don't let get_val log an error.
    if (esp_matter::attribute::get(endpoint_id, cluster_id, attribute_id) ==
        nullptr)
      return false;
    return esp_matter::attribute::get_val(endpoint_id, cluster_id,
                                          attribute_id, &out) == ESP_OK;
  }
  esp_err_t write_attribute(uint16_t endpoint_id, uint32_t cluster_id,
                            uint32_t attribute_id, esp_matter_attr_val_t &value,
                            bool report) override {
    // A nullable attribute (laundry SpinSpeedCurrent, ...) may be written with
    // a plain value of the same base type: adopt the stored type.
    esp_matter_val_type_t stored = esp_matter::attribute::get_val_type(
        endpoint_id, cluster_id, attribute_id);
    if (stored != ESP_MATTER_VAL_TYPE_INVALID) {
      esp_matter_attr_val_t probe;
      probe.type = stored;
      if (probe.get_base_type() == value.get_base_type())
        value.type = stored;
    }
    return report ? esp_matter::attribute::report(endpoint_id, cluster_id,
                                                  attribute_id, &value)
                  : esp_matter::attribute::update(endpoint_id, cluster_id,
                                                  attribute_id, &value);
  }

private:
  static constexpr size_t GUARDS = 4;
  std::array<std::optional<MatterPushGuard>, GUARDS> guards_;
  size_t depth_{0};
};

} // namespace

BindingPlatform *binding_platform() {
  if (installed_platform != nullptr)
    return installed_platform;
  static DeviceBindingPlatform device_platform;
  return &device_platform;
}

#else

BindingPlatform *binding_platform() { return installed_platform; }

#endif

void set_binding_platform(BindingPlatform *platform) {
  installed_platform = platform;
}

// ---------------------------------------------------------------------------
// ChipWorkSlot
// ---------------------------------------------------------------------------

void ChipWorkSlot::request() {
  if (this->pending_.exchange(true))
    return; // already scheduled (or waiting for a retry)
  this->attempt_();
}

void ChipWorkSlot::attempt_() {
  BindingPlatform *platform = binding_platform();
  if (platform->schedule_chip(this->fn_, this->ctx_))
    return;
  // The CHIP event queue is full. pending_ stays set so request() keeps
  // coalescing into the retry; the work reads the newest state when it runs.
  this->failures_++;
  if (this->failures_ == 1 || this->failures_ % 20 == 0)
    ESP_LOGW(TAG, "ScheduleWork failed, retrying in %u ms (%u failures)",
             static_cast<unsigned>(RETRY_MS),
             static_cast<unsigned>(this->failures_));
  platform->main_after(RETRY_MS, [this]() { this->attempt_(); });
}

// ---------------------------------------------------------------------------
// Echo scope
// ---------------------------------------------------------------------------

MatterBindingBase::PushScope::PushScope(uint16_t ep) : ep_(ep) {
  MATTER_ASSERT_CHIP_THREAD();
  bool found = false;
  for (size_t i = 0; i < echo_count; i++) {
    if (echo_entries[i].ep == ep) {
      echo_entries[i].depth++;
      found = true;
      break;
    }
  }
  if (!found) {
    if (echo_count < MAX_ECHO_ENTRIES)
      echo_entries[echo_count++] = EchoEntry{ep, 1};
    else
      echo_overflow++;
  }
  binding_platform()->echo_guard_enter();
}

MatterBindingBase::PushScope::~PushScope() {
  binding_platform()->echo_guard_exit();
  for (size_t i = 0; i < echo_count; i++) {
    if (echo_entries[i].ep != this->ep_)
      continue;
    if (--echo_entries[i].depth == 0) {
      echo_entries[i] = echo_entries[echo_count - 1];
      echo_count--;
    }
    return;
  }
  if (echo_overflow > 0)
    echo_overflow--;
}

bool MatterBindingBase::is_echo(uint16_t ep) {
  if (echo_overflow > 0)
    return true;
  for (size_t i = 0; i < echo_count; i++) {
    if (echo_entries[i].ep == ep)
      return true;
  }
  return false;
}

// ---------------------------------------------------------------------------
// MatterBindingBase
// ---------------------------------------------------------------------------

MatterBindingBase::MatterBindingBase(uint16_t endpoint_id, BindingPolicy policy)
    : MatterEndpointMappingBase(endpoint_id), policy_(policy),
      push_slot_(&MatterBindingBase::push_trampoline_,
                 reinterpret_cast<intptr_t>(this)),
      boot_slot_(&MatterBindingBase::boot_trampoline_,
                 reinterpret_cast<intptr_t>(this)) {}

void MatterBindingBase::push_trampoline_(intptr_t ctx) {
  auto *self = reinterpret_cast<MatterBindingBase *>(ctx);
  self->push_slot_.begin_run();
  self->run_push_on_chip_();
}

void MatterBindingBase::boot_trampoline_(intptr_t ctx) {
  auto *self = reinterpret_cast<MatterBindingBase *>(ctx);
  self->boot_slot_.begin_run();
  self->run_boot_replay_on_chip_();
}

void MatterBindingBase::initialize() {
  thread::capture_main_loop();
  MATTER_ASSERT_MAIN_LOOP();
  this->setup_();
  switch (this->policy_.boot) {
  case BootSync::ESPHOME_WINS:
    this->request_push();
    break;
  case BootSync::MATTER_WINS:
    this->boot_slot_.request();
    break;
  case BootSync::NONE:
    break;
  }
}

void MatterBindingBase::watch_attribute(uint32_t cluster, uint32_t attribute) {
  MATTER_ASSERT_MAIN_LOOP();
  this->watches_.push_back(Watch{cluster, attribute});
  binding_platform()->register_attribute_watch(
      this->endpoint_id_, cluster, attribute,
      [this, cluster, attribute](const esp_matter_attr_val_t &v) {
        MATTER_ASSERT_CHIP_THREAD();
        if (is_echo(this->endpoint_id_))
          return;
        this->on_attribute_(cluster, attribute, v);
      });
}

void MatterBindingBase::run_boot_replay_on_chip_() {
  MATTER_ASSERT_CHIP_THREAD();
  for (const Watch &w : this->watches_) {
    esp_matter_attr_val_t v;
    if (binding_platform()->read_attribute(this->endpoint_id_, w.cluster,
                                           w.attribute, v))
      this->on_attribute_(w.cluster, w.attribute, v);
  }
}

void MatterBindingBase::request_push() {
  MATTER_ASSERT_MAIN_LOOP();
  // The entity published (or a reconcile asked): the settle wait is over.
  this->settling_ = false;
  this->capture_into_slot_();
  this->push_slot_.request();
}

void MatterBindingBase::mark_inbound_() {
  // Called with the slot lock held so the sequence matches the slot content.
  this->inbound_seq_.fetch_add(1);
}

void MatterBindingBase::schedule_apply_() {
  if (this->apply_scheduled_.exchange(true))
    return;
  binding_platform()->defer_main([this]() { this->run_apply_on_main_(); });
}

void MatterBindingBase::finish_apply_(uint32_t seq) {
  this->applied_seq_.store(seq);
  this->reconcile_();
}

bool MatterBindingBase::is_settling() const {
  if (!this->settling_)
    return false;
  return static_cast<int32_t>(this->settle_until_ms_ -
                              binding_platform()->now_ms()) > 0;
}

void MatterBindingBase::reconcile_() {
  // A skipped push means Matter may be behind ESPHome even without the policy.
  bool skipped = this->push_skipped_.exchange(false);
  if (!this->policy_.reconcile_after_apply && !skipped)
    return;
  if (this->policy_.settle_ms == 0) {
    this->request_push();
    return;
  }
  BindingPlatform *platform = binding_platform();
  this->settling_ = true;
  this->settle_until_ms_ = platform->now_ms() + this->policy_.settle_ms;
  uint32_t gen = ++this->settle_gen_;
  platform->main_after(this->policy_.settle_ms, [this, gen]() {
    // Still the same settle window and the entity never published: reconcile
    // with whatever the entity says now (this restores truth after a refusal).
    if (this->settling_ && this->settle_gen_ == gen)
      this->request_push();
  });
}

} // namespace esphome::matter

#endif // USE_MATTER
