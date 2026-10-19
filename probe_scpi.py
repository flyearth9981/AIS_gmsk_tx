#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_scpi.py
=============
Find which TCP port on an instrument speaks SCPI (for a Keysight/Agilent signal
generator such as the N5182B).

It connects to each candidate port, sends "*IDN?\\n", and reports whatever comes
back.  The instrument's SCPI-RAW socket is conventionally port 5025.

Usage:
    python probe_scpi.py 192.254.4.100
    python probe_scpi.py 192.254.4.100 5025 4880 111   # explicit ports
"""

import socket
import sys

# Common instrument-LAN services. 5025 = Keysight/Agilent SCPI-RAW (the one you
# want); 4880 = HiSLIP; 111 = VXI-11 portmapper; others are just for context.
CANDIDATE_PORTS = [5025, 4880, 111, 5024, 5023, 5000, 23]


def probe(ip, port, timeout=2.5):
    """Return (state, text). state in {'open-scpi','open-non-scpi','closed'}."""
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
    except OSError as e:
        return ("closed", str(e))
    try:
        s.settimeout(timeout)
        resp = b""
        try:
            s.sendall(b"*IDN?\n")
            resp = s.recv(512)
        except OSError:
            resp = b""
        if resp:
            return ("open-scpi", resp.decode("ascii", "replace").strip())
        return ("open-non-scpi", "<connected but no SCPI reply to *IDN?>")
    finally:
        try:
            s.close()
        except OSError:
            pass


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("example: python probe_scpi.py 192.254.4.100")
        return 1
    ip = sys.argv[1]
    ports = [int(p) for p in sys.argv[2:]] or CANDIDATE_PORTS
    print(f"probing {ip} ...")
    hits = []
    for port in ports:
        state, text = probe(ip, port)
        if state == "open-scpi":
            print(f"  {port:5d}  *** SCPI ***  -> {text!r}")
            hits.append(port)
        elif state == "open-non-scpi":
            print(f"  {port:5d}  open, not SCPI  -> {text}")
        else:
            print(f"  {port:5d}  closed/filtered  ({text})")
    print()
    if hits:
        print(f"=> use port {hits[0]}:  "
              f"python ais_gmsk_n5182a.py --probe --ip {ip} --port {hits[0]}")
    else:
        print("=> no SCPI responder found. Check the IP (front panel: "
              "Utility > I/O Config > LAN), the cable, and that the LAN/SCPI "
              "socket service is enabled on the instrument.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
