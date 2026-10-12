#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_MATTER_OPERATIONAL_STATE)

#include "esphome/core/log.h"
#include "matter_attributes.h"
#include "matter_component.h"
#include "matter_operational_state.h"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <esp_matter_cluster.h>
#include <esp_matter_command.h>
#include <esp_matter_data_model.h>
#include <esp_matter_delegate_callbacks.h>
#include <esp_matter_event.h>
#include <lib/support/Span.h>

static const char *const TAG = "matter.opstate";

namespace esphome::matter {

namespace {

namespace opstate_ns = chip::app::Clusters::OperationalState;
namespace rvc_ns = chip::app::Clusters::RvcOperationalState;

constexpr uint32_t OPERATIONAL_STATE_ID = 0x0060;
constexpr uint32_t RVC_OPERATIONAL_STATE_ID = 0x0061;

constexpr size_t LABEL_MAX = 64;

std::vector<MatterOperationalStateMapping *> &opstate_mappings() {
  static std::vector<MatterOperationalStateMapping *> mappings;
  return mappings;
}

chip::CharSpan span_of(const std::string &text) {
  return chip::CharSpan(text.c_str(), utf8_prefix_length(text, LABEL_MAX));
}

} // namespace

MatterOperationalStateMapping::MatterOperationalStateMapping(
    uint16_t endpoint_id, uint32_t cluster_id)
    : MatterBinding<MatterOpStateSnapshot, NoInbound>(
          endpoint_id, BindingPolicy{BootSync::ESPHOME_WINS, 0, false}),
      cluster_id_(cluster_id),
      events_([this](uint16_t, const MatterOpEvent &event) {
        this->emit_event_(event);
      }) {
  opstate_mappings().push_back(this);
}

MatterOperationalStateMapping *
MatterOperationalStateMapping::find(uint16_t endpoint_id, uint32_t cluster_id) {
  for (auto *mapping : opstate_mappings()) {
    if (mapping->endpoint_id() == endpoint_id &&
        mapping->cluster_id() == cluster_id)
      return mapping;
  }
  return nullptr;
}

std::string MatterOperationalStateMapping::normalize_(const std::string &text) {
  std::string result;
  for (char c : text) {
    if (c == '_' || c == ' ' || c == '-')
      continue;
    result.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
  }
  return result;
}

void MatterOperationalStateMapping::add_state(uint8_t id, const char *label) {
  this->states_.push_back({id, label});
}

void MatterOperationalStateMapping::add_state_name(const char *name, uint8_t id) {
  this->state_names_.push_back({normalize_(name), id});
}

void MatterOperationalStateMapping::add_error_name(const char *name, uint8_t id) {
  this->error_names_.push_back({normalize_(name), id});
}

void MatterOperationalStateMapping::add_phase(const char *name) {
  this->phases_.push_back(name);
}

void MatterOperationalStateMapping::add_command_trigger(
    MatterOperationalCommand command, MatterOperationalCommandTrigger *trigger) {
  this->triggers_[static_cast<size_t>(command)].push_back(trigger);
}

bool MatterOperationalStateMapping::lookup_(const std::vector<NameEntry> &names,
                                            const std::string &name,
                                            uint8_t &id) const {
  std::string normalized = normalize_(name);
  for (const auto &entry : names) {
    if (entry.name == normalized) {
      id = entry.id;
      return true;
    }
  }
  return false;
}

void *MatterOperationalStateMapping::esp_matter_delegate() {
  if (this->cluster_id_ == RVC_OPERATIONAL_STATE_ID)
    return static_cast<rvc_ns::Delegate *>(this);
  return static_cast<opstate_ns::Delegate *>(this);
}

// ---------------------------------------------------------------- delegate

chip::app::DataModel::Nullable<uint32_t>
MatterOperationalStateMapping::GetCountdownTime() {
  int64_t value = this->countdown_.load();
  if (value < 0)
    return chip::app::DataModel::NullNullable;
  return chip::app::DataModel::MakeNullable(static_cast<uint32_t>(value));
}

CHIP_ERROR MatterOperationalStateMapping::GetOperationalStateAtIndex(
    size_t index, opstate_ns::GenericOperationalState &operational_state) {
  if (index >= this->states_.size())
    return CHIP_ERROR_NOT_FOUND;
  const StateEntry &entry = this->states_[index];
  if (entry.label.empty())
    operational_state.Set(entry.id);
  else
    operational_state.Set(
        entry.id, chip::MakeOptional(span_of(entry.label)));
  return CHIP_NO_ERROR;
}

CHIP_ERROR MatterOperationalStateMapping::GetOperationalPhaseAtIndex(
    size_t index, chip::MutableCharSpan &phase) {
  if (index >= this->phases_.size())
    return CHIP_ERROR_NOT_FOUND;
  return chip::CopyCharSpanToMutableCharSpan(span_of(this->phases_[index]),
                                             phase);
}

bool MatterOperationalStateMapping::is_idle() const {
  if (this->idle_states_.empty())
    return true;
  int state = this->current_state_.load();
  for (uint8_t idle : this->idle_states_) {
    if (idle == state)
      return true;
  }
  return false;
}

bool endpoint_is_idle(uint16_t endpoint_id) {
  for (auto *mapping : opstate_mappings()) {
    if (mapping->endpoint_id() == endpoint_id)
      return mapping->is_idle();
  }
  return true;
}

void MatterOperationalStateMapping::run_command_(
    MatterOperationalCommand command, opstate_ns::GenericOperationalError &err,
    uint8_t default_error) {
  const size_t index = static_cast<size_t>(command);
  // Declarative rule, decided here without waiting for the main loop.
  const auto &allowed = this->allowed_states_[index];
  if (!allowed.empty()) {
    int state = this->current_state_.load();
    bool ok = false;
    for (uint8_t value : allowed)
      ok = ok || value == state;
    if (!ok) {
      err.Set(chip::to_underlying(opstate_ns::ErrorStateEnum::kCommandInvalidInState),
              chip::MakeOptional(chip::CharSpan::fromCharString("Not allowed in the current state")));
      return;
    }
  }
  const auto &triggers = this->triggers_[index];
  if (triggers.empty()) {
    err.Set(default_error);
    return;
  }
  if (!this->decide_in_automation_) {
    // Default: accept and run the automations asynchronously.
    defer_to_main_loop(global_matter_component, [&triggers]() {
      for (auto *trigger : triggers)
        trigger->trigger();
    });
    return;
  }
  MatterCommandOutcome outcome;
  bool answered = run_on_main_loop_and_wait(
      [&triggers](MatterCommandOutcome &) {
        for (auto *trigger : triggers)
          trigger->trigger();
      },
      outcome, this->decide_timeout_ms_,
      [](const MatterCommandOutcome &late) {
        // The state follows the state source; the controller was told the command failed.
        ESP_LOGW(TAG, "Command finished late%s; the state source decides the state",
                 late.rejected ? " and was rejected" : "");
      });
  if (!answered) {
    err.Set(default_error,
            chip::MakeOptional(chip::CharSpan::fromCharString("Timed out")));
  } else if (outcome.rejected) {
    err.Set(outcome.status,
            outcome.text.empty() ? chip::MakeOptional(chip::CharSpan::fromCharString("Rejected"))
                                 : chip::MakeOptional(span_of(outcome.text)),
            outcome.details.empty() ? chip::NullOptional
                                    : chip::MakeOptional(span_of(outcome.details)));
  }
}

void MatterOperationalStateMapping::HandlePauseStateCallback(
    opstate_ns::GenericOperationalError &err) {
  this->run_command_(MatterOperationalCommand::PAUSE, err,
                     chip::to_underlying(opstate_ns::ErrorStateEnum::kCommandInvalidInState));
}

void MatterOperationalStateMapping::HandleResumeStateCallback(
    opstate_ns::GenericOperationalError &err) {
  this->run_command_(MatterOperationalCommand::RESUME, err,
                     chip::to_underlying(opstate_ns::ErrorStateEnum::kUnableToStartOrResume));
}

void MatterOperationalStateMapping::HandleStartStateCallback(
    opstate_ns::GenericOperationalError &err) {
  this->run_command_(MatterOperationalCommand::START, err,
                     chip::to_underlying(opstate_ns::ErrorStateEnum::kUnableToStartOrResume));
}

void MatterOperationalStateMapping::HandleStopStateCallback(
    opstate_ns::GenericOperationalError &err) {
  this->run_command_(MatterOperationalCommand::STOP, err,
                     chip::to_underlying(opstate_ns::ErrorStateEnum::kUnableToCompleteOperation));
}

void MatterOperationalStateMapping::HandleGoHomeCommandCallback(
    opstate_ns::GenericOperationalError &err) {
  this->run_command_(MatterOperationalCommand::GO_HOME, err,
                     chip::to_underlying(opstate_ns::ErrorStateEnum::kCommandInvalidInState));
}

// ------------------------------------------------------- ESPHome -> Matter

opstate_ns::Instance *MatterOperationalStateMapping::instance_() {
  auto *cluster =
      esp_matter::cluster::get(this->endpoint_id(), this->cluster_id_);
  if (cluster == nullptr)
    return nullptr;
  void *managed = esp_matter::cluster::get_delegate_managed_instance(cluster);
  if (this->cluster_id_ == RVC_OPERATIONAL_STATE_ID)
    return static_cast<rvc_ns::Instance *>(managed);
  return static_cast<opstate_ns::Instance *>(managed);
}

void MatterOperationalStateMapping::setup_() {
#ifdef USE_SENSOR
  if (this->countdown_sensor_ != nullptr) {
    auto update = [this](float value) {
      // CountdownTime is at most 259200 s (3 days).
      int64_t seconds = std::isfinite(value) && value >= 0
                            ? std::min<int64_t>(static_cast<int64_t>(std::lround(value)), 259200)
                            : -1;
      if (seconds == this->countdown_.exchange(seconds))
        return;
      this->request_push();
    };
    this->countdown_sensor_->add_on_state_callback(update);
    if (this->countdown_sensor_->has_state())
      update(this->countdown_sensor_->state);
  }
#endif
#ifdef USE_SELECT
  if (this->state_select_ != nullptr) {
    this->state_select_->add_on_state_callback([this](auto &&...) {
      auto index = this->state_select_->active_index();
      if (!index.has_value())
        return;
      auto option = this->state_select_->at(index.value());
      if (option.has_value())
        this->on_state_source_(option.value());
    });
  }
  if (this->phase_select_ != nullptr) {
    this->phase_select_->add_on_state_callback([this](auto &&...) {
      auto index = this->phase_select_->active_index();
      if (!index.has_value())
        return;
      auto option = this->phase_select_->at(index.value());
      if (option.has_value())
        this->on_phase_source_(option.value());
    });
  }
#endif
#ifdef USE_TEXT_SENSOR
  if (this->state_text_sensor_ != nullptr)
    this->state_text_sensor_->add_on_state_callback(
        [this](const std::string &value) { this->on_state_source_(value); });
  if (this->phase_text_sensor_ != nullptr)
    this->phase_text_sensor_->add_on_state_callback(
        [this](const std::string &value) { this->on_phase_source_(value); });
#endif
  // Push the restored state of the sources (read on the main loop).
  binding_platform()->defer_main([this]() { this->push_source_states_(); });
}

void MatterOperationalStateMapping::push_source_states_() {
#ifdef USE_SELECT
  if (this->state_select_ != nullptr && this->state_select_->has_state()) {
    auto index = this->state_select_->active_index();
    if (index.has_value()) {
      auto option = this->state_select_->at(index.value());
      if (option.has_value())
        this->on_state_source_(option.value());
    }
  }
  if (this->phase_select_ != nullptr && this->phase_select_->has_state()) {
    auto index = this->phase_select_->active_index();
    if (index.has_value()) {
      auto option = this->phase_select_->at(index.value());
      if (option.has_value())
        this->on_phase_source_(option.value());
    }
  }
#endif
#ifdef USE_TEXT_SENSOR
  if (this->state_text_sensor_ != nullptr &&
      this->state_text_sensor_->has_state())
    this->on_state_source_(this->state_text_sensor_->state);
  if (this->phase_text_sensor_ != nullptr &&
      this->phase_text_sensor_->has_state())
    this->on_phase_source_(this->phase_text_sensor_->state);
#endif
}

void MatterOperationalStateMapping::on_state_source_(const std::string &value) {
  this->set_state(value);
}

void MatterOperationalStateMapping::on_phase_source_(const std::string &value) {
  this->set_phase(value);
}

// Main loop.
void MatterOperationalStateMapping::set_state(const std::string &state_name) {
  uint8_t id = 0;
  if (!this->lookup_(this->state_names_, state_name, id)) {
    ESP_LOGW(TAG, "Unknown operational state '%s' on endpoint %u cluster 0x%04x",
             state_name.c_str(), this->endpoint_id(),
             static_cast<unsigned>(this->cluster_id_));
    return;
  }
  this->current_state_.store(id);
  this->desired_state_ = id;
  this->state_from_event_ = false;
  this->request_push();
}

// Main loop.
void MatterOperationalStateMapping::set_phase(const std::string &phase_name) {
  int16_t phase = -1;
  if (!phase_name.empty()) {
    std::string wanted = normalize_(phase_name);
    bool found = false;
    for (size_t i = 0; i < this->phases_.size(); i++) {
      if (normalize_(this->phases_[i]) == wanted) {
        phase = static_cast<int16_t>(i);
        found = true;
        break;
      }
    }
    if (!found && wanted != "none" && wanted != "null") {
      ESP_LOGW(TAG, "Unknown phase '%s' on endpoint %u cluster 0x%04x",
               phase_name.c_str(), this->endpoint_id(),
               static_cast<unsigned>(this->cluster_id_));
      return;
    }
  }
  this->desired_phase_ = phase;
  this->request_push();
}

// Main loop.
MatterOpStateSnapshot MatterOperationalStateMapping::capture_() {
  MatterOpStateSnapshot snapshot;
  // The Error state entered by raise_error() is written by its OperationalError event (which carries the error).
  snapshot.state = this->state_from_event_ ? -1 : this->desired_state_;
  snapshot.phase = this->desired_phase_;
  snapshot.countdown = this->countdown_.load();
  return snapshot;
}

// CHIP task, inside the PushScope.
void MatterOperationalStateMapping::write_matter_(
    const MatterOpStateSnapshot &s) {
  auto *instance = this->instance_();
  if (instance == nullptr) {
    ESP_LOGW(TAG, "Operational state cluster 0x%04x on endpoint %u is not running",
             static_cast<unsigned>(this->cluster_id_), this->endpoint_id());
    return;
  }
  if (s.state >= 0) {
    const uint8_t id = static_cast<uint8_t>(s.state);
    if (id == chip::to_underlying(opstate_ns::OperationalStateEnum::kError)) {
      // The Error state can only be entered by reporting an OperationalError (matter.operational_state.error does that
      // with a specific error). A bare "error" state without one reports a generic error.
      if (instance->GetCurrentOperationalState() != id) {
        opstate_ns::Structs::ErrorStateStruct::Type error;
        error.errorStateID = chip::to_underlying(
            opstate_ns::ErrorStateEnum::kUnableToCompleteOperation);
        instance->OnOperationalErrorDetected(error);
      }
    } else if (instance->SetOperationalState(id) != CHIP_NO_ERROR) {
      ESP_LOGW(TAG, "Operational state %u rejected by endpoint %u cluster 0x%04x",
               id, this->endpoint_id(), static_cast<unsigned>(this->cluster_id_));
    }
  }
  if (s.phase != -2) {
    chip::app::DataModel::Nullable<uint8_t> phase = chip::app::DataModel::NullNullable;
    if (s.phase >= 0)
      phase = chip::app::DataModel::MakeNullable(static_cast<uint8_t>(s.phase));
    if (instance->SetCurrentPhase(phase) != CHIP_NO_ERROR)
      ESP_LOGW(TAG, "Current phase rejected by endpoint %u cluster 0x%04x",
               this->endpoint_id(), static_cast<unsigned>(this->cluster_id_));
  }
  if (s.countdown != this->written_countdown_) {
    this->written_countdown_ = s.countdown;
    // The Instance reads the value from GetCountdownTime() and reports a change.
    instance->UpdateCountdownTimeFromDelegate();
  }
}

// Main loop.
void MatterOperationalStateMapping::raise_error(uint8_t error_id,
                                                const std::string &label,
                                                const std::string &details) {
  this->current_state_.store(
      chip::to_underlying(opstate_ns::OperationalStateEnum::kError));
  this->desired_state_ =
      chip::to_underlying(opstate_ns::OperationalStateEnum::kError);
  this->state_from_event_ = true;
  MatterOpEvent event;
  event.error_id = error_id;
  event.label = label;
  event.details = details;
  this->events_.post(this->endpoint_id(), event);
  // Refresh the outbound slot: a state captured before the error must not run after the event and undo it.
  this->request_push();
}

// Main loop.
void MatterOperationalStateMapping::signal_completion(uint8_t error_id,
                                                      int64_t total_time,
                                                      int64_t paused_time) {
  MatterOpEvent event;
  event.completion = true;
  event.error_id = error_id;
  event.total_time = total_time;
  event.paused_time = paused_time;
  this->events_.post(this->endpoint_id(), event);
}

// CHIP task, in order with the events posted before it.
void MatterOperationalStateMapping::emit_event_(const MatterOpEvent &event) {
  auto *instance = this->instance_();
  if (instance == nullptr)
    return;
  PushScope scope(this->endpoint_id());
  if (event.completion) {
    chip::Optional<chip::app::DataModel::Nullable<uint32_t>> total;
    chip::Optional<chip::app::DataModel::Nullable<uint32_t>> paused;
    if (event.total_time >= 0)
      total.SetValue(chip::app::DataModel::MakeNullable(static_cast<uint32_t>(event.total_time)));
    if (event.paused_time >= 0)
      paused.SetValue(chip::app::DataModel::MakeNullable(static_cast<uint32_t>(event.paused_time)));
    instance->OnOperationCompletionDetected(event.error_id, total, paused);
    return;
  }
  // Already in this error (a state source reported "error" first, or this action ran twice): the
  // OperationalError event was sent for it, do not send a duplicate.
  opstate_ns::GenericOperationalError current(0);
  instance->GetCurrentOperationalError(current);
  if (instance->GetCurrentOperationalState() ==
          chip::to_underlying(opstate_ns::OperationalStateEnum::kError) &&
      current.errorStateID == event.error_id)
    return;
  opstate_ns::Structs::ErrorStateStruct::Type error;
  error.errorStateID = event.error_id;
  if (!event.label.empty())
    error.errorStateLabel.SetValue(span_of(event.label));
  if (!event.details.empty())
    error.errorStateDetails.SetValue(span_of(event.details));
  instance->OnOperationalErrorDetected(error);
}

// --------------------------------------------------------------------- actions

namespace {
MatterOperationalStateMapping *find_or_warn(uint16_t endpoint_id,
                                            uint32_t cluster_id) {
  auto *mapping = MatterOperationalStateMapping::find(endpoint_id, cluster_id);
  if (mapping == nullptr)
    ESP_LOGW(TAG, "No operational state cluster 0x%04x configured on endpoint %u",
             static_cast<unsigned>(cluster_id), endpoint_id);
  return mapping;
}
} // namespace

void operational_state_set(uint16_t endpoint_id, uint32_t cluster_id,
                           const std::string &state, const std::string &phase,
                           bool has_phase) {
  auto *mapping = find_or_warn(endpoint_id, cluster_id);
  if (mapping == nullptr)
    return;
  if (has_phase)
    mapping->set_phase(phase);
  if (!state.empty())
    mapping->set_state(state);
}

void operational_state_error(uint16_t endpoint_id, uint32_t cluster_id,
                             uint8_t error_id, const std::string &label,
                             const std::string &details) {
  if (auto *mapping = find_or_warn(endpoint_id, cluster_id))
    mapping->raise_error(error_id, label, details);
}

void operational_state_completed(uint16_t endpoint_id, uint32_t cluster_id,
                                 uint8_t error_id, int64_t total_time,
                                 int64_t paused_time) {
  if (auto *mapping = find_or_warn(endpoint_id, cluster_id))
    mapping->signal_completion(error_id, total_time, paused_time);
}

void operational_state_reject(uint8_t error_id, const std::string &label,
                              const std::string &details) {
  MatterCommandOutcome *outcome = active_command_outcome();
  if (outcome == nullptr) {
    ESP_LOGW(TAG, "matter.operational_state.reject ignored: no command is "
                  "being handled (use it directly in on_start/on_stop/"
                  "on_pause/on_resume/on_go_home, before any delay)");
    return;
  }
  outcome->rejected = true;
  outcome->status = error_id;
  outcome->text = label;
  outcome->details = details;
}

// ---------------------------------------------------------------------- attach

DelegateAttach attach_operational_state_delegate(esp_matter::endpoint_t *endpoint,
                                                 uint32_t cluster_id) {
  if (cluster_id != OPERATIONAL_STATE_ID && cluster_id != RVC_OPERATIONAL_STATE_ID)
    return DelegateAttach::NOT_HANDLED;
  const uint16_t endpoint_id = esp_matter::endpoint::get_id(endpoint);
  auto *mapping = MatterOperationalStateMapping::find(endpoint_id, cluster_id);
  if (mapping == nullptr)
    return DelegateAttach::NOT_HANDLED;

  esp_matter::cluster_t *cluster = esp_matter::cluster::get(endpoint, cluster_id);
  if (cluster == nullptr) {
    ESP_LOGE(TAG, "Operational state cluster 0x%04x missing on endpoint %u",
             static_cast<unsigned>(cluster_id), endpoint_id);
    return DelegateAttach::FAILED;
  }

  using namespace esp_matter;
  using Cmd = MatterOperationalCommand;
  // The OperationalState::Instance serves the commands, but only the ones in the data model are advertised.
  bool any_command = false;
  if (mapping->has_command(Cmd::PAUSE)) {
    cluster::operational_state::command::create_pause(cluster);
    any_command = true;
  }
  if (mapping->has_command(Cmd::RESUME)) {
    cluster::operational_state::command::create_resume(cluster);
    any_command = true;
  }
  if (cluster_id == OPERATIONAL_STATE_ID) {
    if (mapping->has_command(Cmd::START)) {
      cluster::operational_state::command::create_start(cluster);
      any_command = true;
    }
    if (mapping->has_command(Cmd::STOP)) {
      cluster::operational_state::command::create_stop(cluster);
      any_command = true;
    }
  } else if (mapping->has_command(Cmd::GO_HOME)) {
    // esp-matter has no creator for GoHome (0x80); the command id is RvcOperationalState::Commands::GoHome.
    command::create(cluster, rvc_ns::Commands::GoHome::Id, COMMAND_FLAG_ACCEPTED, nullptr);
    any_command = true;
  }
  if (any_command &&
      command::get(cluster, opstate_ns::Commands::OperationalCommandResponse::Id,
                   COMMAND_FLAG_GENERATED) == nullptr)
    cluster::operational_state::command::create_operational_command_response(cluster);

  // CountdownTime is optional; the SDK serves it from the delegate once the attribute exists.
  if (mapping->has_countdown() &&
      attribute::get(cluster, opstate_ns::Attributes::CountdownTime::Id) == nullptr)
    cluster::operational_state::attribute::create_countdown_time(
        cluster, nullable<uint32_t>());

  // Events: OperationalError is mandatory, OperationCompletion is optional in the XML but required by the washer,
  // dishwasher and RVC device types, so both are created. The plain Operational State creator already makes
  // OperationalError.
  if (event::get(cluster, opstate_ns::Events::OperationalError::Id) == nullptr)
    cluster::operational_state::event::create_operational_error(cluster);
  if (event::get(cluster, opstate_ns::Events::OperationCompletion::Id) == nullptr)
    cluster::operational_state::event::create_operation_completion(cluster);

  esp_matter::cluster::delegate_init_callback_t init_callback =
      cluster_id == RVC_OPERATIONAL_STATE_ID
          ? esp_matter::cluster::delegate_cb::RvcOperationalStateDelegateInitCB
          : esp_matter::cluster::delegate_cb::OperationalStateDelegateInitCB;
  // On a microwave oven the Microwave Oven Control cluster creates (and Init()s) the operational state instance itself
  // once it has a delegate; only hand over the delegate pointer then, to avoid a second Init().
  if (cluster_id == OPERATIONAL_STATE_ID) {
    esp_matter::cluster_t *control = esp_matter::cluster::get(endpoint, 0x005F);
    if (control != nullptr &&
        esp_matter::cluster::get_delegate_impl(control) != nullptr)
      init_callback = nullptr;
  }
  if (cluster::set_delegate_and_init_callback(cluster, init_callback,
                                              mapping->esp_matter_delegate()) != ESP_OK)
    return DelegateAttach::FAILED;
  return DelegateAttach::OK;
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_MATTER_OPERATIONAL_STATE
