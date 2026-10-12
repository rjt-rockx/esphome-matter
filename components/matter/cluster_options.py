"""Entity bindings that can be configured in `clusters: <cluster>:` for some clusters.

* fan_control: fan_id / climate_id / wind_presets (fan_binding.py)
* hepa_filter_monitoring, activated_carbon_filter_monitoring (filter_monitoring.py)
"""

from . import fan_binding, filter_monitoring


def extra_schema(conf_key: str) -> dict:
    if conf_key == fan_binding.FAN_CONTROL_KEY:
        return fan_binding.cluster_options()
    if filter_monitoring.is_filter_cluster(conf_key):
        return filter_monitoring.schema_options()
    return {}


def validate(conf_key: str):
    """Validator applied to the cluster config after its schema."""
    if conf_key == fan_binding.FAN_CONTROL_KEY:
        return fan_binding.validate_cluster_options
    if filter_monitoring.is_filter_cluster(conf_key):
        return filter_monitoring.validate_cluster
    return lambda config: config
