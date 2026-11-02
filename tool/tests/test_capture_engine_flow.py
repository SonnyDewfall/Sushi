"""`CaptureEngine`'s sequence of calls, against a hand-written stand-in for JACK.

Read `jack_fake.py` for what the stand-in is: written from JACK-Client's
documented shape, not a capture, so it proves the engine's own logic (what it
calls, how many times, what it does when a read comes back empty) and nothing
about JACK or PipeWire. Where the real library behaves otherwise, these pass
and the rig run fails. The ports JACK lists have the rig's names, which are
not the machine file's spelling; their aliases, which carry that spelling, are
a guess until the rig shows them (`jack_fake.py`).

Its own module because it needs numpy (the pump allocates its buffer).
"""

from __future__ import annotations

import sys

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import capture  # noqa: E402
from sushi_rig.take import DRY, PROCESSED, Stem  # noqa: E402

from jack_fake import ALIAS, JACK_NAME, JACK_PORTS, FakeJack  # noqa: E402

SOURCE = ALIAS
PLAN = ((Stem("guitar", DRY, SOURCE), 1),)


def engine(**kwargs):
    return capture.CaptureEngine(48000, 256, settle_s=0.0, link_retry_s=0.0, **kwargs)


def test_a_link_that_shows_up_a_moment_late_is_not_a_refused_capture(monkeypatch):
    """Under pipewire-jack the client's view of the graph may lag a connect().
    Whether it does is unverified; a good start must not depend on it not."""
    FakeJack(visible_after=3).install(monkeypatch)
    e = engine()
    e.start(PLAN, lambda block: len(block))
    assert e.stop()["error"] == ""


def test_a_link_that_never_shows_is_refused_after_trying(monkeypatch):
    FakeJack(visible_after=10**6).install(monkeypatch)
    e = engine()
    with pytest.raises(capture.CaptureError, match="should be fed by Audient iD14 Multichannel:capture_AUX0 alone"):
        e.start(PLAN, lambda block: len(block))


def counting_sleep(monkeypatch):
    """Records every wait the engine asks for, without making it."""
    waits = []
    monkeypatch.setattr(capture.time, "sleep", waits.append)
    return waits


def test_the_quantum_is_read_once_not_once_per_take(monkeypatch):
    """Reading it costs a wait on the caller's thread, which in the listener is
    the one OSC thread: a pause on every RECORD. And the first take uses one
    client, not a probe and a capture."""
    jack = FakeJack()
    jack.install(monkeypatch)
    waits = counting_sleep(monkeypatch)
    e = capture.CaptureEngine(48000, 256, settle_s=0.123, link_retry_s=0.0)
    e.start(PLAN, lambda block: len(block))
    e.stop()
    assert (len(jack.clients), jack.activations) == (1, 1)
    e.start(PLAN, lambda block: len(block))
    e.stop()
    assert (len(jack.clients), jack.activations) == (2, 2)
    assert waits.count(0.123) == 1, "the settle is paid by the first take only"


def test_prepare_pays_the_wait_ahead_of_the_first_take_on_a_client_of_its_own(monkeypatch):
    jack = FakeJack()
    jack.install(monkeypatch)
    waits = counting_sleep(monkeypatch)
    e = capture.CaptureEngine(48000, 256, settle_s=0.123, link_retry_s=0.0)
    e.prepare()
    probe = jack.clients[0]
    assert probe.closed and probe.name != e.client_name, "a name of its own, so no -01 rename"
    e.start(PLAN, lambda block: len(block))
    assert jack.clients[1].name == e.client_name
    assert waits.count(0.123) == 1, "and the take does not wait again"
    e.stop()


