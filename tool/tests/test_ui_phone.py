"""The rack's "Agent on …" line names the phone's URL (#181).

Two layers, as test_ui.py has them: rig.js's `agentLine` imported into a bare
page, then the rack in Chromium against a real agent over `ui_fake.py`'s
fake rig, its `/health` saying where it listens. What stands in: the agent's
bind (`Agent.listening`, which `serve` sets) and the laptop's Wi-Fi and
ethernet (`Agent.listen_interfaces`). Not a real phone or LAN.
"""

from __future__ import annotations

import re

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
    # A few words on the line (#209); what they mean is its title.
    assert out["old"] == {"text": "Agent on localhost:8029", "urls": [], "after": "",
                          "title": "Phone and laptop see the same rig."}
    assert out["local"]["text"] == "This machine only"
    assert "for this machine only: a phone cannot reach it" in out["local"]["title"]
    assert "sushi-rig agent --phone" in out["local"]["title"]
    assert out["phone"]["text"] == "Phone:"
    assert out["phone"]["title"].startswith("Agent on 192.168.1.150:8029. On a phone on the same network, open")
    assert out["phone"]["urls"] == [{"url": "http://192.168.1.150:8029/", "label": "Wi-Fi, wlp0s20f3"},
                                    {"url": "http://192.168.1.147:8029/", "label": "ethernet, enx0"}]
    assert "No password" in out["phone"]["title"]
    assert out["none"]["text"] == "No network" and out["none"]["urls"] == []
    assert "no network address" in out["none"]["title"]
    assert out["junk"]["urls"] == [] and "no network address" in out["junk"]["title"]


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
    expect(line).to_have_text("Phone: http://192.168.1.150:8029/, http://192.168.1.147:8029/")
    expect(line).to_have_attribute("title", re.compile("On a phone on the same network.*No password"))
    wifi = line.get_by_role("link", name="http://192.168.1.150:8029/")
    expect(wifi).to_have_attribute("href", "http://192.168.1.150:8029/")
    expect(wifi).to_have_attribute("title", "Wi-Fi, wlp0s20f3")
    expect(line.get_by_role("link", name="http://192.168.1.147:8029/")).to_have_attribute(
        "title", "ethernet, enx00e04c680001")
    page.close()


def test_the_rack_says_localhost_only_when_it_is(browser, served):
    served.agent.listening = ("127.0.0.1", 8029)
    page = Watched(browser, served, "/#rack")
    line = page.page.locator("p.agent")
    expect(line).to_have_text("This machine only")
    expect(line).to_have_attribute("title", re.compile("for this machine only: a phone cannot reach it"))
    page.close()
