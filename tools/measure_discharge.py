#!/usr/bin/env python3
"""Log the M5's battery discharge curve to CSV until the stick dies.

Phase 2 of docs/superpowers/specs/2026-08-11-repair-and-battery-guard-design.md:
BatteryGuard's thresholds must come from THIS data, not from a guess.

    systemctl --user stop familiar          # only one client can hold the link
    uv run --with bleak python tools/measure_discharge.py \
        --address F0:16:1D:03:4C:FA --out discharge.csv

Start from a FULL charge, then UNPLUG. Plugging in charges the stick and
destroys the measurement -- which is also why serial is not an option here.
Expect 1-2 hours on the bare internal cell.

When it ends, check whether the bond survived:

    bluetoothctl info F0:16:1D:03:4C:FA     # then: familiar doctor

That answer is the point of the exercise. A lost bond confirms the brownout
theory; a surviving bond kills it, and BatteryGuard would have fixed nothing.
"""
import argparse
import asyncio
import csv
import json
import sys
import time

from bleak import BleakClient

NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # write to device
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # notify from device


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--out", default="discharge.csv")
    ap.add_argument("--interval", type=float, default=60.0)
    args = ap.parse_args()

    rows = asyncio.Queue()
    buf = bytearray()

    def on_notify(_c, data):
        # BLE notifications are fragmented: ble_bridge.cpp caps chunks at 180 bytes,
        # but the status reply (320-byte buffer with name, owner, sec, bat{4}, sys{4},
        # stats{5}) exceeds that, so replies always span 2+ notifications. Reassemble
        # on newline boundaries (firmware terminates each reply with \n). Mirrors
        # daemon.py:84-96 for the same line-delimited JSON protocol.
        nonlocal buf
        buf.extend(data)
        # Bound the buffer to prevent unbounded growth if a malformed reply never
        # terminates with \n. A reply that large is corrupt anyway.
        if len(buf) > 8192:
            buf.clear()
            return
        while b"\n" in buf:
            line, _, rest = buf.partition(b"\n")
            buf[:] = rest
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except Exception:
                continue
            if msg.get("ack") == "status":
                rows.put_nowait(msg.get("data", {}).get("bat", {}))

    with open(args.out, "w", newline="") as fh:
        out = csv.writer(fh)
        out.writerow(["iso_time", "mV", "mA", "usb", "pct"])
        fh.flush()

        async with BleakClient(args.address) as client:
            await client.start_notify(NUS_TX, on_notify)
            print(f"connected; logging to {args.out} every {args.interval}s")
            print("UNPLUG THE STICK NOW if it is still on USB.")
            while True:
                await client.write_gatt_char(
                    NUS_RX, b'{"cmd":"status"}\n', response=False)
                try:
                    bat = await asyncio.wait_for(rows.get(), timeout=15)
                except asyncio.TimeoutError:
                    print("no status reply — the stick may have died")
                    break
                stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
                out.writerow([stamp, bat.get("mV"), bat.get("mA"),
                              bat.get("usb"), bat.get("pct")])
                fh.flush()      # the run ends with a power cut; never buffer
                print(f"{stamp}  {bat.get('mV')} mV  {bat.get('mA')} mA  "
                      f"usb={bat.get('usb')}")
                await asyncio.sleep(args.interval)

    print(f"\nlink ended. curve saved to {args.out}")
    print("now check the bond:  bluetoothctl info <MAC> && familiar doctor")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
