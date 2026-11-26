#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Connectivity probe for an LG monitor's RS-232C control port.

Protocol: ASCII, 9600 bps, 8N1, no parity, 1 stop bit (LG manual,
"EXTERNAL CONTROL DEVICE SETUP", 40584909_43ud79.pdf).

    request:  [cmd1][cmd2] [set-id] [data]\r
    ack:      [cmd2] [set-id] [OK|NG] [data]\r

Set ID 1 is the factory default; ID 0 broadcasts without a reply.
Data 0xFF means "read current value"; unsupported reads answer NG.

Volume Mute (`ke`) is inverted with respect to intuition: 00 = Mute ON,
01 = Mute Off.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass

import serial

BAUD = 9600
ACK_END = b"\r"
READ = "ff"
MUTE_ON = "00"
MUTE_OFF = "01"


@dataclass
class Ack:
    command: str
    set_id: str
    status: str
    data: str
    latency_ms: float | None

    def __str__(self) -> str:
        latency = f"{self.latency_ms:.1f} ms" if self.latency_ms is not None else "no reply"
        return f"{self.status:<2} data={self.data} ({latency})"


def send(port: serial.Serial, command: str, data: str, set_id: int = 1, timeout: float = 1.0) -> Ack:
    """Send one command frame and parse the acknowledgement."""
    frame = f"{command} {set_id:02X} {data}\r".encode("ascii")
    port.reset_input_buffer()
    started = time.perf_counter()
    port.write(frame)
    port.timeout = timeout
    raw = port.read_until(ACK_END)
    elapsed_ms = (time.perf_counter() - started) * 1000

    text = raw.decode("ascii", errors="replace").strip()
    # Expected layout: [cmd2] [set-id] [OK|NG] [data]  ->  "a 01 NG ff"
    if len(text) < 7:
        return Ack(command[1], f"{set_id:02X}", "NO", text, None)

    status = text[5:7] if text[5:7] in ("OK", "NG") else "??"
    return Ack(text[0], text[2:4], status, text[7:], elapsed_ms)


def run_probe(device: str, set_id: int, repeats: int) -> int:
    port = serial.Serial(device, BAUD, timeout=1.0, bytesize=8, parity="N", stopbits=1)
    try:
        print(f"port      : {device}")
        print(f"baud      : {BAUD} 8N1")
        print(f"set id    : {set_id}")
        print()

        # 1. Read query: proves the monitor parses and answers frames. NG is a
        #    valid answer here (several firmware builds reject read mode).
        power = send(port, "ka", READ, set_id)
        print(f"power read   ka {set_id:02X} ff : {power}")

        # 2. Idempotent write: Mute Off is safe to repeat forever, so it proves
        #    command execution without leaving the monitor changed.
        mute = send(port, "ke", MUTE_OFF, set_id)
        print(f"mute off     ke {set_id:02X} {MUTE_OFF} : {mute}")

        # 3. Stability: repeated round trips, latency measured from frame write
        #    to the terminating carriage return.
        latencies = []
        for _ in range(repeats):
            ack = send(port, "ke", MUTE_OFF, set_id)
            if ack.status != "NO" and ack.latency_ms is not None:
                latencies.append(ack.latency_ms)

        print()
        print(f"replies      : {len(latencies)}/{repeats}")
        if latencies:
            print(f"latency      : min {min(latencies):.1f} ms / max {max(latencies):.1f} ms")

        link_up = power.status != "NO" or mute.status != "NO"
        control = mute.status == "OK"
        print()
        print(f"link         : {'OK' if link_up else 'NO REPLY'}")
        print(f"control      : {'OK' if control else 'NOT CONFIRMED'}")
        return 0 if link_up and control else 1
    finally:
        port.close()


def mute_cycle(port: serial.Serial, set_id: int, seconds: float) -> bool:
    """Mute, hold the muted state for `seconds`, then always unmute."""
    muted = send(port, "ke", MUTE_ON, set_id)
    print(f"mute on      ke {set_id:02X} {MUTE_ON} : {muted}")
    started = time.perf_counter()
    try:
        time.sleep(seconds)
    finally:
        held = time.perf_counter() - started
        unmuted = send(port, "ke", MUTE_OFF, set_id)
        print(f"mute off     ke {set_id:02X} {MUTE_OFF} : {unmuted}")
        print(f"estado mute  : {held:.2f} s")
    return muted.status == "OK" and unmuted.status == "OK"


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe an LG monitor's RS-232C control port.")
    parser.add_argument("--device", default="/dev/ttyUSB0")
    parser.add_argument("--set-id", type=int, default=1, help="monitor set id, 1-10 (default 1)")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--mute-cycle", type=float, metavar="SECONDS",
                        help="mute, wait SECONDS, then unmute (always unmutes)")
    parser.add_argument("--command", help="ad-hoc command pair, e.g. 'ka'")
    parser.add_argument("--data", default=READ, help="data byte in hex (default ff = read)")
    args = parser.parse_args()

    if args.command or args.mute_cycle is not None:
        port = serial.Serial(args.device, BAUD, timeout=1.0, bytesize=8, parity="N", stopbits=1)
        try:
            if args.command:
                print(send(port, args.command, args.data, args.set_id))
                return 0
            return 0 if mute_cycle(port, args.set_id, args.mute_cycle) else 1
        finally:
            port.close()

    try:
        return run_probe(args.device, args.set_id, args.repeats)
    except serial.SerialException as error:
        print(f"serial error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