def test_nothing_reaches_the_recorder_before_every_link_has_settled(monkeypatch):
    """The process callback is live from activation, but the first block pushed
    must be the first with every column linked: otherwise the take opens with
    silence and columns linked on different cycles sit a quantum apart."""
    jack = FakeJack(visible_after=2)
    jack.install(monkeypatch)
    pushed = []
    e = engine()

    def a_cycle(client):
        client.process(256)

    jack.on_activate.append(a_cycle)
    jack.on_connect.append(a_cycle)
    e.start(PLAN, lambda block: pushed.append(len(block)) or len(block))
    assert pushed == [], "cycles ran at activation and between links; none was pushed"
    jack.clients[0].process(256)
    assert pushed == [256], "the gate is open once start returns"
    account = e.stop()
    assert account["gated_cycles"] == 2 and account["blocks"] == 1


def test_a_start_refused_at_the_links_has_fed_the_recorder_nothing(monkeypatch):
    jack = FakeJack(visible_after=10**6)
    jack.install(monkeypatch)
    pushed = []
    jack.on_connect.append(lambda client: client.process(256))
    e = engine()
    with pytest.raises(capture.CaptureError):
        e.start(PLAN, lambda block: pushed.append(1) or len(block))
    assert pushed == []


def test_the_start_up_quantum_transient_does_not_flag_a_clean_take(monkeypatch):
    jack = FakeJack()
    jack.install(monkeypatch)
    jack.on_activate.append(lambda client: [client.blocksize_cb(n) for n in (256, 1024, 256)])
    e = engine()
    e.start(PLAN, lambda block: len(block))
    account = e.stop()
    assert account["quantum_changes"] == 0


def test_a_wrong_quantum_is_refused_after_activation_and_read_again_next_time(monkeypatch):
    jack = FakeJack(blocksize=1024)
    jack.install(monkeypatch)
    pushed = []
    jack.on_activate.append(lambda client: client.process(1024))
    e = engine()
    with pytest.raises(capture.CaptureError, match="granted a quantum of 1024"):
        e.start(PLAN, lambda block: pushed.append(1) or len(block))
    assert pushed == []
    jack.blocksize = 256
    e.start(PLAN, lambda block: len(block))  # not stuck on the old answer
    e.stop()


@pytest.mark.parametrize("fails", ["plan", "jack"])
def test_a_start_that_fails_early_does_not_hand_back_the_last_takes_account(monkeypatch, fails):
    FakeJack().install(monkeypatch)
    e = engine()
    e.start(PLAN, lambda block: len(block))
    e.stop()
    assert e.stop() is not None, "a finished take's account is kept for its own stop"
    if fails == "plan":
        with pytest.raises(capture.CaptureError):
            e.start(((Stem("guitar", DRY, SOURCE), 0),), lambda block: len(block))
    else:
        monkeypatch.setitem(sys.modules, "jack", None)
        with pytest.raises(capture.CaptureUnavailable):
            e.start(PLAN, lambda block: len(block))
    assert e.stop() is None, "no account at all, not an empty one the take would keep as `engine: {}`"


# -- which port a column is linked from (the rig, 2026-11-02) ------------------------------

def test_a_machine_file_port_is_linked_from_the_jack_port_that_carries_it_as_an_alias(monkeypatch):
    """The rig refused every take: `no port called 'Audient iD14:capture_AUX0'
    (for board-dry.wav) is in the graph`, listing `Audient iD14
    Multichannel:capture_AUX0` among those that were. The machine file names the
    alias, which the patchbay needs; JACK names the port otherwise and lists the
    alias among its aliases (a guess until the rig shows it)."""
    assert ALIAS not in JACK_PORTS and ALIAS in JACK_PORTS[JACK_NAME], "the two spellings differ"
    jack = FakeJack(levels={JACK_NAME: 0.25, "sushi:audio_output_2": 0.5, "sushi:audio_output_3": 0.75})
    jack.install(monkeypatch)
    pushed = []
    e = engine()
    plan = ((Stem("board", PROCESSED, "sushi:audio_output_2"), 2), (Stem("board", DRY, ALIAS), 1))
    e.start(plan, lambda block: pushed.append(block.copy()) or len(block))
    assert [source for source, _ in jack.connected] == ["sushi:audio_output_2", "sushi:audio_output_3", JACK_NAME]
    jack.clients[0].process(256)
    assert pushed[0][0].tolist() == [0.5, 0.75, 0.25], "each column holds its own port's signal"
    assert e.stop()["sources"] == {
        "board.wav[0]": "sushi:audio_output_2", "board.wav[1]": "sushi:audio_output_3",
        "board-dry.wav": JACK_NAME,
    }


