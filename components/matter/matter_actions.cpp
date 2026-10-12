#include "esphome/core/defines.h"
#ifdef USE_MATTER

#include "matter_actions.h"

#include "esphome/core/log.h"

#include <app/DeviceProxy.h>
#include <app/clusters/bindings/binding-table.h>
#include <platform/CHIPDeviceLayer.h>

#include <atomic>
#include <cstdio>
#include <cstring>
#include <inttypes.h>
#include <memory>
#include <string>

static const char *const TAG = "matter.actions";

namespace esphome::matter {

// Builds the JSON command payload for outgoing client commands. Called by
// esp_matter once per matching binding entry for every command sent through
// cluster_update(), so a single action fans out to one call per bound node.
static void client_invoke_cb(esp_matter::client::peer_device_t *peer_device,
                             esp_matter::client::request_handle_t *req_handle,
                             void *priv_data) {
  if (req_handle->type != esp_matter::client::INVOKE_CMD) {
    return;
  }
  const char *cmd_data =
      req_handle->request_data != nullptr
          ? static_cast<const char *>(req_handle->request_data)
          : "{}";
  // Captured into the response/failure callbacks below so each log line names
  // the node it came from; without it, fan-out to several bound nodes produces
  // indistinguishable lines. Captured rather than passed through send_request's
  // void *ctx because a 64-bit NodeId does not fit in a 32-bit pointer.
  const uint64_t node_id = static_cast<uint64_t>(peer_device->GetDeviceId());
  ESP_LOGV(TAG, "Sending request to node=%" PRIu64, node_id);
  esp_matter::client::interaction::invoke::send_request(
      nullptr, peer_device, req_handle->command_path, cmd_data,
      [node_id](void *, const chip::app::ConcreteCommandPath &path,
                const chip::app::StatusIB &status, chip::TLV::TLVReader *) {
        ESP_LOGD(TAG,
                 "Response: node=%" PRIu64
                 " endpoint=%u cluster=%lu command=%lu status=0x%02x",
                 node_id, static_cast<unsigned>(path.mEndpointId),
                 static_cast<unsigned long>(path.mClusterId),
                 static_cast<unsigned long>(path.mCommandId),
                 static_cast<unsigned>(status.mStatus));
      },
      [node_id](void *, CHIP_ERROR error) {
        ESP_LOGW(TAG,
                 "Send command to node=%" PRIu64 " failed: %" CHIP_ERROR_FORMAT,
                 node_id, error.Format());
      },
      chip::NullOptional);
}

static void
client_group_invoke_cb(uint8_t fabric_index,
                       esp_matter::client::request_handle_t *req_handle,
                       void *priv_data) {
  if (req_handle->type != esp_matter::client::INVOKE_CMD) {
    return;
  }
  const char *cmd_data =
      req_handle->request_data != nullptr
          ? static_cast<const char *>(req_handle->request_data)
          : "{}";
  ESP_LOGV(TAG, "Sending group request");
  esp_matter::client::interaction::invoke::send_group_request(
      fabric_index, req_handle->command_path, cmd_data);
}

void register_client_request_callbacks() {
  esp_matter::client::set_request_callback(client_invoke_cb,
                                           client_group_invoke_cb, nullptr);
}

namespace {

// Request handed from the ESPHome main loop to the CHIP task. Heap allocated
// because the work item is a single intptr_t; ScheduleLambda would be limited
// to CHIP_CONFIG_LAMBDA_EVENT_SIZE bytes of capture, too small for a string.
struct ClientCommandRequest {
  uint16_t endpoint_id;
  chip::ClusterId cluster;
  chip::CommandId command;
  std::string data;
};

// Upper bound on requests queued towards the CHIP task. If the CHIP task is
// stalled we drop new commands instead of growing the heap without bound.
constexpr uint8_t MAX_PENDING_CLIENT_COMMANDS = 16;
std::atomic<uint8_t> pending_client_commands{0};

// Runs on the CHIP task. The CHIP event loop holds the chip stack lock while
// executing scheduled work, so the binding table can be read safely and
// cluster_update() needs no extra locking here.
void send_client_command_on_matter_thread(intptr_t context) {
  std::unique_ptr<ClientCommandRequest> request(
      reinterpret_cast<ClientCommandRequest *>(context));
  pending_client_commands.fetch_sub(1);

  std::string node_ids;
  for (const auto &entry :
       chip::app::Clusters::Binding::Table::GetInstance()) {
    if (entry.local == request->endpoint_id &&
        (!entry.clusterId.has_value() ||
         entry.clusterId.value() == request->cluster)) {
      if (!node_ids.empty())
        node_ids += ",";
      char node_id[21];
      snprintf(node_id, sizeof(node_id), "%" PRIu64,
               static_cast<uint64_t>(entry.nodeId));
      node_ids += node_id;
    }
  }

  if (node_ids.empty()) {
    ESP_LOGW(TAG, "No bound nodes for endpoint=%u cluster=%lu",
             request->endpoint_id, static_cast<unsigned long>(request->cluster));
    return;
  }
  ESP_LOGD(TAG,
           "Sending command: nodes=[%s] endpoint=%u cluster=%lu command=%lu "
           "data=%s",
           node_ids.c_str(), request->endpoint_id,
           static_cast<unsigned long>(request->cluster),
           static_cast<unsigned long>(request->command), request->data.c_str());

  esp_matter::client::request_handle_t req;
  req.type = esp_matter::client::INVOKE_CMD;
  req.command_path.mClusterId = request->cluster;
  req.command_path.mCommandId = request->command;
  req.request_data = const_cast<char *>(request->data.c_str());
  esp_err_t err = esp_matter::client::cluster_update(request->endpoint_id, &req);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "cluster_update failed: %s", esp_err_to_name(err));
  }
}

} // namespace

// Called from the ESPHome main loop. Never blocks and never touches CHIP state:
// the whole send path (binding table walk and cluster_update) runs on the CHIP
// task. The command data is copied, so the caller's pointer need not outlive
// this call.
void send_client_command(uint16_t endpoint_id, chip::ClusterId cluster,
                         chip::CommandId command, const char *command_data) {
  if (pending_client_commands.load() >= MAX_PENDING_CLIENT_COMMANDS) {
    ESP_LOGW(TAG, "Too many pending commands; dropping command=%lu",
             static_cast<unsigned long>(command));
    return;
  }
  auto *request = new ClientCommandRequest{
      endpoint_id, cluster, command,
      command_data != nullptr ? command_data : "{}"};
  pending_client_commands.fetch_add(1);
  CHIP_ERROR err = chip::DeviceLayer::PlatformMgr().ScheduleWork(
      send_client_command_on_matter_thread, reinterpret_cast<intptr_t>(request));
  if (err != CHIP_NO_ERROR) {
    ESP_LOGE(TAG, "Failed to schedule Matter command: %s", err.AsString());
    pending_client_commands.fetch_sub(1);
    delete request;
  }
}

} // namespace esphome::matter

#endif // USE_MATTER
