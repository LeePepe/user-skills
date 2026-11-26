"""Fail-closed visual screenshot auditing for the my-designer gate."""
from __future__ import annotations

import base64
import binascii
import json
import os
import struct
import sys
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Iterable

from design_evidence import _decode_png


VISION_AUDIT_SCHEMA = "my-designer/vision-audit-v1"
_ALLOWED_PLATFORMS = {"desktop", "mobile", "web"}
_ALLOWED_PROVIDERS = {"auto", "openai", "magpie"}
_ALLOWED_SEVERITIES = {"P0", "P1", "P2"}
_ALLOWED_CATEGORIES = {"alignment", "overlap", "clipping", "proportion", "ghosting"}
_MAGPIE_ENDPOINT = "http://127.0.0.1:3425/v1/chat/completions"


def _error_result(image: str, *errors: str) -> dict[str, Any]:
    return {"image": image, "valid": False, "pass": False, "defects": [], "errors": list(errors)}


def _non_empty(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return value is not None


def validate_vision_response(payload: Any) -> tuple[bool, list[str], dict[str, Any] | None]:
    """Validate one provider response without allowing malformed data to pass."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return False, ["response must be a JSON object"], None
    if set(payload) != {"pass", "defects"}:
        errors.append("response must contain exactly pass and defects")
    if type(payload.get("pass")) is not bool:
        errors.append("response.pass must be a strict boolean")
    if type(payload.get("defects")) is not list:
        errors.append("response.defects must be a strict list")
    if errors:
        return False, errors, None

    defects: list[dict[str, Any]] = []
    for index, defect in enumerate(payload["defects"], 1):
        if not isinstance(defect, dict):
            errors.append(f"defects[{index}] must be an object")
            continue
        severity = defect.get("severity")
        category = defect.get("category")
        description = defect.get("description")
        evidence = defect.get("evidence")
        if severity not in _ALLOWED_SEVERITIES:
            errors.append(f"defects[{index}].severity must be one of P0, P1, P2")
        if category not in _ALLOWED_CATEGORIES:
            errors.append(
                f"defects[{index}].category must be one of alignment, overlap, clipping, proportion, ghosting"
            )
        if not isinstance(description, str) or not description.strip():
            errors.append(f"defects[{index}].description must be non-empty")
        if not _non_empty(evidence):
            errors.append(f"defects[{index}].evidence must be present and non-empty")
        defects.append(defect)

    if errors:
        return False, errors, None
    normalized = {"pass": payload["pass"], "defects": defects}
    return True, [], normalized


def discover_pngs(directories: Iterable[str] = (), images: Iterable[str] = ()) -> tuple[list[Path], list[str]]:
    """Return deterministic PNG inputs and fail-closed input errors."""
    found: list[Path] = []
    errors: list[str] = []
    seen: set[Path] = set()
    for raw in directories:
        path = Path(raw).expanduser()
        if not path.is_dir():
            errors.append(f"directory not found: {path}")
            continue
        for candidate in sorted(path.rglob("*")):
            if candidate.is_file() and candidate.suffix.lower() == ".png":
                identity = candidate.absolute()
                if identity not in seen:
                    seen.add(identity)
                    found.append(candidate)
    for raw in images:
        path = Path(raw).expanduser()
        if path.suffix.lower() != ".png":
            errors.append(f"image is not a PNG: {path}")
        elif not path.is_file():
            errors.append(f"image not found: {path}")
        else:
            identity = path.absolute()
            if identity not in seen:
                seen.add(identity)
                found.append(path)
    return sorted(found, key=lambda item: str(item)), errors


def _is_png(path: Path, platform: str) -> tuple[bool, str | None]:
    """Validate the PNG stream, dimensions, and evidence boundary before sending it."""
    try:
        trusted_system_aliases = {Path("/var"), Path("/tmp")}
        if path.is_symlink() or any(parent.is_symlink() and parent not in trusted_system_aliases for parent in path.absolute().parents):
            return False, "symlinked PNG evidence is not allowed"
        data = path.read_bytes()
    except OSError as error:
        return False, f"cannot read PNG: {error}"
    signature = b"\x89PNG\r\n\x1a\n"
    if len(data) < 33 or data[:8] != signature:
        return False, "file is not a valid PNG"
    offset = 8
    ihdr: tuple[int, int, int, int, int, int, int] | None = None
    compressed = bytearray()
    saw_iend = False
    try:
        while offset + 12 <= len(data):
            length = struct.unpack_from(">I", data, offset)[0]
            chunk_type = data[offset + 4:offset + 8]
            chunk_start = offset + 8
            chunk_end = chunk_start + length
            if chunk_end + 4 > len(data):
                return False, "truncated PNG chunk"
            chunk = data[chunk_start:chunk_end]
            expected_crc = struct.unpack_from(">I", data, chunk_end)[0]
            if binascii.crc32(chunk_type + chunk) & 0xFFFFFFFF != expected_crc:
                return False, "PNG chunk checksum mismatch"
            if chunk_type == b"IHDR":
                if ihdr is not None or len(chunk) != 13:
                    return False, "invalid PNG IHDR"
                ihdr = struct.unpack(">IIBBBBB", chunk)
            elif chunk_type == b"IDAT":
                compressed.extend(chunk)
            elif chunk_type == b"IEND":
                saw_iend = True
                break
            offset = chunk_end + 4
    except (struct.error, ValueError):
        return False, "invalid PNG structure"
    if ihdr is None or not saw_iend or not compressed:
        return False, "incomplete PNG stream"
    width, height, bit_depth, color_type, compression, filtering, interlace = ihdr
    if width < 1 or height < 1 or bit_depth not in (8, 16) or color_type not in (0, 2, 4, 6) or (compression, filtering, interlace) != (0, 0, 0):
        return False, "unsupported PNG format"
    try:
        _decode_png(data)
    except (ValueError, OSError, struct.error, zlib.error) as error:
        return False, f"invalid PNG image data: {error}"
    bounds = {"mobile": (390, 844), "desktop": (1280, 800)}
    if platform in bounds:
        minimum_width, minimum_height = bounds[platform]
        if width < minimum_width or height < minimum_height:
            return False, f"screenshot is below {platform} viewport {minimum_width}x{minimum_height}"
    elif width < 390 or height < 844:
        return False, "screenshot is below supported viewport minimum"
    return True, None


def _prompt(platform: str) -> str:
    return (
        "Audit this UI screenshot for pixel-visible defects. Return JSON only with exactly top-level "
        "pass (boolean) and defects (array). Each defect must contain severity (P0/P1/P2), category "
        "(alignment/overlap/clipping/proportion/ghosting), a non-empty description, and concrete evidence. "
        f"Platform: {platform}. P0/P1 are blocking; P2 is a warning."
    )


def _openai_endpoint() -> str | None:
    for name in ("VISION_AUDIT_ENDPOINT", "FOUNDRY_ENDPOINT"):
        endpoint = os.environ.get(name)
        if endpoint and endpoint.startswith("https://"):
            return endpoint
    return None


def _provider_config(provider: str) -> tuple[str, str, str | None, str | None]:
    provider = provider.lower()
    if provider not in _ALLOWED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    endpoint = _openai_endpoint()
    key = os.environ.get("VISION_AUDIT_API_KEY") or os.environ.get("FOUNDRY_API_KEY")
    if provider == "auto":
        if endpoint and key:
            return "openai", endpoint, key, os.environ.get("VISION_AUDIT_MODEL") or os.environ.get("FOUNDRY_MODEL")
        return "magpie", _MAGPIE_ENDPOINT, None, "magpie/group/reviewing"
    if provider == "openai":
        if not endpoint:
            raise ValueError("openai provider requires an HTTPS VISION_AUDIT_ENDPOINT or FOUNDRY_ENDPOINT")
        if not key:
            raise ValueError("openai provider requires VISION_AUDIT_API_KEY or FOUNDRY_API_KEY")
        return "openai", endpoint, key, os.environ.get("VISION_AUDIT_MODEL") or os.environ.get("FOUNDRY_MODEL")
    return "magpie", _MAGPIE_ENDPOINT, None, "magpie/group/reviewing"


def _content_from_chat_response(response: Any) -> Any:
    if isinstance(response, dict) and "pass" in response and "defects" in response:
        return response
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("provider response missing choices[0].message.content")
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    if not isinstance(content, str):
        raise ValueError("provider message content must be JSON text")
    try:
        return json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"provider message content is not valid JSON: {error.msg}") from error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, msg, headers, new_url):
        return None


def _request_provider(path: Path, platform: str, provider: str) -> Any:
    kind, endpoint, key, model = _provider_config(provider)
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as error:
        raise ValueError(f"cannot read image: {error.strerror or error.__class__.__name__}") from error
    body = {
        "model": model or "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are a strict visual QA auditor. Output JSON only."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _prompt(platform)},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                ],
            },
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read()
        decoded = json.loads(raw.decode("utf-8"))
        return _content_from_chat_response(decoded)
    except urllib.error.HTTPError as error:
        raise ValueError(f"{kind} provider HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise ValueError(f"{kind} provider unavailable") from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{kind} provider returned invalid JSON") from error


def _load_mock(path: str) -> Any:
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"mock response is not valid JSON: {error}") from error


def _mock_for_image(mock: Any, index: int, count: int) -> Any:
    if isinstance(mock, list):
        if len(mock) != count:
            return {"_error": f"mock response list length {len(mock)} does not match image count {count}"}
        return mock[index]
    return mock


def _write_receipt(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_vision_audit(
    *,
    directories: Iterable[str] = (),
    images: Iterable[str] = (),
    platform: str = "mobile",
    provider: str = "auto",
    mock_response: str | None = None,
) -> dict[str, Any]:
    """Audit PNGs and return the stable receipt consumed by downstream gates."""
    platform = {"ios": "mobile", "macos": "desktop"}.get(platform.lower(), platform.lower())
    if platform not in _ALLOWED_PLATFORMS:
        raise ValueError(f"unsupported platform: {platform}")
    paths, input_errors = discover_pngs(directories, images)
    results: list[dict[str, Any]] = []
    try:
        mock = _load_mock(mock_response) if mock_response else None
        if mock_response and isinstance(mock, dict) and "_error" in mock:
            raise ValueError(str(mock["_error"]))
    except ValueError as error:
        input_errors.append(str(error))
        mock = None

    provider_error: str | None = None
    if not mock_response:
        try:
            _provider_config(provider)
        except ValueError as error:
            provider_error = str(error)
            input_errors.append(provider_error)

    for index, path in enumerate(paths):
        valid_png, png_error = _is_png(path, platform)
        if not valid_png:
            results.append(_error_result(str(path), png_error or "file is not a valid PNG"))
            continue
        if input_errors and not mock_response and provider_error:
            results.append(_error_result(str(path), provider_error))
            continue
        try:
            response = _mock_for_image(mock, index, len(paths)) if mock_response else _request_provider(path, platform, provider)
            if isinstance(response, dict) and "_error" in response:
                raise ValueError(str(response["_error"]))
            valid, errors, normalized = validate_vision_response(response)
            if not valid:
                results.append(_error_result(str(path), *errors))
            else:
                results.append({"image": str(path), "valid": True, **normalized})
        except (OSError, ValueError) as error:
            results.append(_error_result(str(path), str(error)))

    blocking = sum(
        1 if not result.get("valid") else sum(
            1 for defect in result.get("defects", [])
            if isinstance(defect, dict) and defect.get("severity") in {"P0", "P1"}
        )
        for result in results
    )
    if input_errors:
        blocking = max(1, blocking)
    passed = bool(paths) and not input_errors and all(
        result.get("valid") is True
        and result.get("pass") is True
        and not any(defect.get("severity") in {"P0", "P1"} for defect in result.get("defects", []) if isinstance(defect, dict))
        for result in results
    )
    receipt: dict[str, Any] = {
        "schema": VISION_AUDIT_SCHEMA,
        "platform": platform,
        "checked_count": len(paths),
        "pass": passed,
        "blocking_defect_count": blocking,
        "results": results,
    }
    if input_errors:
        receipt["errors"] = input_errors
    return receipt


def command_vision_audit(args: Any) -> int:
    try:
        receipt = run_vision_audit(
            directories=getattr(args, "directories", None) or (),
            images=getattr(args, "images", None) or (),
            platform=getattr(args, "platform", None) or "mobile",
            provider=getattr(args, "provider", None) or "auto",
            mock_response=getattr(args, "mock_response", None),
        )
    except ValueError as error:
        receipt = {
            "schema": VISION_AUDIT_SCHEMA,
            "platform": getattr(args, "platform", None) or "mobile",
            "checked_count": 0,
            "pass": False,
            "blocking_defect_count": 1,
            "results": [],
            "errors": [str(error)],
        }
    try:
        _write_receipt(getattr(args, "out", None), receipt)
    except OSError as error:
        receipt.setdefault("errors", []).append(f"cannot write receipt: {error}")
        receipt["pass"] = False
        receipt["blocking_defect_count"] = max(1, receipt.get("blocking_defect_count", 0))
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["pass"] is True else 1
