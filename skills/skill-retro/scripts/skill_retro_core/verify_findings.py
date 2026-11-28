from __future__ import annotations

import shlex
from .common import *

"""Layer C Automated Repro-Filter (`verify-findings`) for the 3-Gate Zero-Owner-Referee Flow."""

VALID_FINDING_CATEGORIES: frozenset[str] = frozenset({'contract_breach', 'architecture_smell', 'tradeoff'})
VERIFY_FINDINGS_MAX_TIMEOUT_SEC = 10.0
_SHELL_META_RE = re.compile(r'(\|\||&&|[|;<>`]|[$]\()')
_DESTRUCTIVE_REPRO_RE = re.compile(
    r'(?:^|[\s;|&])(?:rm\s+-rf\b|git\s+reset\s+--hard\b|git\s+clean\s+-fd|git\s+push\b|mkfs\b|dd\s+if=)'
)


def _load_findings_payload(findings_input: Any) -> list[dict[str, Any]]:
    if isinstance(findings_input, (str, Path)):
        raw_arg = str(findings_input).strip()
        if not raw_arg:
            raise ValueError('empty --findings-json input')
        if raw_arg == '-':
            raw_text = sys.stdin.read()
        else:
            candidate = Path(raw_arg).expanduser()
            if not raw_arg.startswith(('{', '[')) and candidate.exists():
                if not candidate.is_file():
                    raise ValueError(f'findings path is not a regular file: {candidate}')
                raw_text = candidate.read_text(encoding='utf-8')
            elif not raw_arg.startswith(('{', '[')) and ('/' in raw_arg or raw_arg.endswith('.json')):
                raise ValueError(f'findings file not found: {candidate}')
            else:
                raw_text = raw_arg
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f'invalid JSON in --findings-json: {exc.msg}') from exc
    else:
        parsed = findings_input

    if isinstance(parsed, Mapping):
        raw_list = parsed.get('findings')
        if not isinstance(raw_list, Sequence) or isinstance(raw_list, (str, bytes)):
            raise ValueError("findings JSON object must contain a 'findings' array")
    elif isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes)):
        raw_list = parsed
    else:
        raise ValueError('findings payload must be a JSON array or an object with "findings"')

    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_list, 1):
        if not isinstance(item, Mapping):
            raise ValueError(f'finding #{idx} must be a JSON object')
        fid = str(item.get('id') or '').strip()
        title = str(item.get('title') or '').strip()
        category = str(item.get('category') or '').strip().lower()
        if not fid or not title:
            raise ValueError(f"finding #{idx} requires non-empty 'id' and 'title'")
        if category not in VALID_FINDING_CATEGORIES:
            allowed = ', '.join(sorted(VALID_FINDING_CATEGORIES))
            raise ValueError(f"finding {fid!r} has invalid category {category!r} (expected one of: {allowed})")
        normalized.append(dict(item, id=fid, title=title, category=category))
    return normalized


def _extract_repro_spec(finding: Mapping[str, Any]) -> tuple[str | list[str] | None, int | None, str | None]:
    raw_repro = finding.get('repro_cmd')
    if raw_repro is None:
        raw_repro = finding.get('repro_check')
    expect_exit_raw = finding.get('expect_exit', finding.get('repro_expect_exit'))
    expect_stdout_raw = finding.get('expect_stdout', finding.get('repro_stdout_contains'))

    if isinstance(raw_repro, Mapping):
        if expect_exit_raw is None:
            expect_exit_raw = raw_repro.get('expect_exit')
        if expect_stdout_raw is None:
            expect_stdout_raw = raw_repro.get('expect_stdout')
        raw_repro = raw_repro.get('cmd') or raw_repro.get('command')

    cmd: str | list[str] | None = None
    if isinstance(raw_repro, str):
        cmd = raw_repro.strip() or None
    elif isinstance(raw_repro, Sequence) and not isinstance(raw_repro, (str, bytes)):
        tokens = [str(tok) for tok in raw_repro if str(tok)]
        cmd = tokens or None

    expect_exit = int(expect_exit_raw) if expect_exit_raw is not None else None
    expect_stdout = str(expect_stdout_raw) if expect_stdout_raw is not None and str(expect_stdout_raw) != '' else None
    return (cmd, expect_exit, expect_stdout)


