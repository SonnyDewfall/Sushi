# Retired configs

These are kept for reference and recovery, not for use. The configs from
before 2026-11 all depend on the LSP plugin suite, which was moved to
`plugins/archive/` and is no longer on `LV2_PATH` — so they will not load as-is.

They were retired when the rig was rebuilt on a deliberately simpler plugin set
(`electric_board`): the three LSP plugins in `acoustic_chorus` alone produced
~130 panel faders, against 34 for the entire seven-pedal board that replaced it.

To bring one back: move its bundle out of `plugins/archive/lv2/` (or
`plugins/archive/lsp-plugins.lv2`) up into `plugins/`, and move the config back
into `config/`.

## electric-octave-up (2026-11-16, #152)

Retired when the octave pair (gx_detune_) was taken out of every rig: it
existed for its octave up, whose plugin goes silent now and then (the known
issue in `docs/reference/plugins.md`). Unlike the configs above it loads
as-is, since gx_detune_ is still in `plugin-manifest.txt`. What is here:

- `rigs/electric-octave-up.yaml`, the rig of channels, from `config/rigs/`
- `electric-octave-up.json`, its v1 config, from `config/`
- `sound/electric-octave-up.yaml` and `sound/electric-octave-up.v1-mono.yaml`,
  their sound references, from `config/sound/`

To bring it back, move each to the same place under `config/`. `nulltest
--all` fails on a reference whose config is gone, which is why the references
moved with it.

Its successor is `config/rigs/electric-octave.yaml` (2026-11-17, #164): the
same board, with the octave up on Rubber Band 4.0's Live shifter, on a channel
of its own (`docs/reference/plugins.md`).

## The v1 configs (2026-11-26, #218)

Retired with the v1 path: `sushi-rig up` runs only rigs of channels from
`config/rigs/` now, and nothing reads `config/<name>.json`. Moved here, not
deleted, since all but `electric_board` and `guitar_vocal` were tones saved
from the panel. Each has a rig of channels of the same name in `config/rigs/`
it was converted to (#30, `sushi-rig convert`). What is here:

- `v1/*.json`, the ten v1 configs, from `config/`: accustomed,
  electric-chorus, electric-chorus-2, electric-chorus-hall, electric-clean,
  electric-delay, electric-spacey-delay, electric_board, guitar_vocal and
  hard-feelings
- `v1/src/*.yaml`, the three rig.yaml sources they were built from, from
  `config/src/`: electric_board, electric_stereo and guitar_vocal
- `v1/sound/*.v1-mono.yaml`, their sound references, from `config/sound/`:
  `nulltest --all` takes only the rigs of channels now, and would call these
  orphans

They still load as they are: `sushi-rig verify config/retired/v1/<name>.json`
takes one by its path, and `sushi-rig convert` turns one into a rig of
channels. `nulltest` looks for a v1 config's reference in `config/sound/`, so
to null-test one, put its reference back there first. `config/archive/`,
the earlier versions their saves archived, is left where it was.

Note this directory is distinct from `config/archive/`, which `sushi-rig
save` (and, until it was retired, the panel's listener) writes to snapshot
previous versions of a config when you re-save over its name. Nothing writes here; it is hand-curated.
