"""Schema of the `expose` and `exclude` keys of `matter:` (DIRECTION.md section 5)."""

import esphome.config_validation as cv

from ..conformance import MAX_TAGS_PER_ENDPOINT, _tag_entry

CONF_EXPOSE = "expose"
CONF_EXCLUDE = "exclude"
CONF_ID = "id"
CONF_AS = "as"
CONF_ENDPOINT = "endpoint"
CONF_FEATURES = "features"
CONF_LABEL = "label"
CONF_OPTIONS = "options"
CONF_ATTACH_TO = "attach_to"
CONF_METERS = "meters"
CONF_WAS = "was"
CONF_TAGS = "tags"
CONF_PARENT = "parent"
CONF_CLUSTERS = "clusters"

EXPOSE_ITEM_SCHEMA = cv.Any(
    cv.string_strict,
    cv.Schema(
        {
            cv.Required(CONF_ID): cv.string_strict,
            cv.Optional(CONF_AS): cv.string_strict,
            cv.Optional(CONF_ENDPOINT): cv.int_range(min=1, max=65534),
            cv.Optional(CONF_FEATURES): cv.ensure_list(cv.string_strict),
            cv.Optional(CONF_LABEL): cv.All(cv.string_strict, cv.Length(max=64)),
            cv.Optional(CONF_OPTIONS): cv.Schema({cv.string_strict: object}),
            cv.Optional(CONF_ATTACH_TO): cv.string_strict,
            cv.Optional(CONF_METERS): cv.string_strict,
            cv.Optional(CONF_WAS): cv.string_strict,
            # Same entries as the endpoint `tags:`; replace the automatic Common Number tags.
            cv.Optional(CONF_TAGS): cv.All(
                cv.ensure_list(_tag_entry), cv.Length(min=1, max=MAX_TAGS_PER_ENDPOINT)
            ),
            # Id of an exposed entity (or endpoint key) whose endpoint is the parent, or the
            # number of a hand-written endpoint.
            cv.Optional(CONF_PARENT): cv.Any(
                cv.int_range(min=0, max=65534), cv.string_strict
            ),
            # Same as the endpoint `clusters:`; merged into the lowered endpoint.
            cv.Optional(CONF_CLUSTERS): cv.Schema({cv.string_strict: object}),
        }
    ),
)
# `expose: all` is sugar for `expose: [all]`; `expose: []` is a valid root-only node.
EXPOSE_SCHEMA = cv.ensure_list(EXPOSE_ITEM_SCHEMA)
EXCLUDE_SCHEMA = cv.ensure_list(cv.string_strict)
