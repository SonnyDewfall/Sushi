# Retired configs

These are kept for reference and recovery, not for use. They all depend on the
LSP plugin suite, which was moved to `plugins/archive/` and is no longer on
`LV2_PATH` — so they will not load as-is.

They were retired when the rig was rebuilt on a deliberately simpler plugin set
(`electric_board`): the three LSP plugins in `acoustic_chorus` alone produced
~130 panel faders, against 34 for the entire seven-pedal board that replaced it.

To bring one back: move its bundle out of `plugins/archive/lv2/` (or
`plugins/archive/lsp-plugins.lv2`) up into `plugins/`, and move the config back
into `config/`.

Note this directory is distinct from `config/archive/`, which is written
automatically by `sushi-rig listen` to snapshot previous versions of a config
when you re-save over its name. Nothing writes here; it is hand-curated.
