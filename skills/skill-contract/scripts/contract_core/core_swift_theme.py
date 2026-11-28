"""Swift OS-native appearance hygiene scanner (`custom-theme-plumbing`) for repo-scoped audits."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .core_common import _enrich_finding

DESIGN_THEME_CALL_RE = re.compile(r"\.designTheme\s*\(")
ENV_THEME_INIT_RE = re.compile(r"\.environment\s*\(\s*\\\.theme\s*,\s*Theme\s*\(")
ISDARK_LABEL_RE = re.compile(r"\bisDark\s*:")
APP_SWIFT_ROOTS = ("Apps", "Sources", "Packages")
APP_SWIFT_IGNORE_PARTS = {
    "Tests",
    "UITests",
    "TestSupport",
    ".build",
    "DerivedData",
    "Carthage",
    "Pods",
    "node_modules",
}
SWIFT_OP_HEAD_CHARS = frozenset("+-*%<>&|^~!=?.")
SWIFT_REGEX_KEYWORDS = frozenset(
    {
        "return",
        "case",
        "in",
        "where",
        "throw",
        "try",
        "await",
        "if",
        "guard",
        "while",
        "switch",
        "for",
        "as",
        "is",
        "else",
        "repeat",
        "consume",
        "copy",
    }
)


def _scan_swift_regex_end(text: str, start: int, hashes: int) -> int | None:
    """Return index after closing `/#...` if `start` opens a valid Swift regex literal, else None."""
    n = len(text)
    slash_idx = start + hashes
    if slash_idx >= n or text[slash_idx] != "/":
        return None
    closing = "/" + ("#" * hashes)
    multiline = hashes > 0 and slash_idx + 1 < n and text[slash_idx + 1] in {" ", "\t", "\n", "\r"}
    if hashes == 0:
        if slash_idx + 1 >= n or text[slash_idx + 1] in {"/", "*", " ", "\t", "\n", "\r"}:
            return None
    i = slash_idx + 1
    class_depth = 0
    prev_escaped = False
    while i < n:
        ch = text[i]
        if ch in {"\n", "\r"} and not multiline:
            return None
        if ch == "\\" and i + 1 < n:
            prev_escaped = True
            i += 2
            continue
        if ch == "[":
            class_depth += 1
            prev_escaped = False
            i += 1
            continue
        if ch == "]" and class_depth > 0:
            class_depth -= 1
            prev_escaped = False
            i += 1
            continue
        if class_depth == 0 and text.startswith(closing, i):
            if hashes == 0 and i > slash_idx + 1 and text[i - 1] in {" ", "\t"} and not prev_escaped:
                return None
            after_idx = i + len(closing)
            if hashes == 0 and after_idx < n and (text[after_idx].isalnum() or text[after_idx] == "_"):
                return None
            return after_idx
        prev_escaped = False
        i += 1
    return None


def _scan_swift_code(text: str, i: int, out: list[str], *, stop_at_close_paren: bool = False) -> int:
    """Scan Swift code from index `i`, masking comments, strings, and regex literals while preserving offsets."""
    n = len(text)
    paren_depth = 1 if stop_at_close_paren else 0
    prev_sig = ""
    prev_word = ""
    prev_was_space = True
    while i < n:
        ch = text[i]
        if ch in {" ", "\t", "\n", "\r"}:
            out.append(ch)
            prev_was_space = True
            i += 1
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            out.append(word)
            prev_sig = word[-1]
            prev_word = word
            prev_was_space = False
            i = j
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            prev_was_space = True
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            depth = 1
            out.extend((" ", " "))
            i += 2
            while i < n and depth > 0:
                if text[i] == "/" and i + 1 < n and text[i + 1] == "*":
                    depth += 1
                    out.extend((" ", " "))
                    i += 2
                elif text[i] == "*" and i + 1 < n and text[i + 1] == "/":
                    depth -= 1
                    out.extend((" ", " "))
                    i += 2
                else:
                    out.append("\n" if text[i] == "\n" else " ")
                    i += 1
            prev_was_space = True
            continue
        hashes = 0
        if ch == "#":
            j = i
            while j < n and text[j] == "#":
                j += 1
            if j < n and text[j] == "/":
                regex_end = _scan_swift_regex_end(text, i, j - i)
                if regex_end is not None:
                    for k in range(i, regex_end):
                        out.append("\n" if text[k] == "\n" else " ")
                    i = regex_end
                    prev_sig = "/"
                    prev_word = ""
                    prev_was_space = False
                    continue
            if j < n and text[j] == '"':
                hashes = j - i
        elif ch == "/":
            can_start_bare_regex = (
                prev_was_space
                or prev_sig in {"", "(", "[", "{", ",", ":", ";"}
                or prev_sig in SWIFT_OP_HEAD_CHARS
                or prev_word in SWIFT_REGEX_KEYWORDS
            )
            if can_start_bare_regex:
                regex_end = _scan_swift_regex_end(text, i, 0)
                if regex_end is not None:
                    for k in range(i, regex_end):
                        out.append("\n" if text[k] == "\n" else " ")
                    i = regex_end
                    prev_sig = "/"
                    prev_word = ""
                    prev_was_space = False
                    continue
        if ch == '"' or hashes > 0:
            q_start = i + hashes
            is_multiline = text.startswith('"""', q_start)
            delim_quotes = '"""' if is_multiline else '"'
            closing = delim_quotes + ("#" * hashes)
            escape_seq = "\\" + ("#" * hashes)
            interp_seq = escape_seq + "("
            prefix_len = hashes + len(delim_quotes)
            out.extend(" " for _ in range(prefix_len))
            i = q_start + len(delim_quotes)
            while i < n:
                if text.startswith(closing, i):
                    out.extend(" " for _ in range(len(closing)))
                    i += len(closing)
                    break
                if text.startswith(interp_seq, i):
                    out.extend(" " for _ in range(len(interp_seq)))
                    i = _scan_swift_code(text, i + len(interp_seq), out, stop_at_close_paren=True)
                    continue
                if text.startswith(escape_seq, i) and i + len(escape_seq) < n:
                    step = len(escape_seq) + 1
                    for k in range(step):
                        out.append("\n" if text[i + k] == "\n" else " ")
                    i += step
                    continue
                if not is_multiline and hashes == 0 and text[i] == "\n":
                    out.append("\n")
                    i += 1
                    break
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            prev_sig = '"'
            prev_word = ""
            prev_was_space = False
            continue
        if stop_at_close_paren:
            if ch == "(":
                paren_depth += 1
            elif ch == ")":
                paren_depth -= 1
                if paren_depth == 0:
                    out.append(" ")
                    return i + 1
        out.append(ch)
        prev_sig = ch
        prev_word = ""
        prev_was_space = False
        i += 1
    return i


