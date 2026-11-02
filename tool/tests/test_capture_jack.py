"""That the names the capture engine calls exist in the installed JACK-Client.

This is all it proves. There is no JACK or PipeWire server in CI, so nothing
here starts a client, registers a port or runs a callback; whether the engine
behaves against a live server is unverified, and `capture.py` says so. What it
does catch is a JACK-Client that renames or removes a method the engine calls,
which would otherwise surface on the rig machine, mid-take.

Its own module because it needs the `capture` extra and libjack, and
`importorskip` mid-module would hide the tests that need neither. CI installs
both (`ci.yml`, `ci/Dockerfile`), so here it runs rather than skips.
"""

from __future__ import annotations

import pytest

# Not importorskip: that catches ImportError only, and `import jack` raises
# OSError when libjack is missing, which would be a collection error that
# fails the whole run. CI installs both the package and libjack, so there this
# imports and the tests run; the skip is for a developer's machine.
try:
    import jack
except (ImportError, OSError) as exc:
    pytest.skip(f"needs the capture extra and libjack ({exc})", allow_module_level=True)

from sushi_rig import capture  # noqa: E402


@pytest.mark.parametrize("name", capture.JACK_MODULE_NAMES)
def test_the_module_level_names_the_engine_uses_exist(name):
    assert hasattr(jack, name)


@pytest.mark.parametrize("name", capture.JACK_CLIENT_ATTRIBUTES)
def test_the_client_attributes_the_engine_uses_exist(name):
    assert hasattr(jack.Client, name)


@pytest.mark.parametrize("name", capture.JACK_OWN_PORT_ATTRIBUTES)
def test_the_port_attributes_the_engine_uses_exist(name):
    assert hasattr(jack.OwnPort, name)


def test_load_jack_returns_the_module_that_imported():
    assert capture.load_jack() is jack


def test_without_a_server_the_engine_says_so_instead_of_starting_one():
    """`no_start_server=True` is what makes this a refusal and not a jackd
    launched behind the player's back. It asserts the refusal only where there
    is nothing to connect to, as in CI. On a machine with a server running (the
    rig, under pw-jack) a client does connect; the probe is closed and the test
    claims nothing, rather than skipping, since a skip fails the container job."""
    try:
        client = jack.Client("sushi-rig-capture-test", no_start_server=True)
    except jack.JackError:
        pass
    else:
        client.close()
        return
    from sushi_rig.take import DRY, Stem

    engine = capture.CaptureEngine(48000, 256)
    with pytest.raises(capture.CaptureError, match="no JACK or PipeWire server"):
        engine.start(((Stem("guitar", DRY, "Audient iD14:capture_AUX0"), 1),), lambda block: 0)
