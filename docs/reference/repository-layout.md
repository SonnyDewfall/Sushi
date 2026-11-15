# Repository layout

This is a **monorepo with two zones**: the *rig* (personal, machine-specific —
the configs and scripts you run daily) at the root, and the *tool* (reusable
software that generates the configs) under `tool/`. They share one workflow, so
they share one repo; if the tool ever needs to stand alone, it can be split out
then.

```
.                               ── RIG (root): what you run daily
├── config/
│   ├── rigs/                     the rigs: one file each, channels and a master.
│   │                             What the web UI starts, switches and saves into
│   ├── *.json                    the v1 configs, one tone each (`sushi-rig up <name>`
│   │                             without --rig; electric_board.json is its default)
│   ├── src/                      hand-authored v1 sources (electric_board,
│   │                             electric_stereo, guitar_vocal; the built
│   │                             electric_stereo.json is in retired/)
│   ├── sound/                    nulltest references: what each rig (`<name>.yaml`, or
│   │                             `<name>.<channel>.yaml`) and v1 config
│   │                             (`<name>.v1-mono.yaml`) sounds like
│   ├── archive/                  auto-written version snapshots of v1 configs,
│   │                             config/archive/<name>/vX.Y.json
│   └── retired/                  hand-retired configs (incl. empty.json), kept
│                                 for recovery only
├── Patchbay/                   qpwgraph sessions
│   ├── rig.qpwgraph              the v1 layout's routing; generated from
│   │                             config/src/guitar_vocal.yaml, held by a test.
│   │                             A rig of channels generates its own at start
│   ├── Default.qpwgraph          hand-made in qpwgraph on the rig, kept as a
│   │                             sample; nothing in the tool reads it
│   └── Test_1.qpwgraph           the same: a hand-made session, not read
├── machines/                   the only files that name physical ports,
│   ├── example.yaml              one per machine, as <hostname>.yaml
│   ├── loopback-test.yaml        for the loopback test in tool/spikes/live_rig/
│   └── sonny-dewfall-XPS-9320.yaml
├── amp/models/                 the neural amp's .nam captures
├── docs/
│   ├── guide/                    the user guides, and their screenshots in img/
│   ├── reference/                the detail: files, commands, the agent, tests
│   ├── design/                   why it is built this way; the original brief
│   └── ui/                       the two screenshots the web UI reference shows
├── ci/                         Dockerfile for the container the full suite runs in
├── systemd/                    the agent's user unit; `sushi-rig agent install-service` installs it
├── .github/workflows/          ci.yml (fast suite), rig-container.yml (everything)
├── .claude/skills/             plugin-profile: how a component profile is written
├── plugin-manifest.txt         every LV2 URI this rig can load (see Plugins and portability)
├── plugin-ports.txt            each of those plugins' audio ins and outs, for the compiler
│                               (see The rig file: a mono plugin on a stereo channel)
│
├── tool/                       ── TOOL: the `sushi-rig` package
│   ├── pyproject.toml
│   ├── src/sushi_rig/            47 modules and __init__, one job each:
│   │                             spec, rigfile, rigedit, convert, emit, dump, probe,
│   │                             probes, profile, plugins, library, live, panel,
│   │                             listen, save, saves, state, procs, rig, supervisor,
│   │                             qpwgraph, patchbay, machine, amp, amp_live, top,
│   │                             doctor, verify, render, nulltest, signals, analysis,
│   │                             selftest, take, transport, recorder,
│   │                             recorder_process, capture, midi_listener, agent,
│   │                             agent_client, service, yamlmerge, paths, _numpy,
│   │                             stereo, cli
│   │   └── ui/static/            the web UI: index.html, app.js, rig.js, style.css
│   ├── tests/                    fast suite; fixtures/ holds captured artefacts, and the
│   │                             rigs, v1 configs and machine file the tests name, frozen
│   ├── examples/
│   │   ├── rig.example.yaml       annotated v1 rig spec (real plugins)
│   │   ├── dump.example.json      real --dump-plugins output, test fixture
│   │   └── profiles/gxts9.yaml    a component profile
│   └── spikes/                   experiments, kept as dated records with their
│                                 findings: virtual_ui/ (headless plugin GUI, #28),
│                                 live_rig/ (the capture engine, the live paths, the
│                                 web UI and the iD14 on the rig) and gx_detune_nan/
│
├── MANIFEST.md                 the change log: what was done, found and decided
└── README.md                   the front page
```

Untracked but required at run time: Sushi 1.3.0 as `./sushi` at the root (the
AppImage, or a symlink to it) and `plugins/`. See
[Plugins and portability](plugins.md).

Written at run time and ignored by git: `.sushi-rig/saves/<rig>/`, the snapshots
Revert lists ([the web UI](web-ui.md)). Takes go outside the checkout, in
`~/sushi-rig-takes/` ([Takes](takes.md)).

The original brief is `docs/design/IMPLEMENTATION_BRIEF.md`, kept as history:
see [the design pages](../design/README.md).
