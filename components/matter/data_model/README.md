The JSON files in this directory are auto-generated so don't edit them!
Use `tools/zap_converter.py` to regenerate these files.

`espmatter_names.json` maps the spec names in these files to the identifiers esp-matter uses in C++ (cluster,
feature and endpoint namespaces, `create_*` attribute functions, endpoint `config_t` members). `null` means esp-matter
does not have it; such features and clusters are rejected at config time. It is generated from the esp-matter headers
with `tools/gen_espmatter_names.py`; regenerate it when bumping the esp_matter version. Manual aliases for names that
cannot be derived live in that script.
