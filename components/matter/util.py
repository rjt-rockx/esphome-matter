import logging
import re
from collections.abc import Callable, Iterator, Mapping, Sequence

import esphome.config_validation as cv
from esphome.config import Config
from esphome.const import CONF_RESTORE_MODE
from esphome.types import ConfigFragmentType

from .const import CONF_ENDPOINTS

_LOGGER = logging.getLogger(__name__)


def iter_matter_actions(
    config: ConfigFragmentType,
) -> Iterator[tuple[str, Mapping]]:
    """Yield all Matter actions from a configuration fragment."""
    if isinstance(config, Mapping):
        for key, value in config.items():
            if isinstance(key, str) and key.startswith("matter."):
                yield key, value
            yield from iter_matter_actions(value)
    elif isinstance(config, Sequence) and not isinstance(config, (str, bytes)):
        for value in config:
            yield from iter_matter_actions(value)


def snake_case(name: str) -> str:
    """Converts CamelCase to snake_case."""
    name = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return name.lower()


def maybe_empty(*validators):
    """Allow an empty config section instead of requiring an empty {}."""
    return cv.All(lambda v: {} if v is None else v, *validators)


def iter_bound_entities(
    matter_config: Mapping, full_config: Config, id_key: str
) -> Iterator[tuple[int, str, Mapping, object, Mapping]]:
    """Yield (endpoint id, device type key, device config, entity id, entity config)
    for every device type config that binds an ESPHome entity with ``id_key``."""
    for endpoint_id, endpoint_config in matter_config.get(CONF_ENDPOINTS, {}).items():
        for key, device_config in endpoint_config.items():
            if not isinstance(device_config, Mapping):
                continue
            entity_id = device_config.get(id_key)
            if entity_id is None:
                continue
            try:
                path = full_config.get_path_for_id(entity_id)[:-1]
                entity_config = full_config.get_config_for_path(path)
            except KeyError:
                continue
            yield endpoint_id, key, device_config, entity_id, entity_config


def restore_mode_description(entity_config: Mapping) -> str | None:
    """Describes a restore_mode other than ALWAYS_OFF, else None."""
    restore_mode = entity_config.get(CONF_RESTORE_MODE)
    if restore_mode is None or restore_mode == "ALWAYS_OFF":
        return None
    return f"restore mode {restore_mode}"


def restore_warning(
    matter_config: Mapping,
    full_config: Config,
    id_key: str,
    kind: str,
    describe: Callable[[Mapping], str | None] = restore_mode_description,
    note: str = "",
) -> None:
    """Warn about bound entities that restore their own state.

    Matter owns the power-on state of a bound entity, so a restore setting of
    the entity itself (``describe`` returns it) can disagree with it.
    """
    for _, _, _, entity_id, entity_config in iter_bound_entities(
        matter_config, full_config, id_key
    ):
        description = describe(entity_config)
        if description is not None:
            _LOGGER.warning(
                "%s '%s' is exposed through Matter with %s and should use "
                "restore_mode: ALWAYS_OFF so Matter can restore its power-on state%s",
                kind,
                entity_id,
                description,
                note,
            )
