"""`CaptureEngine`'s sequence of calls, against a hand-written stand-in for JACK.

Read `jack_fake.py` for what the stand-in is: written from JACK-Client's
documented shape, not a capture, so it proves the engine's own logic (what it
calls, how many times, what it does when a read comes back empty) and nothing
about JACK or PipeWire. Where the real library behaves otherwise, these pass
and the rig run fails. The graph around it is a real `pw-dump`, and the ports
JACK lists are the rig's, whose names are not the machine file's aliases.

Its own module because it needs numpy (the pump allocates its buffer).
"""

from __future__ import annotations

import sys

import pytest

np = pytest.importorskip("numpy")

from sushi_rig import capture  # noqa: E402
from sushi_rig.take import DRY, PROCESSED, Stem  # noqa: E402

from jack_fake import ALIAS, JACK_NAME, JACK_PORTS, RIG_DUMP, FakeJack  # noqa: E402

SOURCE = ALIAS
PLAN = ((Stem("guitar", DRY, SOURCE), 1),)


def engine(**kwargs):
    kwargs.setdefault("read_dump", lambda: RIG_DUMP)
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
    e = capture.CaptureEngine(48000, 256, settle_s=0.123, link_retry_s=0.0, read_dump=lambda: RIG_DUMP)
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
    e = capture.CaptureEngine(48000, 256, settle_s=0.123, link_retry_s=0.0, read_dump=lambda: RIG_DUMP)
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

def test_a_machine_file_port_is_linked_from_the_name_jack_knows_it_by(monkeypatch):
    """The rig refused every take: `no port called 'Audient iD14:capture_AUX0'
    (for board-dry.wav) is in the graph`, listing `Audient iD14
    Multichannel:capture_AUX0` among those that were. The machine file names the
    alias, which the patchbay needs; JACK lists the description. The alias is
    resolved through the patchbay's reader to the name JACK has."""
    assert ALIAS not in JACK_PORTS and JACK_NAME in JACK_PORTS, "the two spellings differ, as on the rig"
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


def test_a_port_the_live_graph_does_not_have_is_refused_before_any_port_is_made(monkeypatch):
    jack = FakeJack()
    jack.install(monkeypatch)
    with pytest.raises(capture.CaptureError) as caught:
        engine().start(((Stem("guitar", DRY, "Audient iD14:capture_AUX42"), 1),), lambda block: 0)
    assert "guitar-dry.wav cannot be captured from 'Audient iD14:capture_AUX42': no live port with alias" in str(caught.value)
    assert jack.clients[0].inports == [] and jack.clients[0].closed


def test_a_jack_name_jack_does_not_list_names_both_spellings(monkeypatch):
    """If `patchbay.jack_client_name` is wrong for a device, the take is refused
    with the name looked for and the one the machine file has, and the ports
    that exist: not linked silently from nothing."""
    FakeJack(ports=[p for p in JACK_PORTS if p != JACK_NAME]).install(monkeypatch)
    with pytest.raises(capture.CaptureError) as caught:
        engine().start(PLAN, lambda block: 0)
    said = caught.value.problems
    assert said[0] == (f"no port called {JACK_NAME!r}, which is {ALIAS!r} as the machine file names it "
                       "(for guitar-dry.wav) is in the graph JACK shows")
    assert said[-1].startswith("output ports in the graph include: ")


def test_a_graph_that_cannot_be_read_is_a_refusal_saying_so(monkeypatch):
    from sushi_rig.patchbay import PatchbayError

    FakeJack().install(monkeypatch)

    def unreadable():
        raise PatchbayError(["pw-dump is not installed, so the live graph cannot be read"])

    with pytest.raises(capture.CaptureError, match="the live graph, which could not be read: pw-dump is not installed"):
        engine(read_dump=unreadable).start(PLAN, lambda block: 0)
