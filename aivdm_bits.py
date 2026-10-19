#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aivdm_bits.py
=============
AIVDM (NMEA) payload  <->  AIS source bits.

An AIVDM sentence is:
    !AIVDM,<fragCount>,<fragNum>,<seqId>,<channel>,<payload>,<fillBits>*<checksum>

The <payload> is 6-bit "armored ASCII": each character carries 6 bits.
Expanding it (MSB first, then drop <fillBits> trailing bits) gives the raw AIS
bit sequence that the GMSK modulator transmits.

Dependencies: none (stdlib only).

Usage:
    python aivdm_bits.py                       # decode the built-in sample
    python aivdm_bits.py "!AIVDM,1,1,,B,..."   # decode a sentence from argv
    python aivdm_bits.py --analyze 110011...   # test 6-bit alignment of a raw bit string
"""

import sys


# ----------------------------------------------------------------------------- #
# 6-bit armoring.  Standard table:
#   '0'..'W'  (48..87)  -> 0..39
#   '`'..'w'  (96..119) -> 40..63
# Equivalent rule: subtract 48, and if the result is > 40 subtract another 8.
# ----------------------------------------------------------------------------- #
def char_to_val(c):
    o = ord(c)
    if 48 <= o <= 87:          # '0'..'W'
        return o - 48
    if 96 <= o <= 119:         # '`'..'w'
        return o - 56
    raise ValueError(f"invalid AIS armor char: {c!r} (0x{o:02x})")


def val_to_char(v):
    if not 0 <= v < 64:
        raise ValueError(v)
    return chr(v + 48) if v < 40 else chr(v + 56)


def payload_to_bits(payload, fill=0):
    """6-bit armored payload -> list of 0/1 ints, MSB first, minus <fill> tail bits."""
    bits = []
    for c in payload:
        v = char_to_val(c)
        for k in range(6):
            bits.append((v >> (5 - k)) & 1)
    if fill:
        if fill >= len(bits):
            raise ValueError("fill bits exceed payload length")
        bits = bits[:-fill]
    return bits


def bits_to_payload(bits):
    """Source bits -> (armored payload string, number of fill bits added)."""
    pad = (-len(bits)) % 6
    b = list(bits) + [0] * pad
    out = []
    for i in range(0, len(b), 6):
        v = 0
        for k in range(6):
            v = (v << 1) | b[i + k]
        out.append(val_to_char(v))
    return "".join(out), pad


# ----------------------------------------------------------------------------- #
# Sentence parsing / checksum
# ----------------------------------------------------------------------------- #
def nmea_checksum(sentence):
    """XOR of all chars strictly between '$'/'!' and '*', as 2 hex digits."""
    body = sentence[1:].split("*", 1)[0]
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"{cs:02X}"


def parse_aivdm(sentence):
    """Return dict with fields; verify checksum; return payload bits."""
    s = sentence.strip()
    if not s.startswith(("!AIVDM", "!AIVDO")):
        raise ValueError("not an AIVDM/AIVDO sentence")
    if "*" in s:
        given = s.split("*", 1)[1][:2].upper()
        calc = nmea_checksum(s)
        ok = (given == calc)
    else:
        given = calc = None
        ok = None
    body = s[1:].split("*", 1)[0]
    f = body.split(",")
    # f[0]='AIVDM', 1=fragCount, 2=fragNum, 3=seqId, 4=channel, 5=payload, 6=fill
    return {
        "frag_count": int(f[1]),
        "frag_num": int(f[2]),
        "seq_id": f[3],
        "channel": f[4],
        "payload": f[5],
        "fill": int(f[6]) if len(f) > 6 and f[6] else 0,
        "checksum_given": given,
        "checksum_calc": calc,
        "checksum_ok": ok,
        "bits": payload_to_bits(f[5], int(f[6]) if len(f) > 6 and f[6] else 0),
    }


def msg_type(bits):
    """First 6 bits = AIS message type."""
    if len(bits) < 6:
        return None
    v = 0
    for k in range(6):
        v = (v << 1) | bits[k]
    return v


# ----------------------------------------------------------------------------- #
# Reverse-engineering helper for an unknown bit string
# ----------------------------------------------------------------------------- #
def analyze_layout(bits, max_train=40, max_tail=40, bit_offset=0):
    """Assume: [train][6-bit-aligned payload][tail]. Brute-force the split.

    Reports every (train_len, tail_len) where (len - train - tail) is a positive
    multiple of 6 AND the extracted payload decodes to a valid AIS message type.
    This is how you test whether a mystery bit string is a framed AIVDM payload.
    """
    n = len(bits)
    hits = []
    for tr in range(0, max_train + 1):
        for ta in range(0, max_tail + 1):
            data_len = n - tr - ta
            if data_len <= 0 or data_len % 6 != 0:
                continue
            seg = bits[tr:tr + data_len]
            mt = msg_type(seg)
            if mt in (1, 2, 3, 4, 5, 18, 19, 21, 24):   # plausible types
                hits.append((tr, ta, data_len, data_len // 6, mt))
    return hits


# ----------------------------------------------------------------------------- #
# Main
# ----------------------------------------------------------------------------- #
SAMPLE = ("!AIVDM,1,1,,B,56:B?8P000000000000P4UuQDqw3O?CH0000001J"
          "000004000312CQj0B@000000000000,0*23")


def main(argv):
    if len(argv) >= 2 and argv[1] == "--analyze":
        raw = "".join(c for c in argv[2] if c in "01")
        bits = [int(c) for c in raw]
        print(f"length = {len(bits)} bits")
        hits = analyze_layout(bits)
        if not hits:
            print("no 6-bit-aligned payload found under [train][payload][tail].")
        for tr, ta, dl, chars, mt in hits[:40]:
            print(f"  train={tr:2d} tail={ta:2d} payload={dl:3d} bits "
                  f"({chars} chars)  msg_type={mt}")
        return 0

    sentence = argv[1] if len(argv) >= 2 else SAMPLE
    info = parse_aivdm(sentence)
    print(f"channel      : {info['channel']}")
    print(f"payload      : {info['payload']}  ({len(info['payload'])} chars)")
    print(f"fill bits    : {info['fill']}")
    print(f"checksum     : given={info['checksum_given']} "
          f"calc={info['checksum_calc']} ok={info['checksum_ok']}")
    bits = info["bits"]
    print(f"source bits  : {len(bits)} bits")
    print(f"msg type     : {msg_type(bits)}")
    print(f"bit string   : {''.join(str(b) for b in bits)}")

    # round-trip check
    pl, pad = bits_to_payload(bits)
    print(f"round-trip   : payload={pl} fill={pad}  "
          f"({'OK' if pl == info['payload'] else 'MISMATCH'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
