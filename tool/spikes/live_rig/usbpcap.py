"""Extract USB control transfers from a USBPcap pcapng, without tshark.

Written to check the Windows capture's decoding independently rather than take
it on trust. Streams the file, so the multi-hundred-megabyte captures are fine.

pcapng: blocks of [type u32][total_len u32][body][total_len u32]. Only
Enhanced Packet Blocks (0x06) carry packets; the Section Header Block's
byte-order magic gives endianness.

USBPcap packet header is 27 bytes, with one extra byte (`stage`) for control
transfers:
    u16 headerLen, u64 irpId, u32 status, u16 function, u8 info,
    u16 bus, u16 device, u8 endpoint, u8 transfer, u32 dataLength [, u8 stage]
transfer: 0 iso, 1 interrupt, 2 control, 3 bulk.
stage: 0 setup, 1 data, 2 status, 3 complete.

For a control transfer the 8-byte setup packet follows the header on the SETUP
stage: bmRequestType, bRequest, wValue, wIndex, wLength. Anything after that in
the same packet is payload.
"""
import struct
import sys
from collections import defaultdict

SHB = 0x0A0D0D0A
EPB = 0x00000006
CONTROL = 2


def blocks(path):
    """Yield (block_type, body) for every block, handling endianness.

    Each block is [type u32][total_len u32][body][total_len u32]. The trailing
    length must be consumed too -- leaving it unread desynchronises every
    subsequent block by four bytes, which looks exactly like a file containing
    nothing but a header.
    """
    endian = "<"
    with open(path, "rb") as f:
        while True:
            head = f.read(8)
            if len(head) < 8:
                return
            btype, blen = struct.unpack(endian + "II", head)
            if btype == SHB:
                rest = f.read(blen - 8)          # body remainder + trailer
                if len(rest) < blen - 8:
                    return
                magic = rest[:4]
                if magic == b"\x4d\x3c\x2b\x1a":
                    endian = "<"
                elif magic == b"\x1a\x2b\x3c\x4d":
                    endian = ">"
                else:
                    raise SystemExit(f"not a pcapng byte-order magic: {magic!r}")
                yield btype, rest[:blen - 12]
                continue
            if blen < 12:
                return
            rest = f.read(blen - 8)              # body + trailing length
            if len(rest) < blen - 8:
                return
            yield btype, rest[:blen - 12]


def packets(path):
    """Yield (timestamp_raw, packet_bytes) from Enhanced Packet Blocks."""
    for btype, body in blocks(path):
        if btype != EPB or len(body) < 20:
            continue
        _iface, ts_hi, ts_lo, cap_len, _orig_len = struct.unpack("<IIIII", body[:20])
        data = body[20:20 + cap_len]
        yield (ts_hi << 32) | ts_lo, data


def control_setups(path, device=None):
    """Yield dicts for each control SETUP packet, with any payload attached."""
    for ts, pkt in packets(path):
        if len(pkt) < 27:
            continue
        header_len = struct.unpack("<H", pkt[:2])[0]
        if header_len < 27 or header_len > len(pkt):
            continue
        (_hl, irp, _status, _func, _info, _bus, dev,
         _ep, transfer, data_len) = struct.unpack("<HQIHBHHBBI", pkt[:27])
        if transfer != CONTROL:
            continue
        if device is not None and dev != device:
            continue
        stage = pkt[27] if header_len >= 28 else None
        if stage != 0:                       # SETUP only; payload rides along
            continue
        setup = pkt[header_len:header_len + 8]
        if len(setup) < 8:
            continue
        bm, br, wval, widx, wlen = struct.unpack("<BBHHH", setup)
        payload = pkt[header_len + 8:]
        yield {"ts": ts, "irp": irp, "dev": dev, "bmRequestType": bm,
               "bRequest": br, "wValue": wval, "wIndex": widx,
               "wLength": wlen, "data": payload, "data_len": data_len}


def main():
    path = sys.argv[1]
    device = int(sys.argv[2]) if len(sys.argv) > 2 else None
    want = sys.argv[3] if len(sys.argv) > 3 else "summary"

    rows = []
    for r in control_setups(path, device):
        if r["bmRequestType"] not in (0x21, 0xA1):
            continue
        rows.append(r)

    if not rows:
        print("no class control transfers found")
        return

    t0 = rows[0]["ts"]
    if want == "summary":
        tally = defaultdict(lambda: [0, set()])
        for r in rows:
            entity = r["wIndex"] >> 8
            sel = r["wValue"] >> 8
            ch = r["wValue"] & 0xFF
            key = (r["bmRequestType"], entity, sel, ch, r["wLength"])
            tally[key][0] += 1
            if r["bmRequestType"] == 0x21 and r["data"]:
                tally[key][1].add(r["data"][:r["wLength"]].hex())
        print(f"{len(rows)} class control transfers, device {device}")
        print(f"{'dir':<4} {'entity':>6} {'sel':>5} {'ch':>3} {'len':>4} {'count':>7}  values")
        for key in sorted(tally, key=lambda k: (k[1], k[2], k[3])):
            bm, entity, sel, ch, wlen = key
            n, vals = tally[key]
            d = "OUT" if bm == 0x21 else "IN"
            v = ",".join(sorted(vals)[:8]) + ("..." if len(vals) > 8 else "")
            print(f"{d:<4} 0x{entity:02x}   0x{sel:02x}  {ch:>3} {wlen:>4} {n:>7}  {v}")
    else:
        entity_f, sel_f = (int(x, 16) for x in want.split(":"))
        print(f"{'t(s)':>9} {'dir':<4} {'ch':>3} {'len':>4}  data")
        for r in rows:
            if (r["wIndex"] >> 8) != entity_f or (r["wValue"] >> 8) != sel_f:
                continue
            d = "OUT" if r["bmRequestType"] == 0x21 else "IN"
            print(f"{(r['ts'] - t0) / 1e6:9.3f} {d:<4} {r['wValue'] & 0xFF:>3} "
                  f"{r['wLength']:>4}  {r['data'][:r['wLength']].hex()}")


if __name__ == "__main__":
    main()
