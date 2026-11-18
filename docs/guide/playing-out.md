# Playing out

How to control the rig from your phone, at home or anywhere else: start the
agent for the phone, get the phone and the laptop onto one network, and keep
the laptop playing with its lid closed.

The phone opens the **stage** screen: see [Playing live](playing-live.md).
The laptop does the sound; the phone is only a remote control, so a phone
that drops off the network for a moment leaves the rig playing.

## Start the agent for the phone

On the laptop:

```bash
sushi-rig agent --phone
```

It prints the address to open on the phone, one for each network the laptop
is on, with the kind of connection beside it:

```
sushi-rig agent 0.1 on http://0.0.0.0:8029  (Ctrl+C to stop)
On the phone, on the same network, open:
  http://192.168.1.150:8029/   Wi-Fi, wlp0s20f3
  http://192.168.1.147:8029/   ethernet, enx00e04c680001
Try them in this order; if none opens, see "Playing out" in the guide.
No password: anyone on these networks can open the page and change the rig.
```

Type the address into the phone's browser exactly as printed, starting
`http://`. The rack page on the laptop shows the same addresses, at the bottom
of its right-hand column ("Agent on …"); it says "for this machine only" when
the agent was started without `--phone`.

Docker's, virtual machines' and VPNs' networks are left out of the list: a
phone is not on those.

### If it says the port is already taken

Only one agent can run at a time. If one is already running (often one started
without `--phone`, which a phone cannot reach), `sushi-rig agent --phone`
says which one it is and gives the command to stop it, for example:

```
sushi-rig: cannot listen on 0.0.0.0:8029: the port is already taken.
Another sushi-rig agent has it (pid 4242), listening on 127.0.0.1.
It listens on this machine only (localhost), which is why a phone cannot reach it.

Stop it (the rig keeps playing): Ctrl+C where it runs, or

  kill 4242

and then start it again for the phone:

  sushi-rig agent --phone
```

If the agent runs as a service, it says so and gives the `systemctl` command
instead.

### As a service

To have the agent start with the laptop, listening for the phone, without a
terminal open:

```bash
sushi-rig agent install-service --phone
systemctl --user daemon-reload
systemctl --user restart sushi-rig-agent.service
```

It prints the phone's addresses when it is done. Use `start` instead of
`restart` the first time. If you reach the laptop by a name of its own, such
as a VPN's, add `--allow-host <that name>` after `--phone`, as for the agent
itself. `sushi-rig agent install-service` without `--phone`
puts it back to this laptop only. The service is not yet recorded as run on a
real machine.

## At home, on the same Wi-Fi

Join the phone to the same Wi-Fi network as the laptop, and open the first
address printed. If the laptop is plugged in with a cable as well, it has two
addresses; either may work, so try both.

