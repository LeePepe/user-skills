#!/usr/bin/env python3
"""Layer 0 snapshot checks and R05 Jev visual defect classification."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import urllib.request
import uuid
import os
import urllib.request
import zlib
from pathlib import Path
from typing import Any

STATES = ("populated", "empty", "loading", "error")
APPEARANCES = ("light", "dark")
BANDS = {"top_band": (0.0, 0.42), "mid_band": (0.29, 0.71), "bot_band": (0.58, 1.0)}
DEFECTS = (
    "ellipsis",
    "control_overlap",
    "double_chrome",
    "raw_key_or_dup_unit",
    "unequal_bento",
    "edge_clipping",
)


def call_systemone(payload: dict[str, Any], endpoint: str | None = None) -> dict[str, Any]:
    url = endpoint or os.environ.get("FALCON_BASE_URL", "http://127.0.0.1:19823")
    if not url.rstrip("/").endswith("/v1/systemone"):
        url = url.rstrip("/") + "/v1/systemone"
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "TypeSafe-Danger-Zone-Images": "true"}
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def png_dimensions(data: bytes) -> tuple[int, int]:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def _png_pixels(data: bytes) -> tuple[int, int, bytes, int]:
    width, height = png_dimensions(data)
    pos = 8; raw = b""; color_type = 6; bit_depth = 8
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]; kind = data[pos + 4:pos + 8]; chunk = data[pos + 8:pos + 8 + length]; pos += 12 + length
        if kind == b"IHDR":
            _, _, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            raw += chunk
        elif kind == b"IEND":
            break
    decoded = zlib.decompress(raw)
    channels = {2: 3, 6: 4}.get(color_type, 4)
    stride = width * channels
    rows = []; offset = 0; prior = bytearray(stride)
    for _ in range(height):
        filter_type = decoded[offset]; offset += 1
        row = bytearray(decoded[offset:offset + stride]); offset += stride
        for i in range(stride):
            left = row[i - channels] if i >= channels else 0
            up = prior[i]
            upper_left = prior[i - channels] if i >= channels else 0
            if filter_type == 1: row[i] = (row[i] + left) & 255
            elif filter_type == 2: row[i] = (row[i] + up) & 255
            elif filter_type == 3: row[i] = (row[i] + ((left + up) // 2)) & 255
            elif filter_type == 4:
                p = left + up - upper_left; pa = abs(p-left); pb = abs(p-up); pc = abs(p-upper_left)
                row[i] = (row[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else upper_left)) & 255
        rows.append(row); prior = row
    return width, height, b"".join(rows), channels


def _snapshot_bytes(snapshot: Any) -> bytes:
    if isinstance(snapshot, (str, Path)):
        return Path(snapshot).read_bytes()
    if isinstance(snapshot, dict) and "path" in snapshot:
        return Path(snapshot["path"]).read_bytes()
    if isinstance(snapshot, dict) and "data" in snapshot:
        return base64.b64decode(snapshot["data"])
    return bytes(snapshot)


def _luminance(data: bytes) -> float:
    _, _, pixels, channels = _png_pixels(data)
    sample = pixels[0:channels * min(64, len(pixels) // channels):channels]
    if not sample:
        return 0.0
    values = []
    for index in range(0, len(sample), 3):
        values.append(sum(sample[index:index + 3]) / (255 * 3))
    return sum(values) / len(values)


def audit_snapshots_layer0(snapshots: dict[str, Any], expected_width_pt: float, scale: float) -> dict[str, Any]:
    errors = []; dimensions = {}
    for state in STATES:
        for appearance in APPEARANCES:
            key = f"{state}-{appearance}"
            if key not in snapshots:
                errors.append(f"MISSING:{key}"); continue
            try:
                data = _snapshot_bytes(snapshots[key]); width, height = png_dimensions(data)
                dimensions[key] = [width, height]
                if not data or height <= 0: errors.append(f"EMPTY:{key}")
                if width != round(expected_width_pt * scale): errors.append(f"WIDTH:{key}:{width}")
                lum = _luminance(data)
                if appearance == "light" and lum <= 0.70: errors.append(f"LIGHT_POLARITY:{key}")
                if appearance == "dark" and lum >= 0.20: errors.append(f"DARK_POLARITY:{key}")
            except (OSError, ValueError, zlib.error, struct.error) as exc:
                errors.append(f"INVALID:{key}:{exc}")
    return {"ok": not errors, "errors": errors, "dimensions": dimensions}


def map_tile_box_to_image(box: dict[str, float], y_min: float, y_max: float, width_px: int, height_px: int) -> list[int]:
    return [round(box["left"] * width_px), round((y_min + box["top"] * (y_max - y_min)) * height_px),
            round(box["right"] * width_px), round((y_min + box["bottom"] * (y_max - y_min)) * height_px)]


def map_zoom_box_to_image(zoom_box: dict[str, float], crop_norm_box: dict[str, float] | list[float], width_px: int, height_px: int) -> list[int]:
    if isinstance(crop_norm_box, dict):
        left, top, right, bottom = (crop_norm_box[k] for k in ("left", "top", "right", "bottom"))
    else:
        left, top, right, bottom = crop_norm_box
    return [round((left + zoom_box["left"] * (right - left)) * width_px),
            round((top + zoom_box["top"] * (bottom - top)) * height_px),
            round((left + zoom_box["right"] * (right - left)) * width_px),
            round((top + zoom_box["bottom"] * (bottom - top)) * height_px)]


def build_r05_request(screen_id: str, state: str, appearance: str, pngs: dict[str, bytes], model: str = "jev-1.14.0") -> dict[str, Any]:
    assets = {}
    for name, data in pngs.items():
        ref = f"img-{name}-{hashlib.sha256(data).hexdigest()[:12]}-{uuid.uuid4().hex[:8]}"
        assets[ref] = {"type": "image", "format": "png", "data_base64": base64.b64encode(data).decode()}
    state_refs = {"screen_id": screen_id, "ui_state": state, "appearance": appearance,
                  "assets": list(assets)}
    for band in ("full_view", "top_band", "mid_band", "bot_band"):
        if band in pngs:
            state_refs[band] = next(ref for ref in assets if ref.startswith(f"img-{band}-"))
    return {"model": model, "state": state_refs, "assets": assets,
            "questions": {f"{defect}_present": {"type": "noul", "instructions": f"Is {defect.replace('_', ' ')} present?",
                                                  "criteria": {"true": "The defect is visible.", "false": "The defect is not visible."}}
                         for defect in DEFECTS},
            "headers": {"TypeSafe-Danger-Zone-Images": "true"},
            "http_headers": {"TypeSafe-Danger-Zone-Images": "true"}}


def evaluate_r05_truth_table(noul: float, confidence: float = 0.0, zoom_noul: float | None = None,
                            zoom_confidence: float = 0.0, box: list[int] | None = None,
                            image_size: tuple[int, int] | None = None, choice: str | None = None) -> dict[str, Any]:
    if noul < 0.30:
        return {"verdict": "PASS_CLEAN", "fail": False}
    if zoom_noul is None and noul >= 0.75 and confidence >= 0.65 and choice != "several":
        if box and image_size and (box[2] - box[0]) >= 8 and (box[3] - box[1]) >= 8:
            area = (box[2] - box[0]) * (box[3] - box[1]); total = image_size[0] * image_size[1]
            if area < 0.85 * total:
                return {"verdict": "FAIL_CONFIRMED_DEFECT", "fail": True}
    if zoom_noul is not None and zoom_noul < 0.30:
        return {"verdict": "PASS_CLEARED_ON_ZOOM", "fail": False}
    if zoom_noul is not None and zoom_noul >= 0.70 and zoom_confidence >= 0.55 and (noul >= 0.70 or zoom_noul >= 0.75):
        if box and image_size:
            area = (box[2] - box[0]) * (box[3] - box[1]); total = image_size[0] * image_size[1]
            if area >= 0.85 * total:
                return {"verdict": "WARN_ESCALATE_REVIEWER", "fail": False}
        return {"verdict": "FAIL_CONFIRMED_ON_ZOOM", "fail": True}
    return {"verdict": "WARN_ESCALATE_REVIEWER", "fail": False}


def detect_horizontal_edge_clipping(
    snapshot: Any,
    *,
    y_band: tuple[float, float] = (0.14, 0.86),
    edge_px: int = 2,
    contrast_delta: int = 28,
    min_clipped_ratio: float = 0.06,
) -> dict[str, Any]:
    """Detect whether mid-screen cards/strokes are abruptly clipped at the left/right viewport edges."""
    data = _snapshot_bytes(snapshot)
    width, height, pixels, channels = _png_pixels(data)
    stride = width * channels
    y_start = max(0, min(height, int(height * y_band[0])))
    y_end = max(y_start + 1, min(height, int(height * y_band[1])))
    band_rows = max(1, y_end - y_start)
    edge_span = max(1, min(edge_px, width // 4 or 1))

    def _rgb_at(row_idx: int, col_idx: int) -> tuple[int, int, int]:
        base = row_idx * stride + col_idx * channels
        return (pixels[base], pixels[base + 1], pixels[base + 2])

    def _dist(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
        return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))

    # Estimate baseline canvas background from top/bottom margin corners inside the band
    bg_samples = [
        _rgb_at(y_start, 0),
        _rgb_at(y_start, width - 1),
        _rgb_at(y_end - 1, 0),
        _rgb_at(y_end - 1, width - 1),
    ]
    bg_rgb = (
        sorted(s[0] for s in bg_samples)[len(bg_samples) // 2],
        sorted(s[1] for s in bg_samples)[len(bg_samples) // 2],
        sorted(s[2] for s in bg_samples)[len(bg_samples) // 2],
    )

    left_clipped_rows = 0
    right_clipped_rows = 0
    for y in range(y_start, y_end):
        left_rgb = _rgb_at(y, 0)
        right_rgb = _rgb_at(y, width - 1)
        left_non_bg = any(_dist(_rgb_at(y, x), bg_rgb) >= contrast_delta for x in range(edge_span))
        right_non_bg = any(_dist(_rgb_at(y, width - 1 - x), bg_rgb) >= contrast_delta for x in range(edge_span))
        # A full-bleed uniform background has left_rgb == right_rgb == row background;
        # edge clipping occurs when non-background card/text/border strokes touch x=0..1 or width-1.
        if left_non_bg:
            left_clipped_rows += 1
        if right_non_bg:
            right_clipped_rows += 1

    left_ratio = round(left_clipped_rows / band_rows, 4)
    right_ratio = round(right_clipped_rows / band_rows, 4)
    clipped = left_ratio >= min_clipped_ratio or right_ratio >= min_clipped_ratio
    edges = []
    if left_ratio >= min_clipped_ratio:
        edges.append("left")
    if right_ratio >= min_clipped_ratio:
        edges.append("right")
    return {
        "ok": not clipped,
        "defect": "edge_clipping" if clipped else None,
        "clipped": clipped,
        "clipped_edges": edges,
        "left_clipped_ratio": left_ratio,
        "right_clipped_ratio": right_ratio,
        "dimensions": [width, height],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["off", "observe", "auto"], default="off")
    parser.add_argument("--input", type=argparse.FileType("r"), default="-")
    parser.add_argument(
        "--check-edge-clipping",
        metavar="PNG_PATH",
        help="Check a simulator screenshot PNG for horizontal left/right edge clipping (DC-003).",
    )
    args = parser.parse_args(argv)
    if args.check_edge_clipping:
        report = detect_horizontal_edge_clipping(args.check_edge_clipping)
        if args.mode == "observe" and report["clipped"]:
            report["decision"] = "observe (would fail)"
            print(json.dumps(report))
            return 0
        print(json.dumps(report))
        return 1 if report["clipped"] else 0

    data = json.load(args.input)
    result = evaluate_r05_truth_table(float(data.get("noul", 0)), float(data.get("confidence", 0)),
                                      data.get("zoom_noul"), float(data.get("zoom_confidence", 0)), data.get("box"), data.get("image_size"))
    if args.mode == "observe" and result["fail"]:
        result["decision"] = "observe (would fail)"; result["fail"] = False
    print(json.dumps(result))
    return 1 if args.mode == "auto" and result["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