def _mask_swift_non_code(text: str) -> str:
    """Mask Swift line comments, nested block comments, and single/multiline/raw string literals."""
    out: list[str] = []
    _scan_swift_code(text, 0, out, stop_at_close_paren=False)
    return "".join(out)


def _custom_theme_plumbing_spans(masked: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = [(m.start(), m.end()) for m in DESIGN_THEME_CALL_RE.finditer(masked)]
    n = len(masked)
    for match in ENV_THEME_INIT_RE.finditer(masked):
        depth = 1
        cursor = match.end()
        while cursor < n and depth > 0:
            if masked[cursor] == "(":
                depth += 1
            elif masked[cursor] == ")":
                depth -= 1
            cursor += 1
        theme_args = masked[match.end() : max(match.end(), cursor - 1)]
        if ISDARK_LABEL_RE.search(theme_args):
            env_end = cursor
            while env_end < n and masked[env_end].isspace():
                env_end += 1
            if env_end < n and masked[env_end] == ")":
                env_end += 1
            spans.append((match.start(), env_end))
    return sorted(spans, key=lambda item: (item[0], item[1]))


def check_repo_appearance_hygiene(repo_root: str | Path) -> list[dict[str, Any]]:
    """Scan non-test Apple Swift sources under Apps/, Sources/, and Packages/ for custom theme plumbing."""
    base = Path(repo_root).expanduser().resolve()
    if not base.is_dir():
        return []
    findings: list[dict[str, Any]] = []
    seen_files: set[Path] = set()
    for folder in APP_SWIFT_ROOTS:
        scan_root = base / folder
        if not scan_root.is_dir():
            continue
        for swift_path in sorted(scan_root.rglob("*.swift")):
            if not swift_path.is_file():
                continue
            resolved = swift_path.resolve()
            if resolved in seen_files:
                continue
            try:
                rel_parts = swift_path.relative_to(base).parts
            except ValueError:
                rel_parts = swift_path.parts
            if any(part in APP_SWIFT_IGNORE_PARTS or part.endswith("Tests") for part in rel_parts[:-1]):
                continue
            seen_files.add(resolved)
            try:
                raw_text = swift_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = raw_text.splitlines()
            masked = _mask_swift_non_code(raw_text)
            rel_file = swift_path.relative_to(base).as_posix() if swift_path.is_relative_to(base) else str(swift_path)
            for start, end in _custom_theme_plumbing_spans(masked):
                start_line = masked.count("\n", 0, start) + 1
                end_line = masked.count("\n", 0, max(start, end - 1)) + 1
                spanned_raw = "\n".join(lines[start_line - 1 : end_line])
                if "design-contract-allow" in spanned_raw:
                    continue
                findings.append(
                    _enrich_finding(
                        {
                            "code": "custom-theme-plumbing",
                            "file": rel_file,
                            "line": start_line,
                            "match": raw_text[start:end].replace("\n", " ").strip()[:200],
                            "guidance_drawer": "skills/my-designer/references/appearance-ledger.md",
                        }
                    )
                )
    return findings


__all__ = [name for name in globals() if not name.startswith("__")]
