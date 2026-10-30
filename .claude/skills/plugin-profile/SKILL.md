---
name: plugin-profile
description: Write or revise a sushi-rig component profile — the file that decides which of an LV2 plugin's controls appear, how they are grouped and labelled, and what scale each one uses. Use this whenever the user wants to profile a plugin, add a plugin to the rig's control surface, cut a panel down to the controls that matter, fix a panel showing wrong units or useless parameters, or asks what a plugin's controls ought to look like. Use it too when reviewing, correcting or re-validating an existing file under profiles/, and when a plugin turns out to be one Sushi cannot drive. Reach for it even if the user never says the word "profile" — if the question is which controls a plugin should show and how, this is the skill.
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
- **A screenshot of the plugin's own GUI**, if the user has one or has pasted one in. Layout is evidence: controls the author grouped together on screen usually belong together on the panel, and a knob drawn large is rarely an afterthought.
- **The parameter's range**, read carefully. A range of 100–1000 on something called Tone suggests Hz. A range of −20 to 4 suggests dB. Say "suggests" in the description rather than asserting a unit the plugin never declared — `probe` reports a real `unit` when one exists, and its absence is itself information.

## Write the profile

Copy the shape from `tool/examples/profiles/gxts9.yaml`, which was written by hand against real probe output and is the reference for this format. Profiles live in `profiles/` at the repository root, named after the plugin (`profiles/gxts9.yaml`). They are rig content rather than tool code, which is also why they will move to the library repository later (#27).

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
sushi-rig profile validate profiles/<name>.yaml
# or, away from the rig:
sushi-rig profile validate profiles/<name>.yaml --facts /tmp/probe-<name>.json
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
4. **What came from where** — docs, screenshot, or the range alone. A judgement drawn only from a parameter's range is a weaker thing than one the documentation states, and saying which is which is what makes the review quick.

Every judgement stays `draft` until a human changes it. That is not a formality: the panel will present these controls to someone playing live, and `draft` is an honest description of a proposal nobody has checked yet.