def _execute_repro_command(
    cmd: str | list[str] | None,
    *,
    cwd: Path,
    timeout_sec: float,
    expect_exit: int | None=None,
    expect_stdout: str | None=None,
) -> dict[str, Any]:
    if cmd is None:
        return {
            'reproduced': False,
            'reason': 'missing_repro_cmd',
            'exit_code': None,
            'timed_out': False,
            'duration_ms': 0.0,
            'stdout_snippet': '',
            'stderr_snippet': '',
        }

    cmd_text = ' '.join(cmd) if isinstance(cmd, list) else cmd
    if _DESTRUCTIVE_REPRO_RE.search(cmd_text):
        return {
            'reproduced': False,
            'reason': 'destructive_repro_cmd_blocked',
            'exit_code': None,
            'timed_out': False,
            'duration_ms': 0.0,
            'stdout_snippet': '',
            'stderr_snippet': 'blocked destructive command in repro_cmd',
        }

    use_shell = False
    if isinstance(cmd, list):
        argv = list(cmd)
    elif _SHELL_META_RE.search(cmd):
        argv = ['/bin/sh', '-c', cmd]
        use_shell = True
    else:
        try:
            argv = shlex.split(cmd)
        except ValueError as exc:
            return {
                'reproduced': False,
                'reason': 'malformed_repro_cmd',
                'exit_code': None,
                'timed_out': False,
                'duration_ms': 0.0,
                'stdout_snippet': '',
                'stderr_snippet': str(exc)[:240],
            }
    if not argv:
        return {
            'reproduced': False,
            'reason': 'missing_repro_cmd',
            'exit_code': None,
            'timed_out': False,
            'duration_ms': 0.0,
            'stdout_snippet': '',
            'stderr_snippet': '',
        }
    if not use_shell and argv[0] in {'python', 'python3'}:
        argv[0] = sys.executable

    t0 = time.monotonic()
    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        stdout_raw, stderr_raw = proc.communicate(timeout=timeout_sec)
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
        rc = int(proc.returncode)
    except subprocess.TimeoutExpired:
        if proc is not None:
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            with contextlib.suppress(Exception):
                proc.communicate(timeout=0.5)
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
        return {
            'reproduced': False,
            'reason': 'repro_timeout_exceeded',
            'exit_code': None,
            'timed_out': True,
            'duration_ms': elapsed_ms,
            'stdout_snippet': '',
            'stderr_snippet': f'repro_cmd timed out after {timeout_sec:.2f}s',
        }
    except OSError as exc:
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
        return {
            'reproduced': False,
            'reason': 'repro_command_launch_error',
            'exit_code': 127,
            'timed_out': False,
            'duration_ms': elapsed_ms,
            'stdout_snippet': '',
            'stderr_snippet': str(exc)[:240],
        }

    stdout_clean = redact_text((stdout_raw or '').strip())[:400]
    stderr_clean = redact_text((stderr_raw or '').strip())[:400]
    combined_out = f'{stdout_raw or ""}\n{stderr_raw or ""}'

    if rc in {126, 127} and expect_exit not in {126, 127}:
        return {
            'reproduced': False,
            'reason': 'repro_command_not_executable',
            'exit_code': rc,
            'timed_out': False,
            'duration_ms': elapsed_ms,
            'stdout_snippet': stdout_clean,
            'stderr_snippet': stderr_clean,
        }

    if expect_exit is not None:
        exit_matched = rc == expect_exit
    else:
        exit_matched = rc != 0

    stdout_matched = True if expect_stdout is None else (expect_stdout in combined_out)
    reproduced = bool(exit_matched and stdout_matched)
    return {
        'reproduced': reproduced,
        'reason': 'reproduced' if reproduced else 'did_not_reproduce',
        'exit_code': rc,
        'timed_out': False,
        'duration_ms': elapsed_ms,
        'stdout_snippet': stdout_clean,
        'stderr_snippet': stderr_clean,
    }


