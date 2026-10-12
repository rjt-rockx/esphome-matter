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

void MatterComponent::register_cover(cover::Cover *cover, uint16_t endpoint_id) {
  this->mappings_.push_back(new MatterCoverMapping(cover, endpoint_id));
}

MatterCoverMapping::MatterCoverMapping(cover::Cover *cover, uint16_t endpoint_id)
    : MatterCoverBinding(endpoint_id), cover_(cover) {
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

void MatterCoverMapping::setup_() {
  MatterCoverBinding::setup_();
  if (this->cover_ == nullptr)
    return;

  auto traits = this->cover_->get_traits();
  this->has_position_ = traits.get_supports_position();
  this->has_tilt_ = traits.get_supports_tilt();
  this->has_stop_ = traits.get_supports_stop();

  this->cover_->add_on_state_callback([this]() { this->on_entity_state_(); });
}

CoverEntityView MatterCoverMapping::entity_() {
  CoverEntityView v;
  if (this->cover_ == nullptr)
    return v;
  v.position = this->cover_->position;
  v.tilt = this->cover_->tilt;
  switch (this->cover_->current_operation) {
  case cover::COVER_OPERATION_OPENING:
    v.operation = CoverEntityOperation::OPENING;
    break;
  case cover::COVER_OPERATION_CLOSING:
    v.operation = CoverEntityOperation::CLOSING;
    break;
  default:
    break;
  }
  return v;
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

  this->submit_movement(lift, value.val.u16);
  return CHIP_NO_ERROR;
}

CHIP_ERROR MatterCoverMapping::HandleStopMotion() {
  this->submit_stop();
  // IN_PROGRESS keeps connectedhomeip from resetting the target to a possibly
  // stale current position. The stop pushes the final positions and targets
  // when the cover has stopped.
  return this->has_stop_ ? CHIP_ERROR_IN_PROGRESS : CHIP_ERROR_NOT_IMPLEMENTED;
}

// Lift and tilt requests that arrive in the same loop iteration (UpOrOpen and
// DownOrClose request both) are combined into a single cover call.
void MatterCoverMapping::command_move_(bool has_lift, uint16_t lift, bool has_tilt, uint16_t tilt) {
  auto call = this->cover_->make_call();
  if (has_lift) {
    if (lift == conversion::kCoverMatterOpen)
      call.set_command_open();
    else if (lift >= conversion::kCoverMatterClosed)
      call.set_command_close();
    else if (this->has_position_)
      call.set_position(conversion::cover_from_matter(lift));
    else if (lift < conversion::kCoverMatterClosed / 2)
      call.set_command_open();
    else
      call.set_command_close();
  }
  if (has_tilt)
    call.set_tilt(conversion::cover_from_matter(tilt));
  call.perform();
}

void MatterCoverMapping::command_stop_() {
  if (this->has_stop_)
    this->cover_->make_call().set_command_stop().perform();
}

// The Window Covering attribute ids the binding writes (ESPHome -> Matter).
static_assert(MatterCoverMapping::WC_CLUSTER == wc::Id);
static_assert(MatterCoverMapping::WC_OPERATIONAL_STATUS == wc::Attributes::OperationalStatus::Id);
static_assert(MatterCoverMapping::WC_CURRENT_LIFT == wc::Attributes::CurrentPositionLiftPercent100ths::Id);
static_assert(MatterCoverMapping::WC_CURRENT_TILT == wc::Attributes::CurrentPositionTiltPercent100ths::Id);
static_assert(MatterCoverMapping::WC_TARGET_LIFT == wc::Attributes::TargetPositionLiftPercent100ths::Id);
static_assert(MatterCoverMapping::WC_TARGET_TILT == wc::Attributes::TargetPositionTiltPercent100ths::Id);

} // namespace esphome::matter

#endif // USE_MATTER && USE_COVER
