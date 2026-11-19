"""The rack's "Agent on …" line names the phone's URL (#181).

Two layers, as test_ui.py has them: rig.js's `agentLine` imported into a bare
page, then the rack in Chromium against a real agent over `ui_fake.py`'s
fake rig, its `/health` saying where it listens. What stands in: the agent's
bind (`Agent.listening`, which `serve` sets) and the laptop's Wi-Fi and
ethernet (`Agent.listen_interfaces`). Not a real phone or LAN.
"""

from __future__ import annotations

import pytest

pytest.importorskip("aiohttp")

from test_ui import Watched, blank, browser, expect, rigjs, served  # noqa: E402,F401
from ui_fake import Served  # noqa: E402

from sushi_rig.reach import Interface  # noqa: E402

LAPTOP = [Interface("wlp0s20f3", "192.168.1.150", "Wi-Fi"), Interface("enx00e04c680001", "192.168.1.147", "ethernet")]


def test_the_agent_line_names_the_phones_urls_or_says_it_is_this_machines_alone(blank):
    out = rigjs(blank, """
      const urls = [{url: 'http://192.168.1.150:8029/', interface: 'wlp0s20f3', kind: 'Wi-Fi'},
                    {url: 'http://192.168.1.147:8029/', interface: 'enx0', kind: 'ethernet'}];
      return {
        old: R.agentLine(null, 'localhost:8029'),
        local: R.agentLine({host: '127.0.0.1', port: 8029, network: false, urls: []}, 'localhost:8029'),
        phone: R.agentLine({host: '0.0.0.0', port: 8029, network: true, urls}, '192.168.1.150:8029'),
        none: R.agentLine({host: '0.0.0.0', port: 8029, network: true, urls: []}, 'localhost:8029'),
        junk: R.agentLine({network: true, urls: [null, {url: 5}]}, 'h'),
      };""")
    assert out["old"] == {"text": "Agent on localhost:8029. Phone and laptop see the same rig.", "urls": [], "after": ""}
    assert "for this machine only: a phone cannot reach it" in out["local"]["text"]
    assert "sushi-rig agent --phone" in out["local"]["text"]
    assert out["phone"]["text"] == "Agent on 192.168.1.150:8029. On a phone on the same network, open"
    assert out["phone"]["urls"] == [{"url": "http://192.168.1.150:8029/", "label": "Wi-Fi, wlp0s20f3"},
                                    {"url": "http://192.168.1.147:8029/", "label": "ethernet, enx0"}]
    assert "No password" in out["phone"]["after"]
    assert "no network address" in out["none"]["text"] and out["none"]["urls"] == []
    assert out["junk"]["urls"] == [] and "no network address" in out["junk"]["text"]


@pytest.fixture
def phone_served():
    with Served() as made:
        made.agent.listening = ("0.0.0.0", 8029)
        made.agent.listen_interfaces = lambda: (LAPTOP, [])
        yield made


def test_the_rack_shows_the_phones_url_when_the_agent_listens_on_the_network(browser, phone_served):
    page = Watched(browser, phone_served, "/#rack")
    p = page.page
    line = p.locator("p.agent")
    expect(line).to_contain_text("On a phone on the same network, open http://192.168.1.150:8029/ (Wi-Fi, wlp0s20f3)"
                                 " or http://192.168.1.147:8029/ (ethernet, enx00e04c680001).")
    expect(line).to_contain_text("No password")
    expect(line.get_by_role("link", name="http://192.168.1.150:8029/")).to_have_attribute(
        "href", "http://192.168.1.150:8029/")
    page.close()


def test_the_rack_says_localhost_only_when_it_is(browser, served):
    served.agent.listening = ("127.0.0.1", 8029)
    page = Watched(browser, served, "/#rack")
    expect(page.page.locator("p.agent")).to_contain_text("for this machine only: a phone cannot reach it")
    page.close()