def test_a_port_jack_does_not_have_is_refused_before_any_port_is_made_with_every_port_listed(monkeypatch):
    jack = FakeJack()
    jack.install(monkeypatch)
    with pytest.raises(capture.CaptureError) as caught:
        engine().start(((Stem("guitar", DRY, "Audient iD14:capture_AUX42"), 1),), lambda block: 0)
    said = caught.value.problems
    assert said[0] == "no JACK port is called or aliased 'Audient iD14:capture_AUX42' (for guitar-dry.wav)"
    assert f"{JACK_NAME} (aliases: alsa:acp:iD14:2:capture:capture_0, {ALIAS})" in said[-1]
    assert all(name in said[-1] for name in JACK_PORTS), "every port JACK lists, with its aliases"
    assert jack.clients[0].inports == [] and jack.clients[0].closed


def test_an_alias_two_ports_carry_is_refused_naming_both(monkeypatch):
    """Two profiles of one interface live at once would both carry
    `Audient iD14:capture_AUX0`: which to record is not guessed."""
    ports = {**JACK_PORTS, "Audient iD14 Pro:capture_AUX0": ("alsa:acp:iD14:1:capture:capture_0", ALIAS)}
    FakeJack(ports=ports).install(monkeypatch)
    with pytest.raises(capture.CaptureError) as caught:
        engine().start(PLAN, lambda block: 0)
    first = caught.value.problems[0]
    assert first.startswith(f"{ALIAS!r} (for guitar-dry.wav) names more than one JACK port: ")
    assert JACK_NAME in first and "Audient iD14 Pro:capture_AUX0" in first


def test_a_port_named_as_jack_names_it_is_found_too(monkeypatch):
    FakeJack().install(monkeypatch)
    e = engine()
    e.start(((Stem("guitar", DRY, JACK_NAME), 1),), lambda block: len(block))
    assert e.stop()["sources"] == {"guitar-dry.wav": JACK_NAME}


def test_the_engine_reads_no_pw_dump(monkeypatch):
    """The earlier fix read `pw-dump` in a subprocess at every RECORD, to
    predict JACK's names: latency at the start of a take, and a take refused
    whenever pw-dump stalled. The client's own listing is all it asks."""
    import subprocess

    from sushi_rig import patchbay

    def no(*a, **k):
        raise AssertionError("the engine must not read pw-dump or run anything")

    monkeypatch.setattr(patchbay, "read_pw_dump", no)
    monkeypatch.setattr(subprocess, "run", no)
    FakeJack().install(monkeypatch)
    e = engine()
    e.start(PLAN, lambda block: len(block))
    e.stop()


def test_the_listing_doctor_runs_under_pw_jack_prints_every_port_and_its_aliases(monkeypatch, capsys):
    """`python -m sushi_rig.capture`, what `capture.list_jack_ports` runs:
    the same `jack_ports` the engine matches on, as JSON."""
    import json

    jack = FakeJack()
    jack.install(monkeypatch)
    assert capture._print_ports() == 0
    listed = json.loads(capsys.readouterr().out)
    assert {p["name"]: tuple(p["aliases"]) for p in listed} == JACK_PORTS
    assert jack.clients[0].closed and jack.clients[0].inports == [], "it lists, and registers nothing"


def test_the_listing_says_why_when_there_is_no_server(monkeypatch, capsys):
    jack = FakeJack()

    def refuse(name, no_start_server=False):
        raise jack.JackError("server not running")

    jack.Client = refuse
    jack.install(monkeypatch)
    assert capture._print_ports() == 1
    assert "no JACK or PipeWire server to connect to" in capsys.readouterr().err
