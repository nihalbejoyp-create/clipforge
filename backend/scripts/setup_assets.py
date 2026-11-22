#!/usr/bin/env python3
"""Download and normalise subtitle fonts, and pre-fetch the local speech model.

Runs once during install. Safe to re-run: existing files are skipped.
"""
from __future__ import annotations

import io
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.config import FONTS_DIR  # noqa: E402

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

FAMILIES = {
    "Inter": [400, 700, 800],
    "Roboto": [400, 700],
    "Montserrat": [400, 700, 800],
    "Poppins": [400, 700, 800],
    "Bebas Neue": [400],
    "Anton": [400],
    "Oswald": [400, 700],
    "League Spartan": [400, 700],
    "Open Sans": [400, 700],
    "Lato": [400, 700],
    "Archivo Black": [400],
    "Barlow Condensed": [400, 700],
    "DM Sans": [400, 700],
    "Playfair Display": [400, 700],
    "Impact": [400],
    "Arial": [400, 700],
}

SYSTEM_FONTS = {
    "Arial": {
        400: "/System/Library/Fonts/Supplemental/Arial.ttf",
        700: "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    },
    "Impact": {400: "/System/Library/Fonts/Supplemental/Impact.ttf"},
}

WEIGHT_NAMES = {300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold"}


def fetch(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def css_urls(family: str, weights: list[int]) -> dict[int, str]:
    fam = urllib.parse.quote(family)
    spec = f"{fam}:wght@{';'.join(str(w) for w in weights)}" if len(weights) > 1 else f"{fam}"
    url = f"https://fonts.googleapis.com/css2?family={spec}&display=swap"
    try:
        css = fetch(url).decode("utf-8", "ignore")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! CSS lookup failed for {family}: {exc}")
        return {}
    blocks = re.findall(r"@font-face\s*\{[^}]+\}", css)
    out: dict[int, str] = {}
    for block in blocks:
        weight = re.search(r"font-weight:\s*(\d+)", block)
        src = re.search(r"url\((https://[^)]+)\)", block)
        if not weight or not src:
            continue
        w = int(weight.group(1))
        if "woff2" not in src.group(1) and w in out:
            continue
        out.setdefault(w, src.group(1))
    return out


def rename_font(data: bytes, family: str, weight: int, out_path: Path) -> bool:
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        out_path.write_bytes(data)
        return True
    try:
        font = TTFont(io.BytesIO(data))
        if "CFF " in font:
            pass
        name_table = font["name"]
        sub = WEIGHT_NAMES.get(weight, "Regular")
        full = family if sub == "Regular" else f"{family} {sub}"
        ps = full.replace(" ", "")
        records = {
            1: family,
            2: sub,
            3: f"{ps};{weight}",
            4: full,
            6: ps,
            16: family,
            17: sub,
        }
        for rec in list(name_table.names):
            if rec.nameID in records and rec.platformID == 3:
                try:
                    name_table.setName(records[rec.nameID], rec.nameID, rec.platformID,
                                       rec.platEncID, rec.langID)
                except Exception:
                    pass
        if "OS/2" in font:
            font["OS/2"].usWeightClass = weight
            if weight >= 700:
                font["OS/2"].fsSelection = (font["OS/2"].fsSelection & ~0x40) | 0x20
            else:
                font["OS/2"].fsSelection = (font["OS/2"].fsSelection & ~0x20) | 0x40
        if "head" in font:
            font["head"].macStyle = 0x01 if weight >= 700 else 0
        buf = io.BytesIO()
        font.flavor = None
        font.save(buf)
        out_path.write_bytes(buf.getvalue())
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not normalise {family} {weight}: {exc}")
        try:
            out_path.write_bytes(data)
            return True
        except OSError:
            return False


def install_fonts() -> None:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    ok, failed = 0, []
    for family, weights in FAMILIES.items():
        print(f"- {family}")
        if family in SYSTEM_FONTS:
            for weight, path in SYSTEM_FONTS[family].items():
                target = FONTS_DIR / (f"{family.replace(' ', '')}.ttf" if weight == 400
                                      else f"{family.replace(' ', '')}-Bold.ttf")
                if target.exists():
                    ok += 1
                    continue
                src = Path(path)
                if not src.exists():
                    failed.append(f"{family} {weight}")
                    continue
                if rename_font(src.read_bytes(), family, weight, target):
                    ok += 1
                else:
                    failed.append(f"{family} {weight}")
            continue
        urls = css_urls(family, weights)
        if not urls:
            failed.append(family)
            continue
        for weight in weights:
            target = FONTS_DIR / (f"{family.replace(' ', '')}.ttf" if weight == 400
                                  else f"{family.replace(' ', '')}-{WEIGHT_NAMES.get(weight, weight)}.ttf")
            if target.exists():
                ok += 1
                continue
            url = urls.get(weight) or next(iter(urls.values()))
            try:
                data = fetch(url)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! download failed: {exc}")
                failed.append(f"{family} {weight}")
                continue
            if data[:4] == b"wOF2":
                if rename_font(data, family, weight, target):
                    ok += 1
                else:
                    failed.append(f"{family} {weight}")
            else:
                target.write_bytes(data)
                ok += 1
    print(f"\nFonts installed: {ok}" + (f", failed: {', '.join(failed)}" if failed else ""))


def install_cascade() -> None:
    target = ROOT / "assets" / "models" / "haarcascade_frontalface_default.xml"
    if target.exists() and target.stat().st_size > 100_000:
        print("- face detector: present")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    url = ("https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/"
           "haarcascade_frontalface_default.xml")
    print("- face detector: downloading")
    try:
        target.write_bytes(fetch(url))
        print("  ready")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! face detector download failed: {exc} (auto facecam detection disabled)")


def install_model() -> None:
    from app import transcribe

    model = "base"
    print(f"- speech model: faster-whisper '{model}' (one-time download)")
    try:
        transcribe.ensure_model(model)
        print("  model ready")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! model download failed: {exc}")
        print("  The app still runs; transcription will retry on first use.")


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    if only in {"all", "fonts"}:
        print("Installing subtitle fonts…")
        install_fonts()
    if only in {"all", "fonts"}:
        print("\nFace detector…")
        install_cascade()
    if only in {"all", "model"}:
        print("\nPreparing speech recognition…")
        install_model()
    meta = {"fonts": sorted(p.name for p in FONTS_DIR.glob("*.ttf"))}
    (ROOT / "assets" / "fonts.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
