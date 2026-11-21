"""Bounded loopback SystemOne evaluator; never certifies absent evidence."""
import argparse
import importlib.util
import sys
import http.client
import ipaddress
import json
import math
import os
import socket
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


class _DeadlineSocket(socket.socket):
    def __init__(self, family, deadline):
        super().__init__(family, socket.SOCK_STREAM)
        self.deadline = deadline

    def _bound(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('SystemOne total response deadline exceeded')
        self.settimeout(remaining)

    def connect(self, address):
        self._bound()
        return super().connect(address)

    def sendall(self, data, flags=0):
        self._bound()
        return super().sendall(data, flags)

    def recv_into(self, buffer, nbytes=0, flags=0):
        # Buffered status/header readline calls SocketIO.recv_into repeatedly.
        # Rebound each actual receive, so trickled bytes cannot renew the budget.
        self._bound()
        return super().recv_into(buffer, nbytes, flags)


class _DeadlineConnection(http.client.HTTPConnection):
    def __init__(self, host, deadline, **kwargs):
        super().__init__(host, **kwargs)
        self.deadline = deadline

    def connect(self):
        # The authorized localhost alias needs no potentially unbounded DNS.
        hosts = ('127.0.0.1', '::1') if self.host.casefold() == 'localhost' else (self.host,)
        for host in hosts:
            sock = _DeadlineSocket(socket.AF_INET6 if ':' in host else socket.AF_INET, self.deadline)
            try:
                sock.connect((host, self.port))
                self.sock = sock
                return
            except OSError:
                sock.close()
                if host == hosts[-1]:
                    raise


class _DeadlineHandler(urllib.request.HTTPHandler):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def http_open(self, request):
        return self.do_open(lambda host, **kwargs: _DeadlineConnection(host, self.deadline, **kwargs), request)


def _validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    try:
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
    except (ValueError, TypeError):
        local = False
    if not local or parsed.scheme != 'http' or parsed.username or parsed.password or parsed.path != '/v1/systemone' or parsed.query or parsed.fragment:
        raise ValueError('Jev URL must be a loopback http://host:port/v1/systemone; external endpoints/redirects forbidden')
    return parsed


def live_response(url, timeout, state, questions):
    _validate_url(url)
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
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), _DeadlineHandler(deadline))
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


CLI_GUIDANCE = [
    'Read skills/my-designer/references/eval-cases.json for positive/counter-case Jev evidence replays.',
    'Read skills/my-designer/references/routing-index.md for stage/platform routing.',
]


class JevArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write(f'{self.prog}: {message}\n' + '\n'.join(CLI_GUIDANCE) + '\n')
        self.exit(2)


def build_parser():
    parser = JevArgumentParser(description=__doc__)
    parser.add_argument('--self-check', action='store_true')
    parser.add_argument('--stage', choices=['gallery', 'spec', 'traceback'])
    parser.add_argument('--platform', '--surface', choices=['desktop', 'macos', 'mobile', 'ios', 'web'], default='desktop')
    parser.add_argument('--spec-file', '--spec', dest='spec_file')
    parser.add_argument('--gallery-dir', '--dir', dest='gallery_dir')
    parser.add_argument('--taxonomy', default=str(Path(__file__).resolve().parents[2] / 'design-studio/scripts/data/taxonomy-v2.json'))
    parser.add_argument('--no-jev', action='store_true')
    parser.add_argument('--mock-jev-json')
    parser.add_argument('--jev-url', default='http://127.0.0.1:19823/v1/systemone')
    parser.add_argument('--jev-timeout', type=float, default=1.5)
    parser.add_argument('--json', action='store_true', help='Emit JSON output (default)')
    return parser


def self_check():
    questions = {'q': 'Evidence satisfies the constraint'}
    cases = [({'q': {'noul': 1}}, True), ({'q': {'score': .6}}, True),
             ({'q': {'noul': 0, 'status': 'PASS'}}, False), ({'q': {'noul': True}}, False),
             ({'q': {'score': '1'}}, False), ({'q': {'score': float('nan')}}, False),
             ({'q': {'score': 1, 'decision': 'REJECT'}}, False), ({}, False)]
    checks = {'truth_table': all((validate_response(data, questions, 'self-check')['status'] == 'PASS') == expected for data, expected in cases)}
    guards = []
    for url, expected in [('http://127.0.0.1:19823/v1/systemone', True), ('http://[::1]:19823/v1/systemone', True),
                          ('https://127.0.0.1/v1/systemone', False), ('http://example.com/v1/systemone', False),
                          ('http://user@localhost/v1/systemone', False), ('http://localhost/v1/systemone?leak=1', False)]:
        try:
            _validate_url(url)
            guards.append(expected)
        except ValueError:
            guards.append(not expected)
    checks['loopback_url_guard'] = all(guards)
    return {'ok': all(checks.values()), 'checks': checks}


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_check:
        result = self_check()
        print(json.dumps(result))
        if not result['ok']:
            sys.stderr.write('\n'.join(CLI_GUIDANCE) + '\n')
        return 0 if result['ok'] else 1
    if not args.stage:
        parser.error('choose --self-check or --stage with --spec-file/--gallery-dir evidence')
    args.platform = {'ios': 'mobile', 'macos': 'desktop'}.get(args.platform, args.platform)
    # Reuse the deterministic stage gate; standalone Jev must not certify absent evidence.
    spec = importlib.util.spec_from_file_location('standalone_design_gate', Path(__file__).with_name('verify_design_cases.py'))
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    code = gate.command_jev_gate(args)
    if code:
        sys.stderr.write('\n'.join(CLI_GUIDANCE) + '\n')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
