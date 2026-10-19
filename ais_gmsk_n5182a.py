#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ais_gmsk_n5182a.py
==================
AIVDM (NMEA AIS sentence)  ->  GMSK baseband  ->  Agilent/Keysight N5182A MXG.

Flow (STANDARD AIS transmit chain, the default):
    !AIVDM,...  --> 6-bit payload expansion --> AIS source bits (LOGICAL order)
                --> PER-BYTE BIT REVERSAL -> VDL wire order   [M.1371-5 sec.3.3.7]
                --> HDLC framing: ramp(8) + training(24) + start 0x7E
                                  + bit-stuffed(data+CRC) + end 0x7E + buffer(24)
                --> NRZI (differential encoding BEFORE GMSK -- M.1371 uses NRZI)
                --> GMSK (BT=0.4, h=0.5, 9600 bps, +/-2400 Hz)
                --> 16-bit I/Q file: BIG-endian, interleaved I then Q, NO header
                --> SCPI over raw socket (port 5025) to the generator.

The "raw" waveform (--which raw) skips framing+NRZI and is for debugging only.

CONVENTIONS (per ITU-R M.1371, as supplied):
  * Modulation index ....... 0.5                             [CONFIRMED, sec.2.3.2]
  * Bit rate ............... 9600 bit/s +/- 50 ppm           [CONFIRMED, sec.2.4]
  * Gaussian BT ............ 0.4   [KNOWN, HIGH; not in the supplied excerpt]
  * Training sequence ...... 24 bits alternating 0101...    [CONFIRMED, sec.2.5]
                             (may start with 0 or 1 because of NRZI)
  * NRZI polarity .......... a 0 bit -> level TRANSITION, a 1 bit -> no change
                                                            [CONFIRMED, sec.2.6]
  * NRZI scope ............. the WHOLE serialized frame, including the preamble
                             and the 0x7E flags (a receiver deframes on
                             NRZI-DECODED bits, so the flags are inside the NRZI
                             domain).                       [KNOWN, MED-HIGH]
  * Data byte order ......... the AIVDM payload carries the LOGICAL bit order
                             (MSB-first per message table, what every AIVDM
                             decoder reads).  On the VDL each 8-bit byte is sent
                             LSB-FIRST, i.e. EVERY BYTE IS BIT-REVERSED on the
                             wire.  sec.3.3.7, worked example: logical byte
                             "TTTTTTDI" is transmitted as "IDTTTTTT".
                             [CONFIRMED, sec.3.3.7 + the ITU worked example]
  * Packet structure ........ ramp(8) + training(24) + flag(8) + data + FCS(16)
                             + flag(8) + buffer(24).  256 bits for a 168-bit
                             message, 512 bits for a 424-bit type 5.
                                                        [CONFIRMED, sec.3.2.2.9]

STANDARD PACKET (256 bits = 26.667 ms slot, per the supplied M.1371 excerpt):
    bit 0-7    ramp       T0..T_rs (0.000-0.832 ms); amplitude 0->1 rides here
    bit 8-31   training   24 bits alternating 0101... (sec.2.5); T_rs..T2
    bit 32-39  start flag 0x7E (HDLC); T2..T_s
    bit 40-... data       168 bits default (bit-stuffed between the flags)
    ...        CRC        16 bits (ISO/IEC 13239 -> poly x^16+x^12+x^5+1, init 1s)
    bit 224-231 end flag  0x7E (HDLC); transmission ends by T3
    bit 232-255 buffer    24 bits time-filler; RF down-ramp (1 ms) during T3..T4
                          (the buffer ABSORBS bit-stuffing expansion so the slot
                          stays 256 bits -- "比特填充距离延迟". [INFERRED, MED])

  Slot timing: T0=0.000, T_rs=0.832, T1=1.000, T2=3.328, T_s=4.160,
               T3=24.160, T4=T3+1.000, T5=26.670 ms  (1 bit = 0.104167 ms)
  The 8-bit ramp is the FIRST 8 bits of the 256-bit slot (it does NOT extend the
  slot).  The amplitude envelope is applied in the IQ domain over those bit times.

CONFIRMED against the supplied text:
  modulation index 0.5 (2.3.2) | 9600 bit/s (2.4) | BT=0.4 | training 24b 0101
  (2.5) | NRZI: 0 -> transition (2.6) | CRC poly + init=1 | packet=256b | ramp=8b
  | slot timing T0..T5 | buffer=24b

