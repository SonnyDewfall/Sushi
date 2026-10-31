---
name: plugin-profile
description: Write or revise a sushi-rig component profile — the file that decides which of an LV2 plugin's controls appear, how they are grouped and labelled, what scale each uses, and where they sit on a facsimile of the plugin's own GUI. Use this whenever the user wants to profile a plugin, add a plugin to the rig's control surface, cut a panel down to the controls that matter, fix a panel showing wrong units or useless parameters, capture a plugin's GUI or locate its controls, or asks what a plugin's controls ought to look like. Use it too when reviewing, correcting or re-validating an existing file under tool/examples/profiles/, and when a plugin turns out to be one Sushi cannot drive. Reach for it even if the user never says the word "profile" — if the question is which controls a plugin should show and how, this is the skill.
---

# Writing a component profile

A **component profile** describes one plugin and how to present it: which controls show, how they group, what they are called, what scale they use, and which matter on a phone. The rig's panel generator reads it, so a good profile is the difference between four meaningful knobs and twenty-four meaningless ones.

## The one rule that matters

**Facts come from `probe`. Judgements are yours, and are marked as drafts.**

This split is the whole point. A model that confidently invents a parameter name or a range produces a profile that looks right, loads cleanly, and silently misrepresents the plugin — the exact silent failure the project's principle 8 exists to forbid. So the validator re-derives every fact and rejects anything invented, and it will catch you. Work with it rather than against it:

| Facts — never type these from memory | Where they come from |
|---|---|
| Parameter name, symbol | `probe` |
| min, max, default | `probe` |
| toggled, integer, enumeration, logarithmic | `probe` |
| settable | `probe` (`automatable`) |
| audio_in, audio_out, config_drivable | `probe` |
| plugin version | `probe` |

| Judgements — propose these, marked `draft` | What you are deciding |
|---|---|
| visibility | Is this worth a player's attention: primary, advanced, or hidden |
| group, order | What belongs together, and in what order |
| label | The short name a player reads |
| description | One line, under 100 characters |
| scale | How the value should be presented |
| control | knob, fader, toggle, selector |
| phone | Priority on a small screen |

## Get the facts first

Nothing else can start until you have real probe output. Two ways:

**On a machine with the plugins** (the rig machine, or the CI container):

```bash
sushi-rig probe "<uri>" --json > /tmp/probe-<short-name>.json
```

Quote the URI — most of them contain `#`, which a shell treats as a comment.

**Anywhere else, including a plain conversation:** work from a saved probe JSON the user supplies or that is committed in the repo. `sushi-rig profile validate FILE --facts probe.json` exists precisely for machines without lilv, so a profile can be written and validated far from the rig.

If you have neither, stop and ask for one. Do not infer a plugin's parameters from its name, its documentation, or another plugin like it. A profile built on guessed facts is worse than no profile, because it will pass a careless review.

## Then gather what informs the judgements

- **The plugin's documentation.** `probe` reports a homepage where the metadata declares one. Fetch it and read it — it is usually the only place that says what a control is *for*, as opposed to what it is called.
- **A screenshot of the plugin's own GUI.** Layout is evidence: controls the author grouped together on screen usually belong together on the panel, and a knob drawn large is rarely an afterthought. On a machine with the plugins you can capture this yourself, and locate every control while you are at it — see the next section. Failing that, use one the user pastes in.
- **The parameter's range**, read carefully. A range of 100–1000 on something called Tone suggests Hz. A range of −20 to 4 suggests dB. Say "suggests" in the description rather than asserting a unit the plugin never declared — `probe` reports a real `unit` when one exists, and its absence is itself information.

## Get a visual read, if the machine can

On a machine that has the plugins, you can run the plugin's own GUI headlessly, move every parameter, and find each control by diffing screenshots. This is not speculative — it was proven on two plugins, a GTK one and an OpenGL one, locating knob centres to within 2.5 px repeatably. A pedal sweeps in about 10 seconds.

**Check the prerequisites first**, because a missing one wastes a confusing minute:

```bash
command -v Xvfb jackd lv2info && ls -d /usr/share/carla
python3 -c "import numpy, PIL, scipy, pythonosc, Xlib; print('deps ok')"
```

`jackd` is the one most likely to be missing. The rig machine runs PipeWire and has no `jackd`, so install `jackd2` if it is absent — the sweep starts its own private server with a dummy driver under a unique name, so it will not fight PipeWire for the audio device.

If anything is missing and the user does not want to install it, say so and carry on without the visual read. Facts and docs still make a useful profile; it just has no `face`.

**Run it:**

```bash
tool/spikes/virtual_ui/run_sweep.sh "<uri>" /tmp/sweep-<name> --steps 9
```

`--steps 9` is plenty for finding positions — the default 25 is for measuring accuracy and takes two to four times as long. Use `--skip SYM1,SYM2` for ports you do not care about. The script starts Xvfb, a private JACK server and the Carla host, and tears all three down on exit.

**What it gives you**, in the output directory:

| File | Use |
|---|---|
| `rest.png` | The GUI at rest. This is the screenshot for `face.screenshot` |
| `boxes.json` | The located control boxes, keyed by port symbol |
| `diff_<symbol>.png` | What changed when that control moved — look at these if a box seems wrong |

**Read `rest.png` yourself.** You can look at an image, and the layout is the single best evidence for grouping and for which controls the author treated as important. Do not just harvest the boxes and skip the picture.

**The boxes map straight onto the profile's `face` section**, which exists for this:

```yaml
face:
  screenshot: {file: faces/gxts9.png, width: 260, height: 360}
  zones:
    - {parameter: Drive, bbox: [39, 72, 58, 57], confirmed: sweep}
    - {parameter: Tone, bbox: [107, 139, 43, 43], confirmed: sweep}
```

`confirmed: sweep` means the position was measured. Use `draft` only for a zone you placed by eye on a screenshot you were given, and never claim `sweep` for one you did not measure — the whole value of the field is that it distinguishes the two. The validator checks every zone lies inside the screenshot and names a parameter `probe` knows.

Two things to know about the boxes. A located box is **the part that moves**, not the whole hit target — a knob's indicator sweeps a smaller area than the knob, and the TS9's bypass footswitch located as its 18×11 px LED rather than the 60×85 px switch. So widen a zone to something a finger can hit, and keep `confirmed: sweep` only if you have not moved its centre. And match Carla's parameter numbering to Sushi's **by port symbol**, never by index: `probe` reports both, and they agree on symbols but not necessarily on numbering.

These are spike scripts under `tool/spikes/`, not production code, and they are not covered by the test suite the way `tool/src` is. They work, and `tool/spikes/virtual_ui/FINDINGS.md` records their gotchas and timings — read it if a run misbehaves. Productising this into an unattended CI pipeline is #28's job, not this skill's.

## Write the profile

