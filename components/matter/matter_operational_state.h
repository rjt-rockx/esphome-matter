#pragma once

#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_MATTER_OPERATIONAL_STATE)

#include "esphome/core/automation.h"
#include "matter_delegates.h"
#include "matter_endpoints.h"
#ifdef USE_SELECT
#include "esphome/components/select/select.h"
#endif
#ifdef USE_TEXT_SENSOR
#include "esphome/components/text_sensor/text_sensor.h"
#endif
#ifdef USE_SENSOR
#include "esphome/components/sensor/sensor.h"
#endif

#include <app/clusters/operational-state-server/operational-state-server.h>
#include <esp_matter.h>

#include <atomic>
#include <cstdint>
#include <string>
#include <vector>

namespace esphome::matter {

// Commands of the Operational State clusters that can be bound to automations.
enum class MatterOperationalCommand : uint8_t {
  PAUSE,
  RESUME,
  START,
  STOP,
  GO_HOME,
};

class MatterOperationalCommandTrigger : public Trigger<> {};

// Delegate of the Operational State (0x0060) and RVC Operational State (0x0061) clusters.
//
// Matter -> ESPHome: Pause/Resume/Start/Stop/GoHome run on the CHIP task. Each runs its trigger on the main loop and
// answers with OperationalCommandResponse; `matter.operational_state.reject` in the trigger turns the response into an
// error (default is NoError). Commands without a trigger are not created in the data model at all.
//
// ESPHome -> Matter: the bound select/text sensor (or `matter.operational_state.set`) updates OperationalState and
// CurrentPhase through the OperationalState::Instance; the countdown sensor feeds CountdownTime. Errors and operation
// completion are events raised by `matter.operational_state.error` / `.completed`.
class MatterOperationalStateMapping
    : public MatterEndpointMappingBase,
      public chip::app::Clusters::RvcOperationalState::Delegate {
public:
  MatterOperationalStateMapping(uint16_t endpoint_id, uint32_t cluster_id);

  uint32_t cluster_id() const { return this->cluster_id_; }
  static MatterOperationalStateMapping *find(uint16_t endpoint_id,
                                             uint32_t cluster_id);

  // Codegen.
  // Entry of OperationalStateList; `label` is required for manufacturer specific states (0x80-0xBF).
  void add_state(uint8_t id, const char *label);
  // Names the state source / `matter.operational_state.set` use for a state id (normalized: lower case, no separators).
  void add_state_name(const char *name, uint8_t id);
  void add_error_name(const char *name, uint8_t id);
  void add_phase(const char *name);
  void add_command_trigger(MatterOperationalCommand command,
                           MatterOperationalCommandTrigger *trigger);
#ifdef USE_SELECT
  void set_state_select(select::Select *select) { this->state_select_ = select; }
  void set_phase_select(select::Select *select) { this->phase_select_ = select; }
#endif
#ifdef USE_TEXT_SENSOR
  void set_state_text_sensor(text_sensor::TextSensor *sensor) {
    this->state_text_sensor_ = sensor;
  }
  void set_phase_text_sensor(text_sensor::TextSensor *sensor) {
    this->phase_text_sensor_ = sensor;
  }
#endif
#ifdef USE_SENSOR
  void set_countdown_sensor(sensor::Sensor *sensor) {
    this->countdown_sensor_ = sensor;
  }
#endif

  bool has_command(MatterOperationalCommand command) const {
    return !this->triggers_[static_cast<size_t>(command)].empty();
  }

  void initialize() override;

  // Main loop.
  void set_state(const std::string &state_name);
  void set_phase(const std::string &phase_name);
  void raise_error(uint8_t error_id, const std::string &label,
                   const std::string &details);
  void signal_completion(uint8_t error_id, int64_t total_time,
                         int64_t paused_time);

  // OperationalState::Delegate (CHIP task)
  chip::app::DataModel::Nullable<uint32_t> GetCountdownTime() override;
  CHIP_ERROR GetOperationalStateAtIndex(
      size_t index,
      chip::app::Clusters::OperationalState::GenericOperationalState
          &operational_state) override;
  CHIP_ERROR GetOperationalPhaseAtIndex(size_t index,
                                        chip::MutableCharSpan &phase) override;
  void HandlePauseStateCallback(
      chip::app::Clusters::OperationalState::GenericOperationalError &err)
      override;
  void HandleResumeStateCallback(
      chip::app::Clusters::OperationalState::GenericOperationalError &err)
      override;
  void HandleStartStateCallback(
      chip::app::Clusters::OperationalState::GenericOperationalError &err)
      override;
  void HandleStopStateCallback(
      chip::app::Clusters::OperationalState::GenericOperationalError &err)
      override;
  void HandleGoHomeCommandCallback(
      chip::app::Clusters::OperationalState::GenericOperationalError &err)
      override;

  // The delegate pointer in the type the esp-matter init callback of the cluster casts to.
  void *esp_matter_delegate();

protected:
  struct StateEntry {
    uint8_t id;
    std::string label;
  };
  struct NameEntry {
    std::string name;
    uint8_t id;
  };

  void run_command_(MatterOperationalCommand command,
                    chip::app::Clusters::OperationalState::GenericOperationalError &err,
                    uint8_t default_error);
  bool lookup_(const std::vector<NameEntry> &names, const std::string &name,
               uint8_t &id) const;
  void on_state_source_(const std::string &value);
  void on_phase_source_(const std::string &value);
  // Matter thread.
  chip::app::Clusters::OperationalState::Instance *instance_();
  static std::string normalize_(const std::string &text);

  uint32_t cluster_id_;
  std::vector<StateEntry> states_;
  std::vector<NameEntry> state_names_;
  std::vector<NameEntry> error_names_;
  std::vector<std::string> phases_;
  std::vector<MatterOperationalCommandTrigger *> triggers_[5];
#ifdef USE_SELECT
  select::Select *state_select_{nullptr};
  select::Select *phase_select_{nullptr};
#endif
#ifdef USE_TEXT_SENSOR
  text_sensor::TextSensor *state_text_sensor_{nullptr};
  text_sensor::TextSensor *phase_text_sensor_{nullptr};
#endif
#ifdef USE_SENSOR
  sensor::Sensor *countdown_sensor_{nullptr};
#endif
  // -1 = null. Written on the main loop, read on the CHIP task.
  std::atomic<int64_t> countdown_{-1};
};

// Main loop: free functions behind the actions (implemented in matter_operational_state.cpp).
void operational_state_set(uint16_t endpoint_id, uint32_t cluster_id,
                           const std::string &state, const std::string &phase,
                           bool has_phase);
void operational_state_error(uint16_t endpoint_id, uint32_t cluster_id,
                             uint8_t error_id, const std::string &label,
                             const std::string &details);
void operational_state_completed(uint16_t endpoint_id, uint32_t cluster_id,
                                 uint8_t error_id, int64_t total_time,
                                 int64_t paused_time);
void operational_state_reject(uint8_t error_id, const std::string &label,
                              const std::string &details);

// matter.operational_state.set
template <typename... Ts>
class MatterOperationalStateSetAction : public Action<Ts...> {
public:
  void set_endpoint_id(uint16_t endpoint_id) {
    this->endpoint_id_ = endpoint_id;
  }
  void set_cluster_id(uint32_t cluster_id) { this->cluster_id_ = cluster_id; }
  TEMPLATABLE_VALUE(std::string, state)
  TEMPLATABLE_VALUE(std::string, phase)

