#!/usr/bin/env python3
"""
Bleach Online TApplication SWF Tool

Protection observed in the supplied TApplication sample:
    swap byte offsets 1 <-> 10
    swap byte offsets 20 <-> 200

Commands:
    python [1]_Bleach_CoreSwf.py decrypt TApplication.swf -o TApplication_decrypted.swf
    python [1]_Bleach_CoreSwf.py encrypt TApplication_decrypted.swf -o TApplication_protected.swf
    python [1]_Bleach_CoreSwf.py info TApplication.swf

decrypt:
    Restores the byte swaps. By default outputs a valid CWS.
    Use --fws to additionally decompress CWS -> FWS.

encrypt:
    Accepts FWS or CWS.
    FWS is converted to CWS first, then the two swaps are applied.
"""

import argparse
import hashlib
import struct
import sys
import zlib
from pathlib import Path

SWAPS = ((1, 10), (20, 200))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def swaps(data):
    b = bytearray(data)
    for a, c in SWAPS:
        if max(a, c) >= len(b):
            raise ValueError(
                f"File too small for protection swap {a}<->{c}: {len(b)} bytes"
            )
        b[a], b[c] = b[c], b[a]
    return bytes(b)


def declared_length(data):
    if len(data) < 8:
        raise ValueError("File is smaller than a SWF header.")
    return struct.unpack("<I", data[4:8])[0]


def swf_info(data):
    sig = data[:3]
    return {
        "signature": sig.decode("latin1", errors="replace"),
        "version": data[3] if len(data) >= 4 else None,
        "declared_length": declared_length(data) if len(data) >= 8 else None,
        "file_size": len(data),
        "sha256": sha256(data),
    }


def cws_to_fws(data):
    if data[:3] != b"CWS":
        raise ValueError("Input is not a CWS file.")

    expected = declared_length(data)
    body = zlib.decompress(data[8:])
    out = b"FWS" + data[3:8] + body

    if len(out) != expected:
        raise ValueError(
            f"SWF length validation failed: header={expected}, decoded={len(out)}"
        )
    return out


def fws_to_cws(data, level=9):
    if data[:3] != b"FWS":
        raise ValueError("Input is not an FWS file.")

    expected = declared_length(data)
    if len(data) != expected:
        raise ValueError(
            f"FWS length validation failed: header={expected}, actual={len(data)}"
        )

    # CWS keeps the first 8 bytes uncompressed, changing only the signature.
    return b"CWS" + data[3:8] + zlib.compress(data[8:], level)


def restore_protected(data):
    restored = swaps(data)

    if restored[:3] != b"CWS":
        raise ValueError(
            "The supplied file does not match the observed Bleach Online "
            "TApplication protection: restoring the known swaps did not produce CWS."
        )

    # Validate compressed stream without changing the requested output.
    fws = cws_to_fws(restored)
    return restored, fws


def cmd_decrypt(args):
    src = args.input.read_bytes()
    cws, fws = restore_protected(src)

    outdata = fws if args.fws else cws
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(outdata)

    print("[OK] Protection removed")
    print(f"Input       : {args.input}")
    print(f"Output      : {args.output}")
    print(f"Output type : {'FWS (uncompressed)' if args.fws else 'CWS (compressed)'}")
    print(f"SWF version : {fws[3]}")
    print(f"SWF length  : {len(fws):,} bytes")
    print(f"SHA-256 FWS : {sha256(fws)}")


def cmd_encrypt(args):
    src = args.input.read_bytes()

    if src[:3] == b"FWS":
        cws = fws_to_cws(src, args.level)
    elif src[:3] == b"CWS":
        # Validate before protecting.
        cws_to_fws(src)
        cws = src
    else:
        raise ValueError("Encrypt input must be a valid FWS or CWS.")

    protected = swaps(cws)

    # Self-check: inverse operation must restore the exact CWS.
    restored = swaps(protected)
    if restored != cws:
        raise ValueError("Internal protection round-trip failed.")

    # Also verify the protected result restores to a valid SWF.
    _, check_fws = restore_protected(protected)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(protected)

    print("[OK] Protection applied")
    print(f"Input       : {args.input}")
    print(f"Output      : {args.output}")
    print(f"SWF version : {check_fws[3]}")
    print(f"SWF length  : {len(check_fws):,} bytes")
    print(f"SHA-256 FWS : {sha256(check_fws)}")


def cmd_info(args):
    data = args.input.read_bytes()
    print(f"File        : {args.input}")
    print(f"Size        : {len(data):,} bytes")
    print(f"First bytes : {data[:16].hex(' ').upper()}")
    print(f"SHA-256     : {sha256(data)}")

    if data[:3] in (b"FWS", b"CWS"):
        i = swf_info(data)
        print("State       : normal SWF")
        print(f"Signature   : {i['signature']}")
        print(f"Version     : {i['version']}")
        print(f"SWF length  : {i['declared_length']:,}")
        if data[:3] == b"CWS":
            fws = cws_to_fws(data)
            print("ZLIB        : valid")
            print(f"SHA-256 FWS : {sha256(fws)}")
        return

    try:
        cws, fws = restore_protected(data)
        print("State       : protected TApplication")
        print("Protection  : swap(1,10), swap(20,200)")
        print("Restores to : CWS")
        print(f"Version     : {fws[3]}")
        print(f"SWF length  : {len(fws):,}")
        print(f"SHA-256 FWS : {sha256(fws)}")
    except Exception as exc:
        print("State       : unknown / unsupported")
        print(f"Reason      : {exc}")


def main():
    p = argparse.ArgumentParser(
        description="Decrypt/reapply the observed Bleach Online TApplication SWF protection."
    )
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("decrypt", help="Remove TApplication protection.")
    d.add_argument("input", type=Path)
    d.add_argument("-o", "--output", type=Path, required=True)
    d.add_argument(
        "--fws",
        action="store_true",
        help="Output fully uncompressed FWS instead of restored CWS.",
    )
    d.set_defaults(func=cmd_decrypt)

    e = sub.add_parser("encrypt", help="Apply TApplication protection.")
    e.add_argument("input", type=Path)
    e.add_argument("-o", "--output", type=Path, required=True)
    e.add_argument(
        "--level",
        type=int,
        choices=range(10),
        default=9,
        help="ZLIB level when input is FWS (default: 9).",
    )
    e.set_defaults(func=cmd_encrypt)

    i = sub.add_parser("info", help="Inspect/detect a TApplication SWF.")
    i.add_argument("input", type=Path)
    i.set_defaults(func=cmd_info)

    args = p.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
