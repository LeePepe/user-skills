"""Bounded loopback SystemOne evaluator; never certifies absent evidence."""
import ipaddress
import json
import math
import os
import time
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

ACCEPT = {'PASS', 'ACCEPT', 'ACCEPTED'}


def _numeric(value):
    return type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 1


def _verdict(value):
    if not isinstance(value, dict):
        return False, 'answer must be an object containing numeric score/noul'
    metrics = [value[k] for k in ('score', 'noul') if k in value]
    if not metrics or not all(_numeric(n) for n in metrics):
        return False, 'score/noul must be finite numeric values in [0,1], not bool/string'
    if any(n < .6 for n in metrics):
        return False, 'negative score/noul'
    for key in ('status', 'decision'):
        if key in value and (not isinstance(value[key], str) or value[key].upper() not in ACCEPT):
            return False, 'explicit negative or unknown decision'
    return True, ''


def validate_response(data, questions, mode):
    errors = []
    if not isinstance(data, dict):
        return {'status': 'REJECT_REVISE', 'mode': mode, 'reason': 'Jev response must be an object'}
    # SystemOne's native response is keyed by question; wrappers are accepted too.
    answers = data.get('answers', data.get('results', data))
    if not isinstance(answers, dict):
        errors.append('answers/results must be a dictionary')
        answers = {}
    if any(k in data for k in ('score', 'noul')):
        ok, reason = _verdict(data)
        if not ok: errors.append(f'aggregate: {reason}')
    else:
        for k in ('status', 'decision'):
            if k in data and (not isinstance(data[k], str) or data[k].upper() not in ACCEPT):
                errors.append('aggregate: explicit negative decision')
    for q in questions:
        ok, reason = _verdict(answers.get(q))
        if not ok: errors.append(f'{q}: {reason}')
    scores = {q: {k: answers[q][k] for k in ('score', 'noul') if k in answers[q] and _numeric(answers[q][k])}
              for q in questions if isinstance(answers.get(q), dict)}
    return {'status': 'REJECT_REVISE' if errors else 'PASS', 'mode': mode,
            'questions': list(questions), 'scores': scores, 'errors': errors}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.URLError('SystemOne redirects are forbidden')


def _auth():
    token = os.environ.get('FALCON_API_KEY') or os.environ.get('JEV_API_KEY')
    source = os.environ.get('FALCON_SOURCE_KEY')
    if not source:
        try:
            keys = json.loads((Path.home() / '.config' / 'falcon' / 'source-keys.json').read_text())
            if isinstance(keys, dict):
                source = keys.get('my-designer') or keys.get('design-studio')
        except (OSError, ValueError):
            pass
    token = token if isinstance(token, str) and token.strip() else None
    source = source if isinstance(source, str) and source.strip() else None
    return token or source, source


def live_response(url, timeout, state, questions):
    parsed = urllib.parse.urlsplit(url)
    try:
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
    except (ValueError, TypeError):
        local = False
    if not local or parsed.scheme != 'http' or parsed.username or parsed.password or parsed.path != '/v1/systemone' or parsed.query or parsed.fragment:
        raise ValueError('Jev URL must be a loopback http://host:port/v1/systemone; external endpoints/redirects forbidden')
    if type(timeout) not in {float, int} or not math.isfinite(timeout) or not 0 < timeout <= 5:
        raise ValueError('Jev timeout must be >0 and <=5 seconds')
    if not isinstance(state, dict) or not state.get('evidence') or not questions:
        raise ValueError('SystemOne requires actual evidence and all platform questions')
    native = {name: {'type': 'noul', 'instructions': instructions + ' Reject absent evidence, contradictions, or failed deterministic structure.',
                     'criteria': {'true': 'Evidence satisfies every condition.', 'false': 'Evidence is missing, contradictory or violates a condition.'}}
              for name, instructions in questions.items()}
    payload = {'model': 'microsoft-decision-1', 'state': state, 'questions': native}
    token, source = _auth()
    headers = {'Content-Type': 'application/json', 'X-Falcon-Agent': 'my-designer'}
    if token: headers['Authorization'] = f'Bearer {token}'
    if source: headers['X-Falcon-Source-Key'] = source
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method='POST')
    # Ignore ambient proxies: evidence never leaves the loopback client path.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + timeout
    chunks, length = [], 0
    with opener.open(request, timeout=timeout) as response:
        sock = response.fp.raw._sock
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('SystemOne total response deadline exceeded')
            sock.settimeout(remaining)
            chunk = response.read1(min(65536, 1_048_577 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > 1_048_576:
                raise ValueError('SystemOne response exceeds 1MiB bound')
            if response.isclosed():
                break
    return json.loads(b''.join(chunks))


def response(args, evidence, platform, taxonomy):
    if args.no_jev and args.mock_jev_json:
        return {'status': 'REJECT_REVISE', 'reason': '--no-jev cannot be combined with --mock-jev-json'}
    if args.no_jev:
        return {'status': 'PASS', 'mode': 'deterministic-only', 'certified_by_jev': False}
    questions = dict(taxonomy.get('jev_verification_gates', {}).get(platform, {}))
    if not questions:
        return {'status': 'REJECT_REVISE', 'reason': 'required taxonomy questions missing'}
    if getattr(args, 'stage', '') == 'traceback':
        questions['q_traceback_evidence_attribution'] = ('Verify each structured finding against its cited explicit constraint, observed implementation and remedy route. '
            'A declared violation must be IMPL_BUG to Writer; an undeclared gap with conforming implementation must go to Steering for spec amendment first. '
            'Clean results require evidence-supported passing checks. Reject unsupported observations or misleading attribution.')
    mode = 'mock' if args.mock_jev_json else 'live'
    try:
        if args.mock_jev_json:
            raw = args.mock_jev_json
            data = json.loads(raw if raw.lstrip().startswith(('{', '[')) else Path(raw).read_text())
        else:
            data = live_response(getattr(args, 'jev_url', 'http://127.0.0.1:19823/v1/systemone'),
                                 getattr(args, 'jev_timeout', 1.5), evidence, questions)
        return validate_response(data, questions, mode)
    except (OSError, ValueError, TypeError) as error:
        # Never include auth headers or the request body in an error/receipt.
        return {'status': 'REJECT_REVISE', 'mode': mode, 'reason': f'SystemOne inaccessible/invalid: {type(error).__name__}'}