If neither opens, the router may be keeping its Wi-Fi devices apart: see
[If the phone cannot reach the laptop](#if-the-phone-cannot-reach-the-laptop).
That is what happened the first time the phone was tried, and what worked was
the phone's own hotspot, next.

## On the phone's hotspot

This is what has worked: the phone was connected this way on 2026-11-18,
after the home router would not let it reach the laptop. It also works
anywhere, with no venue Wi-Fi.

1. On the phone, turn on the hotspot (**Personal Hotspot** on an iPhone,
   **Hotspot and tethering** on Android).
2. Join the laptop to the phone's hotspot like any Wi-Fi network.
3. Start the agent with `--phone` (or, if it was already running for the
   phone, look at the rack page on the laptop), and open the address it gives
   on the phone itself.

The address is on the hotspot's own network:

- **iPhone**: `172.20.10.x`, such as `http://172.20.10.2:8029/`.
- **Android**: it differs from phone to phone and can change each time the
  hotspot starts. Older phones use `192.168.43.x`; newer ones a network such
  as `10.40.226.x`. Use the address the agent prints, not one remembered from
  last time.

Things to know:

- **The hotspot can switch itself off.** Phones turn the hotspot off when
  nothing has used it for a while. Join the laptop soon after turning it on,
  and on Android check **Turn off hotspot automatically** in the hotspot's
  settings. If the laptop drops off, the rig keeps playing; join again and
  reload the page.
- **Mobile data.** The page's traffic between the phone and the laptop stays
  between them and uses no data. Anything the laptop does on the internet
  while joined (updates, cloud sync) does use the phone's data. To be safe,
  turn those off before a gig, or mark the hotspot as metered on the laptop:
  `nmcli connection modify "<hotspot name>" connection.metered yes`.

The two points above are from the phones' own documentation, not yet tried
with this rig.

## On a hotspot the laptop makes

The laptop can make a Wi-Fi network of its own for the phone to join, with no
router or mobile signal at all. With NetworkManager (Ubuntu's own):

```bash
nmcli device wifi hotspot ifname wlp0s20f3 ssid sushi-rig password "choose-8-or-more"
```

Use your Wi-Fi interface's name for `wlp0s20f3` (it is the one `sushi-rig
agent --phone` labels **Wi-Fi**). Join the phone to `sushi-rig`, then open
`http://10.42.0.1:8029/`: NetworkManager gives the laptop that address on a
hotspot it makes.

While it runs, the laptop's Wi-Fi is the hotspot, so the laptop has no
internet over Wi-Fi. The phone may say the network has no internet; keep it
connected anyway. To stop the hotspot: `nmcli connection down Hotspot`.

Not yet tried with this rig.

## If the phone cannot reach the laptop

When the address does not open, open `http://<address>:8029/health` on the
phone. A page of text starting `{"ok": true` means the phone reaches the agent
and the problem is elsewhere; a page that never loads means it does not.
Check these in order.

### The router keeps Wi-Fi devices apart

Many routers have a setting called **AP isolation**, **client isolation** or
**wireless isolation** that stops devices on its Wi-Fi from reaching each
other. It is often on by default, and always on for a **guest network**. The
phone and the laptop can both be on the internet and still not reach each
other.

- In the router's settings, turn the isolation off, or join both to the main
  network rather than the guest one.
- If the laptop is also on a cable, try its **ethernet** address: some routers
  only keep Wi-Fi devices apart from each other.
- At a venue, assume the Wi-Fi isolates. Use the
  [phone's hotspot](#on-the-phones-hotspot) instead.

### The laptop's firewall

Ubuntu's firewall is off unless someone turned it on. To see:

```bash
sudo ufw status
```

If it says `Status: active`, let the phone in on the agent's port:

```bash
sudo ufw allow 8029/tcp
```

That opens it on every network the laptop joins. To take it away again:
`sudo ufw delete allow 8029/tcp`.

### `http://`, not `https://`

The agent speaks plain `http://`. A browser that adds `https://` by itself, or
one with **Always use secure connections** (Chrome) or **HTTPS-Only** (Firefox)
turned on, finds nothing there. Type the address in full, starting `http://`,
and if the browser warns that the site is not secure, choose to continue.

### VPNs and Private Relay

- **A VPN on the phone** can send everything through the VPN, the laptop's
  address too, and the laptop is not there. Turn it off while playing, or turn
  on the VPN app's **local network** or **LAN** access setting if it has one.
- **A VPN on the laptop** can do the same in the other direction, and some
  block the local network outright. Turn it off while playing.
- **Opening the page by the VPN's name** for the laptop, rather than an
  address, is refused until the agent is told that name:
  `sushi-rig agent --phone --allow-host <that name>`. An address, like every
  one `--phone` prints, always works.
- **iCloud Private Relay** on an iPhone should leave local addresses alone. If
  Safari still will not open the page, turn off **Limit IP Address Tracking**
  for that Wi-Fi network (Settings, Wi-Fi, the (i) beside it) to rule it out.

These are what each is documented to do, not yet tried with this rig.

## With the lid closed

To play with the laptop shut, on the floor or in a bag, the lid must not send
it to sleep, and its Wi-Fi must stay awake for the phone. **None of this is
yet tried on a real laptop with this rig.** Do the five-minute test at the end
before relying on it.

`sushi-rig doctor` says what closing the lid does on this laptop (`lid`) and
whether Wi-Fi power saving is on (`wifi-power-save`).

### The lid must not suspend the laptop

Tell the system to do nothing when the lid closes, with a file of its own:

```bash
sudo mkdir -p /etc/systemd/logind.conf.d
printf '[Login]\nHandleLidSwitch=ignore\nHandleLidSwitchExternalPower=ignore\nHandleLidSwitchDocked=ignore\n' | sudo tee /etc/systemd/logind.conf.d/lid.conf
```

Then **reboot**. Do not restart `systemd-logind` instead: on a desktop, that
can end the graphical session, and the rig with it.

To undo it: `sudo rm /etc/systemd/logind.conf.d/lid.conf`, and reboot.

### Automatic suspend off

In **Settings**, **Power**, turn **Automatic Suspend** off, or the laptop
sleeps after a while of no keyboard or mouse, lid open or not. The screen
going blank, or locking, does not matter: the rig plays on behind it.

### Wi-Fi power saving off

With power saving on, the Wi-Fi dozes between messages, and the phone's
changes can arrive late or the page lose the laptop for a moment.

For this session only (use your Wi-Fi interface's name; `sushi-rig agent
--phone` labels it **Wi-Fi**):

```bash
sudo iw dev wlp0s20f3 set power_save off
```

For good, with NetworkManager: make a file
`/etc/NetworkManager/conf.d/wifi-powersave.conf` holding

```
[connection]
wifi.powersave = 2
```

and restart NetworkManager, which drops the Wi-Fi for a few seconds:

```bash
sudo systemctl restart NetworkManager
```

`2` means off (`3` is on; `0` and `1` leave it to something else). Ubuntu ships
a file of the same kind, `default-wifi-powersave-on.conf`, that sets `3`;
these files are read in alphabetical order and the last one wins, so
`wifi-powersave.conf` comes after it and its `2` is what counts. To check:
`iw dev wlp0s20f3 get power_save` should say `Power save: off`.

### Heat

A shut laptop cools less well. Stand it on something hard, with the vents
clear, never on a cushion or in a closed bag. If it gets too hot it slows
itself down, and the sound starts to drop out: watch **XRUNS** on the stage
screen while you play. If it starts rising, open the lid or give the laptop
air.

### Start the agent before you close the lid

Start the agent, and the rig, with the lid open, and check the phone reaches
it, then close the lid. The service is the reliable way, since it starts with
the laptop and needs no terminal:
[`sushi-rig agent install-service --phone`](#as-a-service).

### The five-minute test

Before a gig, once:

1. Start the rig, and open the stage screen on the phone.
2. Close the lid.
3. Play for five minutes.
4. Check: the sound never stopped; a change on the phone (a bypass, the master
   volume) still takes; **XRUNS** has not climbed.

If any of those fails, open the lid and look at what `sushi-rig doctor` says.

## No password

Started with `--phone`, the agent has no password: **anyone on the same network
can open the page and change the rig**, or stop it. Use it on a network you
trust: your own Wi-Fi, or your phone's hotspot with a password of its own. At a
venue, do not join the venue's Wi-Fi with the agent listening; use the phone's
hotspot. When you are done, stop the agent, or start it again without
`--phone`.