def verify_blind_review_findings(
    findings_input: Any,
    *,
    cwd: Path | str | None=None,
    timeout_sec: float=VERIFY_FINDINGS_MAX_TIMEOUT_SEC,
    summary: bool=False,
) -> dict[str, Any]:
    """Run the 3-Gate Zero-Owner-Referee Repro-Filter on blind-review findings.

    - Gate 1 (`No Repro, No Fix`): executes `repro_cmd` / `repro_check` (bounded `<=10s`).
      Unreproduced findings classify as `FALSE_POSITIVE_UNREPRODUCED` (`action="AUTO_DROP"`).
    - Gate 2 (`Class A / B / C Triage`):
      - `Class A` (`contract_breach` + `reproduced == True`) -> `action="AUTO_FIX"`
      - `Class B` (`architecture_smell` or unverified subjective refactor) -> `action="AUTO_DROP"`
      - `Class C` (`tradeoff` + `reproduced == True`) -> `action="OWNER_DECISION_BRIEF"`
    """
    eff_cwd = Path(cwd).expanduser().resolve() if cwd is not None else Path.cwd().resolve()
    if not eff_cwd.is_dir():
        raise ValueError(f'working directory does not exist: {eff_cwd}')
    try:
        eff_timeout = float(timeout_sec)
    except (TypeError, ValueError) as exc:
        raise ValueError('timeout_sec must be a positive number') from exc
    if eff_timeout <= 0:
        raise ValueError('timeout_sec must be > 0')
    eff_timeout = min(VERIFY_FINDINGS_MAX_TIMEOUT_SEC, eff_timeout)

    findings = _load_findings_payload(findings_input)
    confirmed_auto_fix: list[dict[str, Any]] = []
    dropped_findings: list[dict[str, Any]] = []
    owner_tradeoffs: list[dict[str, Any]] = []
    all_results: list[dict[str, Any]] = []

    for item in findings:
        fid = item['id']
        title = item['title']
        category = item['category']
        cmd, expect_exit, expect_stdout = _extract_repro_spec(item)
        repro_res = _execute_repro_command(
            cmd,
            cwd=eff_cwd,
            timeout_sec=eff_timeout,
            expect_exit=expect_exit,
            expect_stdout=expect_stdout,
        )
        reproduced = bool(repro_res['reproduced'])
        cmd_display = _redact_command_sample(
            ' '.join(cmd) if isinstance(cmd, list) else str(cmd or '')
        ) if cmd is not None else ''

        if not reproduced:
            classification = 'FALSE_POSITIVE_UNREPRODUCED'
            finding_class = 'Class B' if category == 'architecture_smell' else 'Unreproduced'
            action = 'AUTO_DROP'
            target_bucket = dropped_findings
        elif category == 'contract_breach':
            classification = 'CLASS_A_CONTRACT_BREACH'
            finding_class = 'Class A'
            action = 'AUTO_FIX'
            target_bucket = confirmed_auto_fix
        elif category == 'architecture_smell':
            classification = 'CLASS_B_ARCHITECTURE_SMELL'
            finding_class = 'Class B'
            action = 'AUTO_DROP'
            target_bucket = dropped_findings
        else:
            classification = 'CLASS_C_TRADEOFF'
            finding_class = 'Class C'
            action = 'OWNER_DECISION_BRIEF'
            target_bucket = owner_tradeoffs

        record: dict[str, Any] = {
            'id': fid,
            'title': title,
            'category': category,
            'finding_class': finding_class,
            'classification': classification,
            'action': action,
            'reproduced': reproduced,
            'repro_cmd': cmd_display,
            'repro_reason': repro_res['reason'],
            'exit_code': repro_res['exit_code'],
            'timed_out': repro_res['timed_out'],
            'duration_ms': repro_res['duration_ms'],
        }
        if not summary:
            record['stdout_snippet'] = repro_res['stdout_snippet']
            record['stderr_snippet'] = repro_res['stderr_snippet']
        target_bucket.append(record)
        all_results.append(record)

    payload: dict[str, Any] = {
        'ok': True,
        'schema': 'skill-retro-verify-findings/v1',
        'timeout_cap_sec': eff_timeout,
        'total_findings': len(all_results),
        'counts': {
            'total': len(all_results),
            'confirmed_auto_fix': len(confirmed_auto_fix),
            'dropped_findings': len(dropped_findings),
            'owner_tradeoffs': len(owner_tradeoffs),
        },
        'confirmed_auto_fix': confirmed_auto_fix[:10] if summary else confirmed_auto_fix,
        'dropped_findings': dropped_findings[:10] if summary else dropped_findings,
        'owner_tradeoffs': owner_tradeoffs[:10] if summary else owner_tradeoffs,
    }
    if not summary:
        payload['results'] = all_results
    return payload
