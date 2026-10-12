#include "esphome/core/defines.h"
#if defined(USE_MATTER) && defined(USE_COVER)

#include "esphome/core/log.h"
#include "matter_component.h"
#include "matter_cover.h"

#include <cmath>
#include <cstring>
#include <platform/CHIPDeviceLayer.h>
#include <vector>

static const char *const TAG = "matter.cover";

namespace esphome::matter {

namespace {

namespace wc = chip::app::Clusters::WindowCovering;

// Window Covering Type attribute values.
constexpr uint8_t kTypeRollerShade = 0x00;
constexpr uint8_t kTypeDrapery = 0x04;
constexpr uint8_t kTypeAwning = 0x05;
constexpr uint8_t kTypeShutter = 0x06;
constexpr uint8_t kTypeTiltBlindLiftAndTilt = 0x08;

// EndProductType attribute values.
constexpr uint8_t kEndRollerShade = 0x00;
constexpr uint8_t kEndInteriorBlind = 0x0A;
constexpr uint8_t kEndInteriorVenetianBlind = 0x0C;
constexpr uint8_t kEndCentralCurtain = 0x10;
constexpr uint8_t kEndRollerShutter = 0x11;
constexpr uint8_t kEndSwingingShutter = 0x16;
constexpr uint8_t kEndSlidingShutter = 0x17;
constexpr uint8_t kEndAwningTerracePatio = 0x13;

// ConfigStatus bits.
constexpr uint8_t kConfigOperational = 0x01;

std::vector<MatterCoverMapping *> &registry() {
  static std::vector<MatterCoverMapping *> mappings;
  return mappings;
}

MatterCoverMapping *find_mapping(uint16_t endpoint_id) {
  for (auto *mapping : registry()) {
    if (mapping->endpoint_id() == endpoint_id)
      return mapping;
  }
  ESP_LOGE(TAG, "No cover registered for endpoint %u", endpoint_id);
  return nullptr;
}

bool is_class(const char *device_class, const char *name) {
  return device_class != nullptr && strcmp(device_class, name) == 0;
}

struct CoveringKind {
  uint8_t type;
  uint8_t end_product;
};

CoveringKind classify(bool has_tilt, const char *device_class) {
  if (is_class(device_class, "awning"))
    return {kTypeAwning, kEndAwningTerracePatio};
  if (is_class(device_class, "curtain"))
    return {kTypeDrapery, kEndCentralCurtain};
  if (is_class(device_class, "shutter") || is_class(device_class, "garage"))
    return {kTypeShutter, kEndRollerShutter};
  // Closures exposed as Window Covering (closure_compat, see docs/cover.md).
  if (is_class(device_class, "gate"))
    return {kTypeShutter, kEndSlidingShutter};
  if (is_class(device_class, "door"))
    return {kTypeShutter, kEndSwingingShutter};
  if (has_tilt)
    return {kTypeTiltBlindLiftAndTilt, kEndInteriorVenetianBlind};
  if (is_class(device_class, "blind"))
    return {kTypeRollerShade, kEndInteriorBlind};
  return {kTypeRollerShade, kEndRollerShade};
}

} // namespace

namespace conversion {

bool cover_to_matter(float esphome_value, uint16_t &matter_value) {
  if (!std::isfinite(esphome_value))
    return false;
  float clamped = esphome_value < 0.0f ? 0.0f : (esphome_value > 1.0f ? 1.0f : esphome_value);
  matter_value = static_cast<uint16_t>(std::lround((1.0f - clamped) * kCoverMatterClosed));
  return true;
}

float cover_from_matter(uint16_t matter_value) {
  if (matter_value > kCoverMatterClosed)
    matter_value = kCoverMatterClosed;
  return 1.0f - static_cast<float>(matter_value) / static_cast<float>(kCoverMatterClosed);
}

} // namespace conversion

void MatterComponent::register_cover(cover::Cover *cover, uint16_t endpoint_id) {
  this->mappings_.push_back(new MatterCoverMapping(cover, endpoint_id));
}

MatterCoverMapping::MatterCoverMapping(cover::Cover *cover, uint16_t endpoint_id)
    : MatterEndpointMappingBase(endpoint_id), cover_(cover) {
  this->SetEndpoint(endpoint_id);
  registry().push_back(this);
}

uint8_t window_covering_end_product_type(uint16_t endpoint_id, const char *device_class,
                                         int end_product_type) {
  auto *mapping = find_mapping(endpoint_id);
  return mapping == nullptr ? kEndRollerShade : mapping->end_product_type(device_class, end_product_type);
}

void configure_window_covering(esp_matter::cluster::window_covering::config_t &config,
                               uint16_t endpoint_id, const char *device_class) {
  if (auto *mapping = find_mapping(endpoint_id))
    mapping->configure(config, device_class);
}

uint8_t MatterCoverMapping::end_product_type(const char *device_class, int end_product_type) const {
  if (end_product_type >= 0)
    return static_cast<uint8_t>(end_product_type);
  return classify(this->cover_->get_traits().get_supports_tilt(), device_class).end_product;
}

void MatterCoverMapping::configure(esp_matter::cluster::window_covering::config_t &config,
                                   const char *device_class) {
  namespace espm = esp_matter::cluster::window_covering;

  auto traits = this->cover_->get_traits();
  this->has_position_ = traits.get_supports_position();
  this->has_tilt_ = traits.get_supports_tilt();
  this->has_stop_ = traits.get_supports_stop();

  // connectedhomeip only calls the delegate for position aware features, so a
  // cover without position support is exposed as position aware too. It then
  // only ever reports fully open or fully closed, and in-between targets are
  // rounded to the closest end. esp-matter sets the ConfigStatus position
  // aware bits when it adds the features.
  uint32_t flags = espm::feature::lift::get_id() | espm::feature::position_aware_lift::get_id();
  if (this->has_tilt_)
    flags |= espm::feature::tilt::get_id() | espm::feature::position_aware_tilt::get_id();
  config.feature_flags |= flags;
  config.config_status = kConfigOperational;
  config.type = classify(this->has_tilt_, device_class).type;
  config.delegate = static_cast<void *>(static_cast<wc::Delegate *>(this));

  // Only the position aware features have attributes to initialise. The values
  // are null until the cover knows its position.
  uint16_t lift = 0;
  nullable<uint16_t> lift_value = conversion::cover_to_matter(this->cover_->position, lift)
                                      ? nullable<uint16_t>(lift)
                                      : nullable<uint16_t>();
  config.features.position_aware_lift.current_position_lift_percent_100ths = lift_value;
  config.features.position_aware_lift.target_position_lift_percent_100ths = lift_value;
  if (this->has_tilt_) {
    uint16_t tilt = 0;
    nullable<uint16_t> tilt_value = conversion::cover_to_matter(this->cover_->tilt, tilt)
                                        ? nullable<uint16_t>(tilt)
                                        : nullable<uint16_t>();
    config.features.position_aware_tilt.current_position_tilt_percent_100ths = tilt_value;
    config.features.position_aware_tilt.target_position_tilt_percent_100ths = tilt_value;
  }
}

void MatterCoverMapping::initialize() {
  if (this->cover_ == nullptr)
    return;

  auto traits = this->cover_->get_traits();
  this->has_position_ = traits.get_supports_position();
  this->has_tilt_ = traits.get_supports_tilt();
  this->has_stop_ = traits.get_supports_stop();

  this->cover_->add_on_state_callback([this]() { this->on_cover_state_(); });
  this->push_state_to_matter_();
}

// ---------------------------------------------------------------------------
// Matter -> ESPHome
// ---------------------------------------------------------------------------

// connectedhomeip sets the TargetPosition attribute, then calls HandleMovement
// for every position aware axis, for UpOrOpen, DownOrClose and GoTo*Percentage.
// The target attributes are not writable by clients, so this is the only entry.
CHIP_ERROR MatterCoverMapping::HandleMovement(wc::WindowCoveringType type) {
  bool lift = type == wc::WindowCoveringType::Lift;
  if (!lift && !this->has_tilt_)
    return CHIP_ERROR_NOT_IMPLEMENTED;

  esp_matter_attr_val_t value;
  uint32_t attribute_id = lift ? wc::Attributes::TargetPositionLiftPercent100ths::Id
                               : wc::Attributes::TargetPositionTiltPercent100ths::Id;
  if (esp_matter::attribute::get_val(this->endpoint_id(), wc::Id, attribute_id, &value) != ESP_OK ||
      value.is_null())
    return CHIP_ERROR_INCORRECT_STATE;

  uint16_t target = value.val.u16;
  global_matter_component->defer_to_main_loop([this, lift, target]() { this->request_(lift, target); });
  return CHIP_NO_ERROR;
}

CHIP_ERROR MatterCoverMapping::HandleStopMotion() {
  global_matter_component->defer_to_main_loop([this]() { this->stop_(); });
  // IN_PROGRESS keeps connectedhomeip from resetting the target to a possibly
  // stale current position. stop_() reports the final positions and targets
  // when the cover has stopped.
  return this->has_stop_ ? CHIP_ERROR_IN_PROGRESS : CHIP_ERROR_NOT_IMPLEMENTED;
}

void MatterCoverMapping::request_(bool lift, uint16_t target) {
  if (lift) {
    this->pending_lift_ = target;
    this->target_lift_ = target;
  } else {
    this->pending_tilt_ = target;
    this->target_tilt_ = target;
  }
  this->schedule_drain_();
}

// Lift and tilt requests that arrive in the same loop iteration (UpOrOpen and
// DownOrClose request both) are combined into a single cover call.
void MatterCoverMapping::schedule_drain_() {
  if (this->drain_scheduled_)
    return;
  this->drain_scheduled_ = true;
  global_matter_component->defer_to_main_loop([this]() { this->drain_(); });
}

void MatterCoverMapping::drain_() {
  this->drain_scheduled_ = false;
  auto call = this->cover_->make_call();
  bool any = false;
  if (this->pending_lift_.has_value()) {
    uint16_t target = *this->pending_lift_;
    this->pending_lift_.reset();
    if (target == conversion::kCoverMatterOpen)
      call.set_command_open();
    else if (target >= conversion::kCoverMatterClosed)
      call.set_command_close();
    else if (this->has_position_)
      call.set_position(conversion::cover_from_matter(target));
    else if (target < conversion::kCoverMatterClosed / 2)
      call.set_command_open();
    else
      call.set_command_close();
    any = true;
  }
  if (this->pending_tilt_.has_value()) {
    uint16_t target = *this->pending_tilt_;
    this->pending_tilt_.reset();
    call.set_tilt(conversion::cover_from_matter(target));
    any = true;
  }
  if (any)
    call.perform();
}

void MatterCoverMapping::stop_() {
  this->pending_lift_.reset();
  this->pending_tilt_.reset();
  this->target_lift_.reset();
  this->target_tilt_.reset();
  if (this->has_stop_)
    this->cover_->make_call().set_command_stop().perform();
  // The cover might not publish a state when it was already idle.
  this->push_state_to_matter_();
}

// ---------------------------------------------------------------------------
// ESPHome -> Matter
// ---------------------------------------------------------------------------

void MatterCoverMapping::on_cover_state_() { this->push_state_to_matter_(); }

void MatterCoverMapping::push_state_to_matter_() {
  uint16_t lift = 0;
  uint16_t tilt = 0;
  bool lift_known = conversion::cover_to_matter(this->cover_->position, lift);
  bool tilt_known = this->has_tilt_ && conversion::cover_to_matter(this->cover_->tilt, tilt);

  uint8_t state = 0; // OperationalState::Stall
  if (this->cover_->current_operation == cover::COVER_OPERATION_OPENING)
    state = 1; // MovingUpOrOpen
  else if (this->cover_->current_operation == cover::COVER_OPERATION_CLOSING)
    state = 2; // MovingDownOrClose

  // ESPHome has no target position. At rest the target is the current
  // position; while moving it is the last Matter request, or the end of the
  // direction of travel when ESPHome started the movement itself.
  if (state == 0) {
    this->target_lift_.reset();
    this->target_tilt_.reset();
  }
  uint16_t lift_target = lift;
  if (state != 0 && lift_known) {
    bool consistent = this->target_lift_.has_value() &&
                      (state == 1 ? *this->target_lift_ <= lift : *this->target_lift_ >= lift);
    lift_target = consistent ? *this->target_lift_
                             : (state == 1 ? conversion::kCoverMatterOpen : conversion::kCoverMatterClosed);
  }
  uint16_t tilt_target = this->target_tilt_.has_value() ? *this->target_tilt_ : tilt;

  uint16_t eid = this->endpoint_id();
  bool has_tilt = this->has_tilt_;
  // Global (bits 0-1) and lift (bits 2-3) follow the cover operation. Tilt (bits
  // 4-5) is left at stopped because ESPHome has a single operation.
  uint8_t operational_status = static_cast<uint8_t>(state | (state << 2));

  chip::DeviceLayer::SystemLayer().ScheduleLambda(
      [eid, has_tilt, lift_known, lift, lift_target, tilt_known, tilt, tilt_target, operational_status]() {
        auto report_position = [eid](uint32_t attribute_id, bool known, uint16_t value) {
          esp_matter_attr_val_t val =
              esp_matter_nullable_uint16(known ? nullable<uint16_t>(value) : nullable<uint16_t>());
          esp_matter::attribute::report(eid, wc::Id, attribute_id, &val);
        };
        report_position(wc::Attributes::TargetPositionLiftPercent100ths::Id, lift_known, lift_target);
        report_position(wc::Attributes::CurrentPositionLiftPercent100ths::Id, lift_known, lift);
        if (has_tilt) {
          report_position(wc::Attributes::TargetPositionTiltPercent100ths::Id, tilt_known, tilt_target);
          report_position(wc::Attributes::CurrentPositionTiltPercent100ths::Id, tilt_known, tilt);
        }
        esp_matter_attr_val_t status_val = esp_matter_bitmap8(operational_status);
        esp_matter::attribute::report(eid, wc::Id, wc::Attributes::OperationalStatus::Id, &status_val);
      });
}

} // namespace esphome::matter

#endif // USE_MATTER && USE_COVER
