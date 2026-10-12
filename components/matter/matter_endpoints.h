#pragma once

#include "esphome/core/defines.h"
#ifdef USE_MATTER
#include "esphome/core/log.h"

#include <esp_matter.h>
#include <esp_matter_cluster.h>

#include <cstdint>

namespace esphome::matter {

using MatterEndpointBuildFn = bool (*)(esp_matter::endpoint_t *);

// Boot diagnostics. A generated build callback returns
// MATTER_BUILD_FAIL("endpoint 1: cluster FanControl feature MultiSpeed") for the
// step that failed. The step must be a string literal: MatterComponent keeps the
// pointer as the failure reason of the component.
bool record_build_failure(const char *step);
// The most recent step passed to record_build_failure, or nullptr.
const char *last_build_failure();
#define MATTER_BUILD_FAIL(step) ::esphome::matter::record_build_failure(step)

struct MatterEndpointRegistration {
  uint16_t endpoint_id;
  MatterEndpointBuildFn build_fn;
};

using MatterFeatureAddFn = esp_err_t (*)(esp_matter::cluster_t *);

inline esp_err_t add_feature(esp_matter::cluster_t *cluster,
                             MatterFeatureAddFn add_fn) {
  return add_fn(cluster);
}

template <typename ConfigT>
esp_err_t add_feature(esp_matter::cluster_t *cluster,
                      esp_err_t (*add_fn)(esp_matter::cluster_t *, ConfigT *)) {
  ConfigT config{};
  return add_fn(cluster, &config);
}

class MatterEndpointMappingBase {
public:
  explicit MatterEndpointMappingBase(uint16_t endpoint_id)
      : endpoint_id_(endpoint_id) {}
  virtual ~MatterEndpointMappingBase() = default;

  virtual void initialize() {}

  uint16_t endpoint_id() const { return this->endpoint_id_; }

protected:
  bool has_server_cluster(uint32_t cluster_id) const;

  uint16_t endpoint_id_;
};

} // namespace esphome::matter

#endif // USE_MATTER