FILE FORMAT (confirmed against the Keysight "Creating and Downloading Waveform
Files" guide):
  * 16-bit signed 2's complement, interleaved I then Q, BIG-endian byte order.
  * The I/Q file has NO header: the file header (sample rate, marker polarity)
    and the marker file are SEPARATE files the instrument auto-creates.  The old
    244-byte embedded header was WRONG and caused "DAC over range" (-628).

CHECKED AGAINST ITU-R M.1371-5 (2026-10-19):
  * GMSK premod filter sigma = sqrt(ln2)/(2*pi*BT*Rb).  An earlier version dropped
    the 2*pi, so the effective BT was ~0.064 instead of 0.4: the preamble
    deviation collapsed to ~37 Hz (should be ~2400 Hz) and no receiver could lock.
    FIXED.  Verified against Table 5 (modulation accuracy): alternating pattern
    -> 1756 Hz (spec 1740 +/- 175), 00001111 -> 2400 Hz (spec 2400 +/- 240).
  * FCS: reflected 0x8408, init 0xFFFF, final complement, LSB-first.  Matches
    ISO/IEC 13239 (HDLC) and the working AIS receiver implementations.  CONFIRMED.
  * Data byte order: per-byte bit reversal required (sec.3.3.7).  FIXED.

STILL UNVERIFIED:
  * Ramp SHAPE (raised-cosine assumed) and whether the 8 ramp bits are alternating
    (assumed) or something else.  [KNOWN, LOW].  The TIMING now follows Table 6:
    full power until bit 233, down-ramp to -50 dBc by bit 241, then RF off.
  * Buffer content: the 24 filler bits' VALUE is not specified; default fill=1.
  * ARB sample rate: it lives in the (separate) header file we do not embed, so
    it is set via :RADio:ARB:HEAD:WRIT (sample rate + runtime scaling) after the
    I/Q download.  This also controls the DAC scaling ("DAC over range").

Run `--selftest` for an offline loopback (frame -> deframe -> CRC -> reverse ->
compare, plus a BT=0.4 matched-filter demodulator).  A real AIS receiver decoding
your waveform (CRC passes, frame syncs, fields sane) is still the only arbiter.

NOTE: --nrzi-invert flips the NRZI polarity and is NON-CONFORMANT; it is kept only
to debug a receiver that is itself non-conformant.  Leave it OFF for standard AIS.

RF frequency is set AUTOMATICALLY from the sentence's AIS channel:
    channel A -> 161.975 MHz   (AIS1)
    channel B -> 162.025 MHz   (AIS2)

--------------------------------------------------------------------------------
!! READ BEFORE TRUSTING THE .wfm PACKER !!
--------------------------------------------------------------------------------
The GMSK modulator follows the well-documented standard AIS parameters; I am
confident in it.  The Keysight binary ".wfm" HEADER, however, I could NOT verify
against primary documents while writing this (the vendor doc hosts were
unreachable).  build_wfm()'s header layout is a best-effort RECONSTRUCTION
[KNOWN, LOW] and may be wrong in field order / marker count / magic string.
Use --dump-file against a KNOWN-GOOD .wfm (Agilent Signal Studio / a real
instrument) and diff before you trust RF.

    python ais_gmsk_n5182a.py --dump-file good.wfm

--------------------------------------------------------------------------------
RF / LEGAL
--------------------------------------------------------------------------------
AIS1/AIS2 are maritime safety-of-life channels.  Emitting into an antenna is
likely illegal where you are and injects false targets into other vessels'
collision-avoidance displays.  RF output stays OFF unless you pass --rf-on, and
the RF port is expected to be cabled into a shielded enclosure or dummy load.

Dependencies: numpy only.  No pyvisa (raw socket).  Framing uses a pure-Python
CRC (no crcmod needed).
"""

import argparse
import socket
import struct
import sys
import time

import numpy as np

# ----------------------------------------------------------------------------- #
# AIVDM sample (channel B -> 162.025 MHz).  Override with --aivdm / --aivdm-file.
# Real capture, MMSI 413437730 ("HAI XUN 07346", destination DINGHAI), repaired to
# a whole 424-bit type-5 message (71 chars + 2 fill = 53 bytes, per sec.3.3.7).
# ----------------------------------------------------------------------------- #
SAMPLE_SENTENCE = (
    "!AIVDM,1,1,,B,56:B?8P000000000000P4UuQDqw3O?CH0000001J"
    "000004000312CQj0B@0000000000000,2*11"
)

# ----------------------------------------------------------------------------- #
# AIS physical-layer constants (ITU-R M.1371-5)
# ----------------------------------------------------------------------------- #
RB = 9600.0        # bit rate [bit/s]
BT = 0.4           # Gaussian bandwidth-time product
H_INDEX = 0.5      # modulation index -> peak deviation h*RB/2 = 2400 Hz
SPS = 10           # samples/symbol -> 96 kSa/s
PEAK_DEV = H_INDEX * RB / 2.0
FS = RB * SPS
L_TAPS = 4

CH_A_FREQ = 161_975_000.0    # AIS1
CH_B_FREQ = 162_025_000.0    # AIS2
POW_DEFAULT = -10.0          # dBm into a dummy load; keep low


# ============================================================================= #
# AIVDM decoding
# ============================================================================= #
def _char_to_val(c):
    o = ord(c)
    if 48 <= o <= 87:
        return o - 48          # '0'..'W' -> 0..39
    if 96 <= o <= 119:
        return o - 56          # '`'..'w' -> 40..63
    raise ValueError(f"invalid AIS armor char: {c!r}")


def _val_to_char(v):
    if not 0 <= v < 64:
        raise ValueError(v)
    return chr(v + 48) if v < 40 else chr(v + 56)


def payload_to_bits(payload, fill=0):
    bits = []
    for c in payload:
        v = _char_to_val(c)
        for k in range(6):
            bits.append((v >> (5 - k)) & 1)
    if fill:
        bits = bits[:-fill]
    return bits


def bits_to_payload(bits):
    pad = (-len(bits)) % 6
    b = list(bits) + [0] * pad
    out = []
    for i in range(0, len(b), 6):
        v = 0
        for k in range(6):
            v = (v << 1) | b[i + k]
        out.append(_val_to_char(v))
    return "".join(out), pad


def nmea_checksum(sentence):
    body = sentence[1:].split("*", 1)[0]
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"{cs:02X}"


def parse_aivdm(sentence):
    s = sentence.strip()
    if not s.startswith(("!AIVDM", "!AIVDO")):
        raise ValueError("not an AIVDM/AIVDO sentence")
    given = calc = None
    ok = None
    if "*" in s:
        given = s.split("*", 1)[1][:2].upper()
        calc = nmea_checksum(s)
        ok = (given == calc)
    body = s[1:].split("*", 1)[0]
    f = body.split(",")
    fill = int(f[6]) if len(f) > 6 and f[6] else 0
    return {
        "channel": f[4],
        "payload": f[5],
        "fill": fill,
        "checksum_given": given,
        "checksum_calc": calc,
        "checksum_ok": ok,
        "bits": payload_to_bits(f[5], fill),
    }


def msg_type(bits):
    if len(bits) < 6:
        return None
    v = 0
    for k in range(6):
        v = (v << 1) | bits[k]
    return v


def reverse_each_byte(bits, pad=True):
    """AIS message bits in LOGICAL order -> VDL wire order.

    ITU-R M.1371-5 sec.3.3.7: "把与每一消息相关的表格中的数据按自上而下的顺序分成
    8 位的字节。每一字节的最低有效位先输出。" -- the message table is divided into
    8-bit bytes top-to-bottom and EACH BYTE'S LSB IS SENT FIRST.  The standard's own
    worked example: logical byte "TTTTTTDI" is transmitted as "IDTTTTTT".

    The AIVDM payload (and every AIVDM decoder) uses the LOGICAL order, MSB-first.
    So before HDLC framing the message must be regrouped into bytes and every byte
    reversed.  Skip this and a receiver gets a frame whose FCS passes but whose
    fields are scrambled (type 5 -> type 10, MMSI 413437730 -> 294801156, ...).

    sec.3.3.7 also pads the last byte with zeros ("最后一个字节中未用的比特应置为零").
    """
    b = list(bits)
    if len(b) % 8:
        if not pad:
            raise ValueError(f"message is {len(b)} bits, not a whole number of bytes")
        n = 8 - (len(b) % 8)
        print(f"[w] message is {len(b)} bits; zero-padding {n} bits to the next "
              f"byte boundary (sec.3.3.7)")
        b += [0] * n
    out = []
    for i in range(0, len(b), 8):
        out.extend(b[i:i + 8][::-1])
    return out


# ============================================================================= #
# Framing helpers (HDLC, ITU-R M.1371)
# ============================================================================= #
def add_bit_stuffing(bits):
    out, ones = [], 0
    for b in bits:
        out.append(b)
        if b == 1:
            ones += 1
            if ones == 5:
                out.append(0)
                ones = 0
        else:
            ones = 0
    return out


def crc16_ais(bits):
    """AIS FCS = CRC-16 per ISO/IEC 13239 (HDLC).

    Reflected polynomial 0x8408 (= reverse of 0x1021 = x^16 + x^12 + x^5 + 1),
    register initialised to 0xFFFF, bits processed one at a time LSB-first, and
    the register ones-complemented at the end (HDLC FCS convention).

    [KNOWN, MED] The reflected form + init=0xFFFF are confirmed by multiple AIS
    sources.  The final complement and the exact transmit bit-order are the
    standard HDLC behaviour but I could NOT verify them against a decoded packet
    here -- if a real receiver rejects the frame, THIS is the prime suspect.
    Returns 16 bits LSB-first, ready to append before bit-stuffing.
    """
    crc = 0xFFFF
    for b in bits:
        crc ^= (b & 1)
        if crc & 1:
            crc = (crc >> 1) ^ 0x8408
        else:
            crc >>= 1
    crc ^= 0xFFFF
    return [(crc >> i) & 1 for i in range(16)]


# Buffer after the end flag: 24 bit-times (T3..T5) of time-filler (bit-stuffing
# distance, repeater delay, jitter).  Content NOT specified in the excerpt.
# Default: fill with 1s (a common idle level); CONFIRM against your reference.
BUFFER_FILL_DEFAULT = 1
BUFFER_BITS_DEFAULT = 24
RAMP_BITS_DEFAULT = 8
DOWN_RAMP_BITS = 10          # ~1.0 ms (T3..T4) at 9600 bps


def build_ais_packet(payload_bits, buffer_bits=BUFFER_BITS_DEFAULT,
                     buffer_fill=BUFFER_FILL_DEFAULT, ramp_bits=RAMP_BITS_DEFAULT):
    """Assemble the standard AIS 256-bit packet (ramp region included so the total
    stays 256; the amplitude envelope itself is applied later in the IQ domain):

        ramp(8) + training(24) + start 0x7E + bit-stuffed(data+CRC)
              + end 0x7E + buffer(24)

    Mapped to the M.1371 slot timing (1 bit = 1/9600 s = 0.104167 ms):
        T0    0.000 ms  slot start, RF power ramp-up begins   (ramp   8 bits)
        T_rs  0.832 ms  training sequence starts              (train 24 bits)
        T1    1.000 ms  RF power and frequency settled
        T2    3.328 ms  start flag starts                     (32 bits in)
        T_s   4.160 ms  start flag ends, data starts          (40 bits in)
        T3   24.160 ms  transmission ends, end flag done      (232 bits in)
        T4   25.160 ms  RF power reaches zero (down ramp T3..T4 = 1.0 ms)
        T5   26.670 ms  slot ends                             (256 bits)

    The ramp region carries the same alternating pattern as the training (an RF
    power ramp must ride on a carrier).  [KNOWN, LOW] whether the ramp bits are
    alternating or something else is not spelled out in the supplied excerpt.
    Bit stuffing applies ONLY to (data + CRC), between the two flags.

    `payload_bits` must already be in VDL wire order (see reverse_each_byte).
    Returns (packet_bits, buffer_len); buffer_len lets the caller place the RF
    down-ramp right after the end flag (Table 6: T3..T4) instead of at the very
    end of the packet.
    """
    ramp = [0, 1] * (ramp_bits // 2)          # 8 bits, alternating
    training = [0, 1] * 12                    # 24 bits, alternating (sec.2.5)
    flag = [0, 1, 1, 1, 1, 1, 1, 0]           # 0x7E
    body = list(payload_bits) + crc16_ais(payload_bits)
    stuffed = add_bit_stuffing(body)
    s = len(stuffed) - len(body)              # bits added by stuffing
    # The buffer absorbs the bit-stuffing expansion ("比特填充距离延迟"), so the
    # total stays a fixed slot length.  [INFERRED, MED] -- nominal buffer = 24 bits
    # when there is no stuffing.  (sec.3.2.2.8 reserves 4 of those 24 bits for
    # stuffing and lets the packet grow beyond that; this model instead keeps the
    # slot fixed.  Decoders do not care -- they deframe on the flags.)
    buffer_len = max(0, buffer_bits - s)
    if s > buffer_bits:
        print(f"[w] bit-stuffing added {s} bits > {buffer_bits}-bit buffer; "
              f"packet exceeds the nominal slot")
    buffered = [buffer_fill] * buffer_len
    return ramp + training + flag + stuffed + flag + buffered, len(buffered)


def apply_nrzi(bits, invert=False):
    """NRZI differential encoder, applied to the serialized frame before GMSK.

    ITU-R M.1371 sec.2.6: "在比特流中遇到 0 时，规定波型是电平发生变化"
    -> a 0 bit causes a level transition; a 1 bit leaves the level unchanged.
    This is the default (invert=False).  invert=True is NON-CONFORMANT and is
    provided only to debug a non-standard receiver.
    """
    out, last = [], 1
    for b in bits:
        transition = (b == 1) if invert else (b == 0)
        if transition:
            last = 1 - last
        out.append(last)
    return out


# ============================================================================= #
# GMSK modulator
# ============================================================================= #
def gmsk_modulate(bits, sps=SPS, bt=BT, h=H_INDEX, rb=RB, span=L_TAPS):
    bits = np.asarray(bits, dtype=float)
    fs = rb * sps
    n = np.arange(-span * sps, span * sps + 1)
    t = n / fs
    # Gaussian premod filter, 3-dB bandwidth B = BT*Rb  ->  sigma = sqrt(ln2)/(2*pi*B).
    # The 2*pi matters: dropping it makes sigma ~6.3x too wide, BT collapses to
    # ~0.064 and the preamble deviation falls to ~37 Hz (a receiver cannot lock).
    sig = np.sqrt(np.log(2.0)) / (2.0 * np.pi * bt * rb)
    g = np.exp(-t ** 2 / (2.0 * sig ** 2))
    g /= g.sum()
    sym = 2.0 * bits - 1.0
    freq_norm = np.convolve(np.repeat(sym, sps), g, mode="same")
    phase = np.cumsum(freq_norm) * (2.0 * np.pi * (h * rb / 2.0) / fs)
    return np.exp(1j * phase).astype(np.complex64)


def peak_deviation_hz(iq, sps=SPS, rb=RB):
    fs = rb * sps
    dphi = np.angle(iq[1:] * np.conj(iq[:-1]))
    return float(np.max(np.abs(dphi * fs / (2.0 * np.pi))))


def apply_ramps(iq, up_bits=RAMP_BITS_DEFAULT, down_start_bit=None,
                down_bits=DOWN_RAMP_BITS, sps=SPS, shape="cos"):
    """Multiply an AMPLITUDE envelope onto the packet IQ (in place, no padding).

    Up-ramp   : first `up_bits` symbol-times go 0 -> 1   (T0..T_rs, 8 bits)
    Down-ramp : starts at `down_start_bit` (the sample index right after the end
                flag, i.e. the start of the buffer) and runs `down_bits` symbol-
                times down to 0; everything after it is held at 0.

    ITU-R M.1371-5 Table 6 (transmitter power/time mask): power stays within
    +1.5/-1 dB until bit 233 (T_E, the end of the end flag), reaches -50 dBc by
    bit 241 (T_F) and stays there until the slot ends at bit 256.  So the
    down-ramp belongs right after the end flag, NOT at the very end of the
    packet -- the trailing buffer is meant to be radiated at <= -50 dBc.

    The ramp is on RF POWER (amplitude).  The exact SHAPE is [KNOWN, LOW] (the
    figure was not supplied); the TIMING follows Table 6.
    """
    n = len(iq)
    w = np.ones(n, dtype=np.float32)
    nu = up_bits * sps
    w[:nu] = (0.5 * (1 - np.cos(np.pi * np.arange(nu) / nu)) if shape == "cos"
              else np.linspace(0.0, 1.0, nu))
    d0 = (n - down_bits * sps) if down_start_bit is None else down_start_bit * sps
    d0 = max(nu, min(int(d0), n))
    d1 = min(n, d0 + down_bits * sps)
    nd = d1 - d0
    if nd > 0:
        w[d0:d1] = (0.5 * (1 + np.cos(np.pi * np.arange(nd) / nd)) if shape == "cos"
                    else np.linspace(1.0, 0.0, nd))
    w[d1:] = 0.0                       # buffer tail radiated at <= -50 dBc
    out = iq * w
    out[0] = 0.0
    out[-1] = 0.0
    return out.astype(np.complex64)


# ============================================================================= #
# Offline self-test (no instrument, no RF)
# ============================================================================= #
def deframe_packet(packet):
    """Independent HDLC deframer for a packet built by build_ais_packet.

    Mirrors what a receiver does: find the start flag, collect bits up to the
    closing flag, remove the stuffed zeros, split off the 16-bit FCS and check
    it.  Returns (data_bits_in_wire_order, crc_ok, stuffing_count).
    """
    FLAG = [0, 1, 1, 1, 1, 1, 1, 0]
    if packet[32:40] != FLAG:
        raise ValueError("no start flag at bit 32")
    i, raw = 40, []
    while i + 8 <= len(packet):
        if raw and packet[i:i + 8] == FLAG:
            break
        raw.append(packet[i])
        i += 1
    else:
        raise ValueError("no closing flag found")
    body, j, ones = [], 0, 0
    while j < len(raw):
        b = raw[j]
        body.append(b)
        j += 1
        if b == 1:
            ones += 1
            if ones == 5:
                j += 1                      # drop the stuffed 0
                ones = 0
        else:
            ones = 0
    data, fcs = body[:-16], body[-16:]
    return data, (crc16_ais(data) == fcs), len(raw) - len(body)


def _gmsk_demod_bits(iq, sps=SPS, bt=BT):
    """Reference receiver: FM discriminator + BT=0.4 matched filter + slicer.
    Returns the recovered pre-NRZI bit levels (one per symbol)."""
    fs = RB * sps
    d = np.angle(iq[1:] * np.conj(iq[:-1]))
    n = np.arange(-4 * sps, 4 * sps + 1)
    t = n / fs
    sig = np.sqrt(np.log(2.0)) / (2.0 * np.pi * bt * RB)
    g = np.exp(-t ** 2 / (2.0 * sig ** 2))
    g /= g.sum()
    y = np.convolve(d, g, mode="same")
    idx = np.arange(0, len(y), sps)
    return (y[idx] > 0).astype(int)


def run_selftest(info, payload_bits, wire_bits, packet, buffer_len):
    """Offline loopback: framing, CRC, byte order and modulation, checked without
    touching the instrument.  Prints PASS/FAIL lines and returns a bool."""
    ok = True
    print("\n=== offline self-test ===")

    # 0. the standard's own worked example (sec.3.3.7): logical "TTTTTTDI" -> wire
    ex = [1, 1, 1, 1, 1, 1, 0, 1]
    want = [1, 0, 1, 1, 1, 1, 1, 1]
    got = reverse_each_byte(ex, pad=False)
    print(f"[0] sec.3.3.7 ex   : logical {ex} -> wire {got} "
          f"{'PASS' if got == want else 'FAIL'}")
    ok &= (got == want)

    # 1. framing + CRC via an independent deframer
    data, crc_ok, stuffing = deframe_packet(packet)
    print(f"[1] deframe        : data={len(data)} bits, stuffing={stuffing}, "
          f"buffer={buffer_len}")
    print(f"    FCS check      : {'PASS' if crc_ok else 'FAIL'}")
    ok &= crc_ok
    same = (data == list(wire_bits))
    print(f"    wire bits match: {'PASS' if same else 'FAIL'}")
    ok &= same

    # 2. byte-order round-trip back to the logical payload
    back = reverse_each_byte(data, pad=False)
    same = (back == list(payload_bits))
    print(f"[2] byte-order rt  : {'PASS' if same else 'FAIL'} "
          f"(logical {len(payload_bits)} bits)")
    ok &= same

    # 3. modulate -> reference demod -> NRZI decode -> compare with the packet
    tx = np.array(apply_nrzi(packet))
    iq = gmsk_modulate(tx)
    lvl = _gmsk_demod_bits(iq)
    ref = np.array(packet)
    best = None
    for seed in (0, 1):
        out, last = [], seed
        for L in lvl:
            out.append(0 if L != last else 1)
            last = L
        err = int(np.sum(np.array(out) != ref[:len(out)]))
        if best is None or err < best[0]:
            best = (err, len(out))
    ber = best[0] / best[1]
    dev = peak_deviation_hz(iq)
    good = ber < 0.001
    print(f"[3] GMSK demod     : peak dev {dev:.0f} Hz (target {PEAK_DEV:.0f}), "
          f"bit errors {best[0]}/{best[1]} ({ber * 100:.2f}%) "
          f"{'PASS' if good else 'FAIL'}")
    ok &= good

    # 4. preamble deviation -- what lets a receiver lock
    pre = gmsk_modulate(apply_nrzi([0, 1] * 120))
    d = np.angle(pre[1:] * np.conj(pre[:-1])) * (RB * SPS) / (2.0 * np.pi)
    pd = float(np.max(np.abs(d[8 * SPS:-8 * SPS])))
    good = pd > 2000.0
    print(f"[4] preamble dev   : {pd:.0f} Hz (should be ~2400) "
          f"{'PASS' if good else 'FAIL'}")
    ok &= good

    print(f"=== self-test: {'PASS' if ok else 'FAIL'} ===\n")
    return ok


# ============================================================================= #
# Keysight X-series ARB I/Q file  [DOCUMENTED]
# ============================================================================= #
def build_wfm(iq, fs=None, magic="SGT100A", scale=0.5, header=False,
              big_endian=True, i_first=True, ext="wfm"):
    """Serialise I/Q to the Keysight CXG/EXG/MXG X-series ARB I/Q file format.

    DOCUMENTED in "Creating and Downloading Waveform Files" (Keysight X-Series
    Signal Generators Programming Guide):
      * signed 16-bit 2's complement, one I and one Q value per sample
      * INTERLEAVED, I first then Q
      * BIG-endian byte order  ("little-endian will produce an undesired output")
      * the I/Q file contains ONLY the I/Q data.  The file header (sample rate,
        marker polarity, ...) and the marker file are SEPARATE files; the
        instrument auto-creates them if you do not download them.
    => so NO embedded header.  The old 244-byte header was wrong for X-series and
       is what drove "DAC over range" (-628).
    """
    n = len(iq)
    full = 32767.0 * scale
    i16 = np.clip(np.real(iq) * full, -32767, 32767).astype(np.int16)
    q16 = np.clip(np.imag(iq) * full, -32767, 32767).astype(np.int16)
    peak = max(int(np.max(np.abs(i16))), int(np.max(np.abs(q16))))
    print(f"[i] {ext}: {n} samples, peak {peak} ({peak / 32767.0 * 100:.1f}% FS, "
          f"scale={scale}, endian={'big' if big_endian else 'LITTLE!'}, "
          f"order={'IQ' if i_first else 'QI!'}, header={'yes' if header else 'no'})")
    if peak >= 32767:
        print("[w] i/q reached full scale; lower --scale")

    endian = ">" if big_endian else "<"
    fmt = endian + "hh"
    payload = bytearray()
    if i_first:
        for i, q in zip(i16, q16):
            payload += struct.pack(fmt, int(i), int(q))
    else:
        for q, i in zip(q16, i16):
            payload += struct.pack(fmt, int(q), int(i))

    if not header:
        return bytes(payload)

    # Legacy/incorrect embedded header -- kept only for A/B debugging.  Do NOT
    # use on an X-series instrument.
    header_len = 244
    total_len = header_len + len(payload)
    hdr = bytearray(header_len)
    hdr[0:4] = magic.encode("ascii")[:4].ljust(4, b"\0")
    struct.pack_into("<I", hdr, 4, header_len)
    struct.pack_into("<I", hdr, 12, n)
    if fs:
        struct.pack_into("<I", hdr, 16, int(round(fs)))
    struct.pack_into("<I", hdr, 240, total_len)
    return bytes(hdr) + bytes(payload)


def dump_wfm_header(path, nbytes=256):
    with open(path, "rb") as f:
        data = f.read(nbytes)
    print(f"--- {path}  (first {len(data)} bytes) ---")
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"{off:04d}  {hexs:<48}  {asc}")
    if len(data) >= 20:
        print(f"  [recon] header_len={struct.unpack_from('<I', data, 4)[0]} "
              f"samples={struct.unpack_from('<I', data, 12)[0]} "
              f"sample_rate={struct.unpack_from('<I', data, 16)[0]}")


# ============================================================================= #
# N5182A SCPI over raw socket (port 5025)
# ============================================================================= #
class SocketSG:
    def __init__(self, ip, port=5025, timeout=15.0):
        self.sock = socket.create_connection((ip, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""

    def write(self, cmd):
        if isinstance(cmd, str):
            cmd = cmd.encode("ascii")
        if not cmd.endswith(b"\n"):
            cmd += b"\n"
        self.sock.sendall(cmd)

    def write_raw(self, data):
        self.sock.sendall(data)

    def _readline(self):
        while b"\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return line.strip().decode("ascii", "replace")

    def query(self, cmd):
        self.write(cmd)
        return self._readline()

    def check_errors(self):
        errs = []
        try:
            for _ in range(20):
                r = self.query(":SYST:ERR?")
                errs.append(r)
                if r.startswith("+0") or r.lower().startswith("0,"):
                    break
        except (ConnectionError, OSError) as e:
            errs.append(f"<connection lost while reading error queue: {e}>")
        return errs

    def is_idn_ok(self):
        """Query *IDN?. Returns (text, ok). ok=False if empty / not SCPI-looking."""
        try:
            idn = self.query("*IDN?").strip()
        except (ConnectionError, OSError) as e:
            return (f"<error: {e}>", False)
        ok = bool(idn) and not idn.startswith("<error")
        return (idn, ok)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def scpi_definite_block(payload):
    len_str = str(len(payload)).encode("ascii")
    return b"#" + str(len(len_str)).encode("ascii") + len_str + payload


def remote_wfm_path(remote_dir, name):
    """Build the on-instrument path.  The canonical Keysight example uses a
    volume-style path like 'ARBI:file_name'; some instruments accept '/ARB/name'.
    Configure with --remote-dir.  [KNOWN, LOW on the exact string]"""
    d = remote_dir.rstrip("/\\")
    if d.endswith(":"):
        return f"{d}{name}"
    return f"{d}/{name}"


def upload_waveform(sg, wfm_bytes, remote_path):
    """Download I/Q data.  X-series form:  :MEM:DATA:UNPR "WFM1:<name>",#<A><B><data>
    The I/Q file carries NO header (header/marker are separate files)."""
    head = f':MEM:DATA:UNPR "{remote_path}",'.encode("ascii")
    sg.write_raw(head + scpi_definite_block(wfm_bytes) + b"\n")
    time.sleep(0.3)


def write_waveform_header(sg, remote_path, sample_rate, scaling, peak=1.414, rms=0.5):
    """[:SOURce]:RADio:ARB:HEADer:WRITe "file","desc",<sample_rate>,<scaling>,
    <marker_polarity>,<alc_hold>,<alt_power>,<pulse>,<mod_atten>,<mod_filter>,
    <output_filter>,<peak_power>,<rms>   -- sets the per-waveform sample rate and
    runtime DAC scaling (the header we do NOT embed in the I/Q file)."""
    cmd = (f':RADio:ARB:HEAD:WRIT "{remote_path}","AIS GMSK",'
           f'{int(round(sample_rate))},{scaling},NONE,None,UNSP,None,UNSP,UNSP,'
           f'UNSP,{peak},{rms}')
    sg.write(cmd)


# ============================================================================= #
# Main
# ============================================================================= #
def main():
    ap = argparse.ArgumentParser(description="AIVDM -> AIS GMSK -> N5182A")
    ap.add_argument("--aivdm", default=SAMPLE_SENTENCE,
                    help="AIVDM/AIVDO sentence (default: built-in channel-B sample)")
    ap.add_argument("--aivdm-file", help="read the sentence from a file instead")
    ap.add_argument("--ip", default="192.254.4.100")
    ap.add_argument("--port", type=int, default=5025)
    ap.add_argument("--freq", type=float, default=None,
                    help="override RF freq [Hz]; default auto from channel")
    ap.add_argument("--power", type=float, default=POW_DEFAULT)
    ap.add_argument("--scale", type=float, default=0.5,
                    help="I/Q full-scale factor into the 16-bit file (default 0.5 = "
                         "-6 dB headroom; higher avoids 'DAC over range')")
    ap.add_argument("--ext", default="wfm",
                    help="output file extension (docs show .wiq; default wfm)")
    ap.add_argument("--header", dest="header", action="store_true", default=False,
                    help="WRONG for X-series: prepend a legacy 244-byte header "
                         "(kept only for A/B debugging; default is NO header)")
    ap.add_argument("--little-endian", action="store_true",
                    help="WRONG for X-series: emit little-endian (doc requires "
                         "big-endian; diagnostic only)")
    ap.add_argument("--q-first", action="store_true",
                    help="WRONG for X-series: interleave Q before I (doc requires "
                         "I first; diagnostic only)")
    ap.add_argument("--which", choices=["standard", "raw", "framed", "both"],
                    default="standard",
                    help="standard=frame+NRZI (correct AIS TX; default); "
                         "framed=frame, no NRZI; raw=payload only, no frame/NRZI "
                         "(debug); both=all for comparison")
    ap.add_argument("--nrzi-invert", action="store_true",
                    help="NON-CONFORMANT: invert NRZI polarity (1->transition). "
                         "Leave OFF for standard AIS (sec.2.6: 0->transition).")
    ap.add_argument("--no-byte-reverse", action="store_true",
                    help="NON-CONFORMANT: send the AIVDM payload in logical order "
                         "without the sec.3.3.7 per-byte bit reversal (debug only)")
    ap.add_argument("--selftest", action="store_true",
                    help="offline loopback: frame -> deframe -> CRC -> byte order "
                         "-> modulate/demodulate, then exit before any upload")
    ap.add_argument("--ramp", choices=["on", "off"], default="on",
                    help="apply the 8-bit amplitude ramp (standard packet). "
                         "off = no ramp, for A/B comparison")
    ap.add_argument("--ramp-shape", choices=["cos", "linear"], default="cos",
                    help="ramp envelope shape; the standard figure was not "
                         "supplied so this is a guess")
    ap.add_argument("--buffer-fill", type=int, choices=[0, 1], default=BUFFER_FILL_DEFAULT,
                    help="value of the 24-bit trailing buffer bits (default 1)")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--probe", action="store_true",
                    help="connect, query *IDN?, report, and exit without uploading")
    ap.add_argument("--remote-dir", default="WFM1:",
                    help="on-instrument ARB volume (X-series uses 'WFM1:')")
    ap.add_argument("--rscaling", type=int, default=70,
                    help="ARB runtime DAC scaling %% written to the waveform header "
                         "(factory preset 70). Lower it if you still get 'DAC over "
                         "range'; raise it for more dynamic range")
    ap.add_argument("--scpi", action="store_true",
                    help="connect and drop into a raw SCPI prompt (type commands; "
                         "lines starting with '?' are sent as queries)")
    ap.add_argument("--rf-on", action="store_true",
                    help="enable ARB modulation and RF output (dangerous)")
    ap.add_argument("--dump-file", metavar="PATH", help="dump a .wfm header and exit")
    ap.add_argument("--outdir", default=".")
    args = ap.parse_args()

    if args.dump_file:
        dump_wfm_header(args.dump_file)
        return 0

    # ---- interactive SCPI passthrough ---- #
    if args.scpi:
        try:
            sg = SocketSG(args.ip, args.port)
        except OSError as e:
            print(f"[!!] cannot connect to {args.ip}:{args.port}: {e}")
            return 2
        print(f"[i] connected to {args.ip}:{args.port}. Type SCPI commands.")
        print("    Type a line starting with '?' to force a query, or end a line "
              "with '?' to query. Ctrl-D / 'exit' to quit.")
        print("    Handy probes:")
        print("      *IDN?")
        print("      :SYST:ERR?")
        print("      :MMEM:CAT:ARB?            (list ARB waveforms/dir)")
        print("      :MMEM:CDIR?               (current mass-memory dir)")
        print("      :SOUR:RAD:ARB:CAT?        (ARB catalog)")
        print("      :SOUR:RAD:ARB:SCAL?       (ARB DAC scaling, %)")
        print("      :SOUR:RAD:ARB:HEAD?       (ARB headroom, dB; <0 = over range)")
        try:
            while True:
                try:
                    line = input("scpi> ").strip()
                except EOFError:
                    break
                if not line or line.lower() in ("exit", "quit"):
                    break
                try:
                    if line.startswith("?") or line.endswith("?") or "?" in line:
                        print(sg.query(line.lstrip("?")))
                    else:
                        sg.write(line)
                        print("    (sent)")
                except (ConnectionError, OSError) as e:
                    print(f"    [!!] {e}")
        finally:
            sg.close()
        return 0

    # ---- get the sentence and decode it ---- #
    sentence = args.aivdm
    if args.aivdm_file:
        with open(args.aivdm_file, "r", encoding="ascii", errors="replace") as f:
            sentence = f.read().strip().splitlines()[0]
    info = parse_aivdm(sentence)
    print(f"[i] channel   : {info['channel']}")
    print(f"[i] payload   : {info['payload']} ({len(info['payload'])} chars)")
    print(f"[i] fill bits : {info['fill']}")
    print(f"[i] checksum  : given={info['checksum_given']} "
          f"calc={info['checksum_calc']} ok={info['checksum_ok']}")

    payload_bits = info["bits"]
    print(f"[i] source bits: {len(payload_bits)}  msg_type={msg_type(payload_bits)}")

    # ---- logical -> VDL wire order (per-byte bit reversal, sec.3.3.7) ---- #
    if args.no_byte_reverse:
        wire_bits = list(payload_bits)
        print("[w] --no-byte-reverse: NON-CONFORMANT, sending logical order "
              "(a receiver will decode scrambled fields)")
    else:
        wire_bits = reverse_each_byte(payload_bits)
        print(f"[i] wire bits  : {len(wire_bits)} "
              f"(per-byte bit reversal, sec.3.3.7)")

    # ---- frequency from channel ---- #
    if args.freq is not None:
        freq = args.freq
    elif info["channel"].upper().startswith("B"):
        freq = CH_B_FREQ
    elif info["channel"].upper().startswith("A"):
        freq = CH_A_FREQ
    else:
        freq = CH_A_FREQ
        print(f"[w] unknown channel {info['channel']!r}; defaulting to AIS1")
    print(f"[i] RF freq   : {freq/1e6:.3f} MHz "
          f"(channel {info['channel'].upper()})")

    # ---- build waveform bit streams (each entry: (bits, down_ramp_start_bit)) ---- #
    frames = {}
    if args.which in ("standard", "framed", "both"):
        pkt, bl = build_ais_packet(wire_bits, buffer_fill=args.buffer_fill)
        frames["framed"] = (pkt, len(pkt) - bl)
    if args.which in ("standard", "both"):
        pkt, bl = build_ais_packet(wire_bits, buffer_fill=args.buffer_fill)
        frames["standard"] = (apply_nrzi(pkt, invert=args.nrzi_invert),
                              len(pkt) - bl)
    if args.which in ("raw", "both"):
        frames["raw"] = (list(wire_bits), None)
    # playback order: standard first
    ordered = {k: frames[k] for k in ("standard", "framed", "raw") if k in frames}

    wfms = {}
    for name, (b, down_start) in ordered.items():
        iq = gmsk_modulate(b)
        dev = peak_deviation_hz(iq)          # BEFORE ramping (near-zero amp at ends)
        if args.ramp == "on":
            iq = apply_ramps(iq, up_bits=RAMP_BITS_DEFAULT,
                             down_start_bit=down_start, down_bits=DOWN_RAMP_BITS,
                             shape=args.ramp_shape)
        print(f"[i] {name:6s}: {len(b)} bits, {len(iq)} samples, "
              f"{len(iq)/FS*1e3:.3f} ms, peak dev ~{dev:.0f} Hz "
              f"(target {PEAK_DEV:.0f})")
        wfm = build_wfm(iq, FS, scale=args.scale, header=args.header, ext=args.ext,
                        big_endian=not args.little_endian, i_first=not args.q_first)
        path = f"{args.outdir}/{name}_ais.{args.ext}"
        with open(path, "wb") as f:
            f.write(wfm)
        print(f"    wrote {path} ({len(wfm)} bytes)")
        wfms[name] = (path, len(wfm))

    print("\n[!] I/Q file format: big-endian, I-then-Q, NO header (per the "
          "Keysight X-series guide).\n")

    if args.selftest:
        pkt, bl = build_ais_packet(wire_bits, buffer_fill=args.buffer_fill)
        if not run_selftest(info, payload_bits, wire_bits, pkt, bl):
            print("[!!] self-test FAILED; aborting before upload.")
            return 1

    if not args.upload:
        print("[i] no --upload: nothing sent. RF untouched.")
        return 0

    try:
        sg = SocketSG(args.ip, args.port)
    except OSError as e:
        print(f"[!!] cannot connect to {args.ip}:{args.port}: {e}")
        print("     check the IP/port and that the instrument is on the network.")
        return 2
    try:
        print(f"[i] connected to {args.ip}:{args.port}")
        idn, idn_ok = sg.is_idn_ok()
        print(f"    *IDN? -> {idn!r}")
        if not idn_ok:
            print("[!!] *IDN? did not return a string. This endpoint is probably "
                  "NOT the signal generator (or not a SCPI responder).")
            print("     Nothing was uploaded. Aborting before touching ARB/RF.")
            return 3
        if args.probe:
            print("[i] --probe: connection verified, nothing uploaded.")
            return 0
        sg.write("*CLS")
        selected_path = None
        for name, (path, _) in wfms.items():
            remote = f"{name}_ais"                    # WFM1: takes a plain name
            remote_path = remote_wfm_path(args.remote_dir, remote)
            with open(path, "rb") as f:
                blob = f.read()
            print(f"[i] downloading {name}: {len(blob)} bytes -> {remote_path}")
            try:
                upload_waveform(sg, blob, remote_path)
            except (ConnectionError, OSError) as e:
                print(f"    [!!] connection dropped during upload: {e}")
                return 4
            errs = sg.check_errors()
            if not errs or not errs[0].startswith("+0"):
                print("    [!!] instrument errors after I/Q download:", errs)
            # set per-waveform header: sample rate + runtime scaling
            try:
                write_waveform_header(sg, remote_path, FS, args.rscaling,
                                      peak=1.414, rms=round(args.scale, 3))
                errs = sg.check_errors()
                if not errs or not errs[0].startswith("+0"):
                    print("    [!!] errors writing waveform header:", errs)
            except (ConnectionError, OSError) as e:
                print(f"    [!!] connection dropped writing header: {e}")
                return 4
            if selected_path is None:
                selected_path = remote_path

        sg.write(":SOURce:RADio:ARB:STATe OFF")
        sg.write(f':SOURce:RADio:ARB:WAVeform "{selected_path}"')
        sg.write(f":SOURce:FREQuency {freq:.0f}")
        sg.write(f":SOURce:POWer {args.power:.2f}")
        print(f"[i] configured: {freq/1e6:.3f} MHz, {args.power} dBm, "
              f"wav={selected_path} (sample rate {int(FS)} set via header)")

        if args.rf_on:
            sg.write(":SOURce:RADio:ARB:STATe ON")
            sg.write(":OUTPut:STATe ON")
            print("[!] ARB ON, RF OUTPUT ON. Conducted into load, per your setup.")
        else:
            print("[i] --rf-on not given: ARB/RF left OFF.")
        print("[i] instrument errors:", sg.check_errors())
    finally:
        sg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
