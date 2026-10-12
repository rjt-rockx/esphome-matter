#pragma once
// Deterministic BindingPlatform: the "CHIP queue", the "main loop" and the clock
// are all driven by the test.
#include "matter_binding.h"
#include "matter_thread.h"

#include <deque>
#include <functional>
#include <map>
#include <tuple>
#include <vector>

namespace tp {

using esphome::matter::BindingPlatform;
using esphome::matter::thread::HostRole;
using esphome::matter::thread::host_set_role;

class FakePlatform : public BindingPlatform {
 public:
  // --- BindingPlatform
  bool schedule_chip(void (*fn)(intptr_t), intptr_t arg) override {
    if (fail_schedule > 0) {
      fail_schedule--;
      schedule_failures++;
      return false;
    }
    chip_queue.push_back({fn, arg});
    return true;
  }
  void defer_main(std::function<void()> &&f) override { main_queue.push_back(std::move(f)); }
  void main_after(uint32_t delay_ms, std::function<void()> &&f) override {
    timers.push_back({now + delay_ms, std::move(f)});
  }
  uint32_t now_ms() override { return now; }
  void echo_guard_enter() override { legacy_guard_depth++; }
  void echo_guard_exit() override { legacy_guard_depth--; }
  void register_attribute_watch(uint16_t ep, uint32_t cl, uint32_t at,
                                std::function<void(const esp_matter_attr_val_t &)> &&cb) override {
    watches.push_back({ep, cl, at, std::move(cb)});
  }
  bool read_attribute(uint16_t ep, uint32_t cl, uint32_t at, esp_matter_attr_val_t &out) override {
    auto it = store.find({ep, cl, at});
    if (it == store.end())
      return false;
    out = it->second;
    return true;
  }

  esp_err_t write_attribute(uint16_t ep, uint32_t cl, uint32_t at, esp_matter_attr_val_t &value,
                            bool report) override {
    attribute_writes.push_back({ep, cl, at, value, report, esphome::matter::MatterBindingBase::is_echo(ep)});
    if (write_result != ESP_OK)
      return write_result;
    store[{ep, cl, at}] = value;
    // attribute::update() runs the POST_UPDATE callback (report() does not). The watch callbacks must
    // drop it themselves while the write is ESPHome's own (PushScope).
    if (!report && fire_on_update)
      fire_attribute(ep, cl, at, value);
    return ESP_OK;
  }

  // --- Test controls
  // Runs the oldest CHIP work item as the CHIP thread. Returns false if none.
  bool run_chip_one() {
    if (chip_queue.empty())
      return false;
    auto item = chip_queue.front();
    chip_queue.pop_front();
    host_set_role(HostRole::CHIP);
    item.first(item.second);
    host_set_role(HostRole::MAIN);
    return true;
  }
  int run_chip() {
    int n = 0;
    while (run_chip_one())
      n++;
    return n;
  }
  bool run_main_one() {
    if (main_queue.empty())
      return false;
    auto f = std::move(main_queue.front());
    main_queue.pop_front();
    host_set_role(HostRole::MAIN);
    f();
    return true;
  }
  int run_main() {
    int n = 0;
    while (run_main_one())
      n++;
    return n;
  }
  // Advance the clock, firing due timers in order (on the main loop).
  void advance(uint32_t ms) {
    uint32_t end = now + ms;
    for (;;) {
      size_t best = timers.size();
      for (size_t i = 0; i < timers.size(); i++)
        if (timers[i].due <= end && (best == timers.size() || timers[i].due < timers[best].due))
          best = i;
      if (best == timers.size())
        break;
      Timer t = std::move(timers[best]);
      timers.erase(timers.begin() + best);
      now = t.due > now ? t.due : now;
      host_set_role(HostRole::MAIN);
      t.fn();
    }
    now = end;
  }
  // Drains CHIP + main queues until quiet (not timers).
  void settle() {
    for (int i = 0; i < 100 && (!chip_queue.empty() || !main_queue.empty()); i++) {
      run_chip();
      run_main();
    }
  }
  // Delivers a store update to the registered watches, like
  // endpoint_attribute_update_cb -> dispatch_attribute_update (CHIP thread).
  void fire_attribute(uint16_t ep, uint32_t cl, uint32_t at, const esp_matter_attr_val_t &v) {
    for (auto &w : watches)
      if (w.ep == ep && w.cl == cl && w.at == at)
        w.cb(v);
  }

  struct Timer {
    uint32_t due;
    std::function<void()> fn;
  };
  struct Watch {
    uint16_t ep;
    uint32_t cl, at;
    std::function<void(const esp_matter_attr_val_t &)> cb;
  };
  struct AttrWrite {
    uint16_t ep;
    uint32_t cl, at;
    esp_matter_attr_val_t value;
    bool report;
    bool echo;  // was inside a PushScope for `ep`
  };
  std::vector<AttrWrite> attribute_writes;
  esp_err_t write_result{ESP_OK};
  bool fire_on_update{true};
  std::deque<std::pair<void (*)(intptr_t), intptr_t>> chip_queue;
  std::deque<std::function<void()>> main_queue;
  std::vector<Timer> timers;
  std::vector<Watch> watches;
  std::map<std::tuple<uint16_t, uint32_t, uint32_t>, esp_matter_attr_val_t> store;
  uint32_t now{1000};
  int fail_schedule{0};
  int schedule_failures{0};
  int legacy_guard_depth{0};
};

inline esp_matter_attr_val_t bool_val(bool b) {
  esp_matter_attr_val_t v;
  v.type = ESP_MATTER_VAL_TYPE_BOOLEAN;
  v.val.b = b;
  return v;
}

}  // namespace tp