  void play(const Ts &...x) override {
    std::string state = this->state_.has_value() ? this->state_.value(x...) : "";
    bool has_phase = this->phase_.has_value();
    std::string phase = has_phase ? this->phase_.value(x...) : "";
    operational_state_set(this->endpoint_id_, this->cluster_id_, state, phase,
                          has_phase);
  }

protected:
  uint16_t endpoint_id_{0};
  uint32_t cluster_id_{0};
};

// matter.operational_state.error
template <typename... Ts>
class MatterOperationalStateErrorAction : public Action<Ts...> {
public:
  void set_endpoint_id(uint16_t endpoint_id) {
    this->endpoint_id_ = endpoint_id;
  }
  void set_cluster_id(uint32_t cluster_id) { this->cluster_id_ = cluster_id; }
  TEMPLATABLE_VALUE(uint8_t, error)
  TEMPLATABLE_VALUE(std::string, label)
  TEMPLATABLE_VALUE(std::string, details)

  void play(const Ts &...x) override {
    operational_state_error(
        this->endpoint_id_, this->cluster_id_, this->error_.value(x...),
        this->label_.has_value() ? this->label_.value(x...) : std::string(),
        this->details_.has_value() ? this->details_.value(x...) : std::string());
  }

protected:
  uint16_t endpoint_id_{0};
  uint32_t cluster_id_{0};
};

// matter.operational_state.completed
template <typename... Ts>
class MatterOperationalStateCompletedAction : public Action<Ts...> {
public:
  void set_endpoint_id(uint16_t endpoint_id) {
    this->endpoint_id_ = endpoint_id;
  }
  void set_cluster_id(uint32_t cluster_id) { this->cluster_id_ = cluster_id; }
  TEMPLATABLE_VALUE(uint8_t, error)
  TEMPLATABLE_VALUE(uint32_t, total_operational_time)
  TEMPLATABLE_VALUE(uint32_t, paused_time)

  void play(const Ts &...x) override {
    operational_state_completed(
        this->endpoint_id_, this->cluster_id_,
        this->error_.has_value() ? this->error_.value(x...) : 0,
        this->total_operational_time_.has_value()
            ? static_cast<int64_t>(this->total_operational_time_.value(x...))
            : -1,
        this->paused_time_.has_value()
            ? static_cast<int64_t>(this->paused_time_.value(x...))
            : -1);
  }

protected:
  uint16_t endpoint_id_{0};
  uint32_t cluster_id_{0};
};

// matter.operational_state.reject
template <typename... Ts>
class MatterOperationalStateRejectAction : public Action<Ts...> {
public:
  TEMPLATABLE_VALUE(uint8_t, error)
  TEMPLATABLE_VALUE(std::string, label)
  TEMPLATABLE_VALUE(std::string, details)

  void play(const Ts &...x) override {
    operational_state_reject(
        this->error_.value(x...),
        this->label_.has_value() ? this->label_.value(x...) : std::string(),
        this->details_.has_value() ? this->details_.value(x...) : std::string());
  }
};

} // namespace esphome::matter

#endif // USE_MATTER && USE_MATTER_OPERATIONAL_STATE