Copy the shape from `tool/examples/profiles/gxts9.yaml`, which was written by hand against real probe output and is the reference for this format. Profiles live in `tool/examples/profiles/`, named after the plugin (`tool/examples/profiles/gxts9.yaml`); there is no `profiles/` directory at the repository root. They are rig content rather than tool code, which is also why they will move to the library repository later (#27).

Allowed values, enforced by the validator:

- `kind`: effect, instrument, midi-effect, audio-to-midi, amp, utility
- `host`: sushi-lv2, carla-lv2
- `visibility`: primary, advanced, hidden
- `scale`: linear, log, db, enum, toggle
- `control`: knob, fader, toggle, selector
- `provenance`: draft, reviewed, fact

### Provenance: use `fact` sparingly

`fact` means "mechanically derivable, and the validator will re-derive it". It is not a confidence rating, and claiming it where it does not hold fails the file. In practice only two judgements qualify:

- `label`, when it is identical to the parameter's own name
- `scale`, when the flags decide it — `toggle` for a toggled port, `enum` for an enumeration

Everything else you propose is `draft`, including judgements you are confident about. `reviewed` is not yours to set: it means a human has looked at it, so leave that for the person who does.

### Judgement, with reasons

**Hide the plugin's own bypass.** The rig bypasses through Sushi, so a second bypass control is a trap — two switches that disagree. Hide it and say why in the description.

**Hide read-only meters and visibility flags.** They are not settable, and a fader that does nothing is worse than no fader.

**primary means a player would reach for it mid-song.** Everything a player would only touch while dialling in a tone is `advanced`. A dense synth with 24 parameters and 24 primaries has not been profiled; it has been transcribed.

**Group by what the controls do, not by port order.** Threshold and ratio belong together; attack and release belong together. The plugin's own GUI layout is the best evidence available, which is why a screenshot is worth asking for.

**`db` is for values the plugin declares in decibels**, or whose range makes it unambiguous. `log` is for values best moved logarithmically, such as frequency or time. Note that Sushi normalises every control linearly and ignores the LV2 `logarithmic` hint — so this field is purely about presentation, and compensating for it is the panel's job, not the profile's.

## Validate, every time

```bash
sushi-rig profile validate tool/examples/profiles/<name>.yaml
# or, away from the rig:
sushi-rig profile validate tool/examples/profiles/<name>.yaml --facts /tmp/probe-<name>.json
```

Exit 1 means errors; warnings alone are fine. Fix every error before showing the file to anyone.

Each finding carries a code, and the message is specific enough to act on without re-reading the plugin. The ones you will actually hit:

- **`invented-parameter`** — you named a parameter probe does not know. It will tell you what Sushi calls that symbol, which usually makes the fix obvious: `probe knows no parameter named 'Treble' (symbol 'fslider1_' is called 'Tone' in Sushi)`.
- **`fact-mismatch`** — a restated fact disagrees with probe. Probe is right and you are wrong: `restated 9999.0 but probe says 1000.0`.
- **`fact-not-derivable`** — you marked a judgement `fact` where the facts derive something else: `marked fact but the facts derive 'Drive', not 'Grind'`. Either use the derived value or mark it `draft`.
- **`undefined-group`** — a parameter points at a group id that is not declared under `groups`.
- **unknown key** — a typo. Unknown keys are rejected deliberately, so a misspelling cannot pass silently.
- **`unverified-version`** (warning) — probe reported no version, so a version pin cannot be checked. Fine to leave, but do not pretend the pin is verified.

Warnings worth acting on rather than ignoring: an unmentioned settable parameter usually means you missed something, and a zone confirmed only as `draft` means its position has not been measured.

## When Sushi cannot drive the plugin

`probe` reports `config_drivable: false` when a plugin keeps state outside its control ports — a file, a model, an impulse response — reached through LV2 `patch:` or `state:`, which Sushi's wrapper does not bridge. The neural amp is the rig's own example: it selects its model through an `atom:Path` parameter, which is the entire reason it runs in Carla rather than in Sushi.

For such a plugin the profile's job is to **say so plainly, not to invent a control surface**. Set `ports.config_drivable: false`, set `host: carla-lv2` if that is how it runs, and describe what cannot be set from a config. A profile that offers knobs for a plugin that will ignore them is the worst possible outcome here: it looks helpful and it lies.

## Hand it back honestly

When you present a profile, say:

1. **What it covers** — how many parameters, how many primary.
2. **The validator's result** — the actual counts, errors and warnings.
3. **The judgements you are least sure of.** Grouping on a dense plugin, and any unit you inferred from a range rather than read from a declaration, are the usual candidates. The person reviewing needs to know where to look, and a profile presented as uniformly confident wastes their attention.
4. **What came from where** — the sweep, the docs, a screenshot, or the range alone. A judgement drawn only from a parameter's range is a weaker thing than one the documentation states or the sweep measured, and saying which is which is what makes the review quick.
5. **Whether you got a visual read**, and if not, why. A profile with no `face` is not worse, but it is different, and the reviewer should not have to work out which kind they are looking at.

Every judgement stays `draft` until a human changes it. That is not a formality: the panel will present these controls to someone playing live, and `draft` is an honest description of a proposal nobody has checked yet.
