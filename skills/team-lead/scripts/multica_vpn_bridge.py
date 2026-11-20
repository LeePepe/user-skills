#!/usr/bin/env python3
"""Option A Multica VPN Internal TCP Bridge (`multica_vpn_bridge.py`).

Bridges Home Mac mini OpenVPN (`utun*` `10.9.0.x` / `10.8.0.x`) and Azure `OpenVPNSingapore`
internal gateway (`10.9.0.1` / `10.8.0.1`) to Home NAS Multica (`192.168.31.17:13000` & `:18080`)
with zero public port exposure.

Subcommands:
1. `serve`: Async dual-port TCP forwarder (`0.0.0.0:13000 -> 192.168.31.17:13000`,
   `0.0.0.0:18080 -> 192.168.31.17:18080`).
2. `install-launchd`: Idempotently writes and loads
   `~/Library/LaunchAgents/com.tianpli.multica.vpn-bridge.plist` (`KeepAlive=true`, `RunAtLoad=true`)
   and verifies local/VPN listeners and NAS upstream forwarding.
3. `status`: Reports LaunchAgent status, active `utun*` OpenVPN IPv4 addresses, local listeners,
   and upstream NAS Multica reachability.
4. `generate-azure-bridge-script`: Renders `/usr/local/bin/multica-vpn-gateway-bridge.py` (and optional
   systemd unit) strictly bound to internal VPN gateway IPs (`10.9.0.1`, `10.8.0.1` — never `0.0.0.0`
   or public NIC IPs) with dynamic Mac mini OpenVPN client discovery.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import time
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.request

DEFAULT_LAUNCHD_LABEL = "com.tianpli.multica.vpn-bridge"
DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_UPSTREAM_HOST = "192.168.31.17"
DEFAULT_WEB_PORT = 13000
DEFAULT_API_PORT = 18080
DEFAULT_CONNECT_TIMEOUT = 8.0
DEFAULT_BUFFER_SIZE = 65536
DEFAULT_VPN_SUBNETS = ("10.9.0.0/24", "10.8.0.0/24")
DEFAULT_AZURE_GATEWAY_HOSTS = ("10.9.0.1", "10.8.0.1")
DEFAULT_PREFERRED_MAC_VPN_IP = "10.9.0.6"


@dataclass(frozen=True)
class PortMapping:
    bind_host: str
    listen_port: int
    upstream_host: str
    upstream_port: int


def parse_port_mappings(
    *,
    bind_host: str = DEFAULT_BIND_HOST,
    upstream_host: str = DEFAULT_UPSTREAM_HOST,
    web_port: int = DEFAULT_WEB_PORT,
    api_port: int = DEFAULT_API_PORT,
    extra_forwards: Sequence[str] | None = None,
) -> list[PortMapping]:
    if extra_forwards:
        mappings: list[PortMapping] = []
        for spec in extra_forwards:
            # Format: [bind_host:]listen_port:upstream_host:upstream_port
            parts = [p.strip() for p in spec.split(":") if p.strip()]
            if len(parts) == 3:
                l_host = bind_host
                l_port_s, u_host, u_port_s = parts
            elif len(parts) == 4:
                l_host, l_port_s, u_host, u_port_s = parts
            else:
                raise ValueError(f"Invalid --forward spec {spec!r}; expected [bind_host:]listen_port:upstream_host:upstream_port")
            mappings.append(
                PortMapping(
                    bind_host=l_host,
                    listen_port=int(l_port_s),
                    upstream_host=u_host,
                    upstream_port=int(u_port_s),
                )
            )
        return mappings

    return [
        PortMapping(
            bind_host=bind_host,
            listen_port=int(web_port),
            upstream_host=upstream_host,
            upstream_port=int(web_port),
        ),
        PortMapping(
            bind_host=bind_host,
            listen_port=int(api_port),
            upstream_host=upstream_host,
            upstream_port=int(api_port),
        ),
    ]


async def _pipe_stream(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    chunk_size: int = DEFAULT_BUFFER_SIZE,
    close_on_eof: bool = False,
) -> None:
    try:
        while True:
            data = await reader.read(chunk_size)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError, OSError):
        pass
    finally:
        try:
            if writer.can_write_eof():
                writer.write_eof()
        except OSError:
            pass
        if close_on_eof:
            try:
                writer.close()
            except OSError:
                pass


def _log_bridge_error(message: str) -> None:
    print(f"[multica-vpn-bridge] ERROR: {message}", file=sys.stderr, flush=True)


async def handle_tcp_forward(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    *,
    upstream_host: str,
    upstream_port: int,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    chunk_size: int = DEFAULT_BUFFER_SIZE,
) -> None:
    try:
        sock = client_writer.get_extra_info("socket")
        if sock is not None:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(upstream_host, upstream_port),
            timeout=connect_timeout,
        )
        up_sock = upstream_writer.get_extra_info("socket")
        if up_sock is not None:
            try:
                up_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
    except (TimeoutError, asyncio.TimeoutError, OSError) as exc:
        _log_bridge_error(f"upstream connect {upstream_host}:{upstream_port} failed: {exc}")
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except OSError:
            pass
        return

    t1 = asyncio.create_task(_pipe_stream(client_reader, upstream_writer, chunk_size=chunk_size, close_on_eof=False))
    t2 = asyncio.create_task(_pipe_stream(upstream_reader, client_writer, chunk_size=chunk_size, close_on_eof=False))
    try:
        await asyncio.gather(t1, t2)
    finally:
        for w in (upstream_writer, client_writer):
            try:
                w.close()
            except OSError:
                pass


class AsyncTcpBridgeServer:
    """High-concurrency asyncio multi-port TCP forwarder."""

    def __init__(
        self,
        mappings: Sequence[PortMapping],
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        backlog: int = 256,
    ) -> None:
        self.mappings = list(mappings)
        self.connect_timeout = connect_timeout
        self.backlog = backlog
        self._servers: list[asyncio.AbstractServer] = []
        self.bound_endpoints: list[tuple[str, int, str, int]] = []

    async def start(self) -> None:
        for mapping in self.mappings:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setblocking(False)
            sock.bind((mapping.bind_host, mapping.listen_port))
            actual_host, actual_port = sock.getsockname()[:2]

            async def _cb(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
                m: PortMapping = mapping,
            ) -> None:
                await handle_tcp_forward(
                    reader,
                    writer,
                    upstream_host=m.upstream_host,
                    upstream_port=m.upstream_port,
                    connect_timeout=self.connect_timeout,
                )

            srv = await asyncio.start_server(_cb, sock=sock, backlog=self.backlog)
            self._servers.append(srv)
            self.bound_endpoints.append((str(actual_host), int(actual_port), mapping.upstream_host, mapping.upstream_port))

    async def close(self) -> None:
        for srv in self._servers:
            srv.close()
        for srv in self._servers:
            try:
                await srv.wait_closed()
            except OSError:
                pass
        self._servers.clear()


async def run_bridge_forever(
    mappings: Sequence[PortMapping],
    *,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
) -> int:
    bridge = AsyncTcpBridgeServer(mappings, connect_timeout=connect_timeout)
    await bridge.start()
    for b_host, b_port, u_host, u_port in bridge.bound_endpoints:
        print(
            f"[multica-vpn-bridge] listening on {b_host}:{b_port} -> {u_host}:{u_port}",
            flush=True,
        )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    finally:
        await bridge.close()
    return 0


def resolve_python3_binary(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    which_py = shutil.which("python3")
    candidates = [
        which_py,
        sys.executable,
        "/opt/homebrew/bin/python3",
        "/opt/homebrew/bin/python3.14",
        "/opt/homebrew/bin/python3.11",
        "/usr/bin/python3",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return sys.executable


def default_canonical_script_path() -> Path:
    """Prefer ~/.multica/multica_vpn_bridge.py or main repo path so LaunchAgent survives worktree cleanup."""
    current = Path(__file__).resolve()
    home_repo_candidate = Path.home() / "Development" / "user-skills" / "skills" / "team-lead" / "scripts" / "multica_vpn_bridge.py"
    if "worktrees" in current.parts and home_repo_candidate.is_file():
        return home_repo_candidate
    return current


def render_macos_perl_bridge_script() -> str:
    """Render an event-driven non-blocking `/usr/bin/perl` dual-port TCP bridge for macOS LaunchAgents.

    Why `/usr/bin/perl` under macOS Sequoia LaunchAgents:
    Un-signed Python.app framework bundles under launchd are blocked by macOS Sequoia Local Network TCC
    (`Errno 65 No route to host` to 192.168.31.x), whereas `/usr/bin/perl` is an Apple platform binary
    with unrestricted LAN reachability.
    """
    return textwrap.dedent(
        r'''#!/usr/bin/perl
        use strict;
        use warnings;
        use IO::Socket::INET;
        use IO::Select;
        use POSIX qw(:sys_wait_h);

        # Args: bind_host upstream_host web_port api_port
        my $bind_host     = $ARGV[0] || $ENV{MULTICA_VPN_BIND_HOST}     || '0.0.0.0';
        my $upstream_host = $ARGV[1] || $ENV{MULTICA_VPN_UPSTREAM_HOST} || '192.168.31.17';
        my $web_port      = int($ARGV[2] || $ENV{MULTICA_VPN_WEB_PORT}  || 13000);
        my $api_port      = int($ARGV[3] || $ENV{MULTICA_VPN_API_PORT}  || 18080);

        $SIG{PIPE} = 'IGNORE';
        $SIG{CHLD} = 'IGNORE';

        my @ports = ($web_port, $api_port);
        my %listener_port;
        my $accept_sel = IO::Select->new();

        for my $port (@ports) {
            my $srv = IO::Socket::INET->new(
                LocalAddr => $bind_host,
                LocalPort => $port,
                Proto     => 'tcp',
                Listen    => 256,
                ReuseAddr => 1,
            ) or die "[multica-vpn-bridge] Cannot bind $bind_host:$port: $!\n";
            $listener_port{$srv} = $port;
            $accept_sel->add($srv);
            select(STDOUT); $| = 1;
            print "[multica-vpn-bridge] listening on $bind_host:$port -> $upstream_host:$port\n";
        }

        my $running = 1;
        $SIG{TERM} = sub { $running = 0; };
        $SIG{INT}  = sub { $running = 0; };

        while ($running) {
            my @ready = $accept_sel->can_read(1.0);
            for my $srv (@ready) {
                my $target_port = $listener_port{$srv};
                my $client = $srv->accept();
                next unless $client;
                my $pid = fork();
                if (defined $pid && $pid == 0) {
                    for my $s ($accept_sel->handles) {
                        close($s);
                    }
                    my $upstream = IO::Socket::INET->new(
                        PeerAddr => $upstream_host,
                        PeerPort => $target_port,
                        Proto    => 'tcp',
                        Timeout  => 8,
                    );
                    if (!$upstream) {
                        warn "[multica-vpn-bridge] ERROR: upstream connect $upstream_host:$target_port failed: $!\n";
                        close($client);
                        exit(1);
                    }
                    $client->autoflush(1);
                    $upstream->autoflush(1);
                    my $pair_sel = IO::Select->new($client, $upstream);
                    my %half_closed;
                    while ($pair_sel->count() > 0) {
                        my @readable = $pair_sel->can_read(300);
                        last unless @readable;
                        my $fatal = 0;
                        for my $fh (@readable) {
                            my $peer = ($fh == $client) ? $upstream : $client;
                            my $buf;
                            my $n = sysread($fh, $buf, 65536);
                            if (!defined($n)) {
                                $fatal = 1;
                                last;
                            }
                            if ($n == 0) {
                                $pair_sel->remove($fh);
                                $half_closed{$fh} = 1;
                                eval { shutdown($peer, 1); };
                                last if scalar(keys %half_closed) >= 2;
                                next;
                            }
                            my $off = 0;
                            while ($off < $n) {
                                my $w = syswrite($peer, $buf, $n - $off, $off);
                                if (!defined($w)) {
                                    $fatal = 1;
                                    last;
                                }
                                $off += $w;
                            }
                            last if $fatal;
                        }
                        last if $fatal || scalar(keys %half_closed) >= 2;
                    }
                    close($client);
                    close($upstream);
                    exit(0);
                }
                close($client);
            }
        }
        exit(0);
        '''
    )


def sync_runtime_bridge_copy(source_script: Path, runtime_dir: Path | None = None) -> tuple[Path, Path]:
    """Maintain stable copies at ~/.multica/multica_vpn_bridge.{py,pl} so launchd survives worktree GC and macOS Sequoia Local Network TCC."""
    target_dir = Path(runtime_dir or (Path.home() / ".multica")).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    runtime_py = target_dir / "multica_vpn_bridge.py"
    if source_script.resolve() != runtime_py.resolve():
        shutil.copy2(source_script, runtime_py)
        runtime_py.chmod(0o755)
    runtime_pl = target_dir / "multica_vpn_bridge.pl"
    runtime_pl.write_text(render_macos_perl_bridge_script(), encoding="utf-8")
    runtime_pl.chmod(0o755)
    return runtime_py, runtime_pl


def build_launchd_plist_payload(
    *,
    label: str = DEFAULT_LAUNCHD_LABEL,
    python_bin: str | None = None,
    script_path: Path | None = None,
    perl_bridge_path: Path | None = None,
    bind_host: str = DEFAULT_BIND_HOST,
    upstream_host: str = DEFAULT_UPSTREAM_HOST,
    web_port: int = DEFAULT_WEB_PORT,
    api_port: int = DEFAULT_API_PORT,
    log_dir: Path | None = None,
    use_perl_runner: bool = True,
) -> dict[str, Any]:
    resolved_log_dir = Path(log_dir or (Path.home() / ".multica")).expanduser()
    home_dir = str(Path.home())

    if use_perl_runner and Path("/usr/bin/perl").is_file() and perl_bridge_path is not None:
        program_args = [
            "/usr/bin/perl",
            str(Path(perl_bridge_path).expanduser()),
            bind_host,
            upstream_host,
            str(web_port),
            str(api_port),
        ]
    else:
        resolved_python = resolve_python3_binary(python_bin)
        resolved_script = Path(script_path or Path(__file__).resolve()).expanduser()
        program_args = [
            resolved_python,
            str(resolved_script),
            "serve",
            "--bind-host",
            bind_host,
            "--upstream-host",
            upstream_host,
            "--web-port",
            str(web_port),
            "--api-port",
            str(api_port),
        ]

    return {
        "Label": label,
        "ProgramArguments": program_args,
        "EnvironmentVariables": {
            "HOME": home_dir,
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
        },
        "WorkingDirectory": home_dir,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "StandardOutPath": str(resolved_log_dir / "vpn-bridge.out.log"),
        "StandardErrorPath": str(resolved_log_dir / "vpn-bridge.err.log"),
    }


def detect_openvpn_utun_interfaces(
    *,
    ifconfig_output: str | None = None,
    vpn_subnets: Sequence[str] = DEFAULT_VPN_SUBNETS,
) -> list[dict[str, str]]:
    if ifconfig_output is None:
        proc = subprocess.run(["ifconfig"], capture_output=True, text=True, check=False)
        ifconfig_output = proc.stdout or ""

    networks = [ipaddress.ip_network(cidr, strict=False) for cidr in vpn_subnets]
    found: list[dict[str, str]] = []
    current_iface = ""
    header_re = re.compile(r"^([a-zA-Z0-9_]+):\s+flags=")
    inet_re = re.compile(r"^\s*inet\s+(\d+\.\d+\.\d+\.\d+)(?:\s+-->\s+(\d+\.\d+\.\d+\.\d+))?")

    for raw_line in ifconfig_output.splitlines():
        m_hdr = header_re.match(raw_line)
        if m_hdr:
            current_iface = m_hdr.group(1)
            continue
        m_inet = inet_re.match(raw_line)
        if not m_inet or not current_iface:
            continue
        ip_str = m_inet.group(1)
        peer_str = m_inet.group(2) or ""
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for net in networks:
            if ip_obj in net:
                found.append(
                    {
                        "interface": current_iface,
                        "ip": ip_str,
                        "peer": peer_str,
                        "subnet": str(net),
                    }
                )
                break
    return found


def probe_http_endpoint(url: str, *, timeout: float = 4.0) -> dict[str, Any]:
    req = urllib.request.Request(url, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = int(resp.status)
            body = resp.read(256).decode("utf-8", errors="replace")
            return {"ok": 200 <= status < 400, "status": status, "url": url, "snippet": body[:120]}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": int(exc.code), "url": url, "error": f"HTTP {exc.code}"}
    except (OSError, urllib.error.URLError) as exc:
        return {"ok": False, "status": 0, "url": url, "error": str(exc)}


def probe_tcp_port(host: str, port: int, *, timeout: float = 2.5) -> dict[str, Any]:
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency_ms = round((time.monotonic() - start) * 1000.0, 2)
            return {"ok": True, "host": host, "port": port, "latency_ms": latency_ms}
    except OSError as exc:
        return {"ok": False, "host": host, "port": port, "error": str(exc)}


def inspect_bridge_status(
    *,
    label: str = DEFAULT_LAUNCHD_LABEL,
    plist_path: Path | None = None,
    upstream_host: str = DEFAULT_UPSTREAM_HOST,
    web_port: int = DEFAULT_WEB_PORT,
    api_port: int = DEFAULT_API_PORT,
    ifconfig_output: str | None = None,
    skip_network_probes: bool = False,
) -> dict[str, Any]:
    resolved_plist = Path(
        plist_path or (Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")
    ).expanduser()

    uid = os.getuid()
    launchctl_proc = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{label}"],
        capture_output=True,
        text=True,
        check=False,
    )
    launchd_loaded = launchctl_proc.returncode == 0
    pid_match = re.search(r"\bpid\s*=\s*(\d+)", launchctl_proc.stdout or "")
    launchd_pid = int(pid_match.group(1)) if pid_match else None

    vpn_ifaces = detect_openvpn_utun_interfaces(ifconfig_output=ifconfig_output)
    probes: dict[str, Any] = {}
    if not skip_network_probes:
        probes["local_web_127"] = probe_http_endpoint(f"http://127.0.0.1:{web_port}/login")
        probes["local_api_127"] = probe_http_endpoint(f"http://127.0.0.1:{api_port}/health")
        probes["upstream_nas_web"] = probe_http_endpoint(f"http://{upstream_host}:{web_port}/login")
        probes["upstream_nas_api"] = probe_http_endpoint(f"http://{upstream_host}:{api_port}/health")
        for iface in vpn_ifaces:
            vpn_ip = iface["ip"]
            # Note: On macOS point-to-point utun interfaces, self-connecting to 10.9.0.6 hairpins through
            # the remote OpenVPN server (which requires client-to-client); record peer gateway reachability too.
            if iface.get("peer"):
                probes[f"vpn_peer_{iface['peer']}_ping_tcp"] = probe_tcp_port(iface["peer"], web_port, timeout=2.0)
            probes[f"vpn_ip_{vpn_ip}_web"] = probe_http_endpoint(f"http://{vpn_ip}:{web_port}/login", timeout=3.0)
            probes[f"vpn_ip_{vpn_ip}_api"] = probe_http_endpoint(f"http://{vpn_ip}:{api_port}/health", timeout=3.0)

    local_ok = (
        True
        if skip_network_probes
        else bool(
            probes["local_web_127"]["ok"]
            and probes["local_api_127"]["ok"]
            and probes["upstream_nas_web"]["ok"]
            and probes["upstream_nas_api"]["ok"]
        )
    )
    return {
        "ok": bool(resolved_plist.is_file() and launchd_loaded and local_ok),
        "label": label,
        "plist_path": str(resolved_plist),
        "plist_exists": resolved_plist.is_file(),
        "launchd_loaded": launchd_loaded,
        "launchd_pid": launchd_pid,
        "openvpn_interfaces": vpn_ifaces,
        "probes": probes,
    }


def install_launchd_agent(
    *,
    label: str = DEFAULT_LAUNCHD_LABEL,
    plist_path: Path | None = None,
    script_path: Path | None = None,
    python_bin: str | None = None,
    bind_host: str = DEFAULT_BIND_HOST,
    upstream_host: str = DEFAULT_UPSTREAM_HOST,
    web_port: int = DEFAULT_WEB_PORT,
    api_port: int = DEFAULT_API_PORT,
    log_dir: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    resolved_plist = Path(
        plist_path or (Path.home() / "Library" / "LaunchAgents" / f"{label}.plist")
    ).expanduser()
    resolved_log_dir = Path(log_dir or (Path.home() / ".multica")).expanduser()
    source_script = Path(script_path or Path(__file__).resolve()).expanduser()
    resolved_perl_bridge: Path | None = None
    if dry_run:
        resolved_script = source_script
        if script_path is None:
            resolved_perl_bridge = resolved_log_dir / "multica_vpn_bridge.pl"
    elif script_path is not None:
        resolved_script = source_script
    else:
        resolved_script, resolved_perl_bridge = sync_runtime_bridge_copy(source_script, runtime_dir=resolved_log_dir)

    payload = build_launchd_plist_payload(
        label=label,
        python_bin=python_bin,
        script_path=resolved_script,
        perl_bridge_path=resolved_perl_bridge,
        bind_host=bind_host,
        upstream_host=upstream_host,
        web_port=web_port,
        api_port=api_port,
        log_dir=resolved_log_dir,
        use_perl_runner=(resolved_perl_bridge is not None and python_bin is None),
    )
    plist_bytes = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)

    if dry_run:
        return {
            "ok": True,
            "action": "dry_run",
            "label": label,
            "plist_path": str(resolved_plist),
            "payload": payload,
        }

    resolved_plist.parent.mkdir(parents=True, exist_ok=True)
    resolved_log_dir.mkdir(parents=True, exist_ok=True)
    resolved_plist.write_bytes(plist_bytes)

    uid = os.getuid()
    domain = f"gui/{uid}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True, text=True, check=False)
    subprocess.run(["launchctl", "bootout", domain, str(resolved_plist)], capture_output=True, text=True, check=False)

    boot = subprocess.run(
        ["launchctl", "bootstrap", domain, str(resolved_plist)],
        capture_output=True,
        text=True,
        check=False,
    )
    if boot.returncode != 0:
        # Fallback to kickstart if already bootstrapped
        kick = subprocess.run(
            ["launchctl", "kickstart", "-k", f"{domain}/{label}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if kick.returncode != 0:
            detail = (boot.stderr or boot.stdout or kick.stderr or kick.stdout or "").strip()
            raise RuntimeError(f"launchctl bootstrap/kickstart failed for {label}: {detail}")
    else:
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"{domain}/{label}"],
            capture_output=True,
            text=True,
            check=False,
        )

    # Wait briefly for listener startup
    status_report: dict[str, Any] = {}
    for _ in range(12):
        time.sleep(0.25)
        status_report = inspect_bridge_status(
            label=label,
            plist_path=resolved_plist,
            upstream_host=upstream_host,
            web_port=web_port,
            api_port=api_port,
        )
        if status_report.get("ok"):
            break

    status_report["action"] = "installed"
    return status_report


def validate_internal_gateway_bind_hosts(bind_hosts: Sequence[str]) -> list[str]:
    """Enforce zero public exposure: allow only RFC1918/CGNAT internal VPN gateway IPs (never 0.0.0.0 or Azure NIC 172.17.x)."""
    validated: list[str] = []
    azure_eth_net = ipaddress.ip_network("172.17.0.0/16")
    allowed_vpn_nets = (
        ipaddress.ip_network("10.8.0.0/16"),
        ipaddress.ip_network("10.9.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
    )
    for host in bind_hosts:
        cleaned = host.strip()
        if not cleaned:
            continue
        if cleaned in {"0.0.0.0", "::", "*"}:
            raise ValueError(f"Refusing wildcard bind address {cleaned!r}: Azure gateway bridge must bind strictly to internal VPN IPs")
        ip_obj = ipaddress.ip_address(cleaned)
        if ip_obj.is_unspecified or not ip_obj.is_private or ip_obj in azure_eth_net:
            raise ValueError(f"Refusing non-VPN bind address {cleaned!r}: must be an internal OpenVPN gateway IP")
        if not any(ip_obj in net for net in allowed_vpn_nets):
            raise ValueError(f"Bind address {cleaned!r} is outside allowed OpenVPN subnets (10.8.0.0/16, 10.9.0.0/16)")
        validated.append(cleaned)
    if not validated:
        raise ValueError("At least one internal OpenVPN gateway bind host is required")
    return validated


def render_azure_gateway_bridge_script(
    *,
    bind_hosts: Sequence[str] = DEFAULT_AZURE_GATEWAY_HOSTS,
    preferred_client_ip: str = DEFAULT_PREFERRED_MAC_VPN_IP,
    web_port: int = DEFAULT_WEB_PORT,
    api_port: int = DEFAULT_API_PORT,
) -> str:
    checked_hosts = validate_internal_gateway_bind_hosts(bind_hosts)
    hosts_literal = repr(tuple(checked_hosts))
    preferred_literal = repr(preferred_client_ip)

    return textwrap.dedent(
        f'''\
        #!/usr/bin/env python3
        """Azure OpenVPNSingapore Internal-Only Multica Gateway Bridge.

        Security Invariant:
        - Binds ONLY to internal OpenVPN gateway IPs {hosts_literal} (`IP_FREEBIND` enabled so unit
          survives transient `tun0`/`tun1` restarts) and NEVER binds to `0.0.0.0`, `::`, or `eth0` (`172.17.0.4`).
        - Dynamically discovers the active Home Mac mini OpenVPN client IP (`10.9.0.x` / `10.8.0.x`)
          by probing preferred IP {preferred_literal}, OpenVPN status files, and active `10.9.0.2..254` /
          `10.8.0.2..254` peers exposing Multica port `{api_port}`, then forwards `:13000` and `:18080`.
        """

        from __future__ import annotations

        import asyncio
        import glob
        import ipaddress
        import re
        import signal
        import socket
        import time

        ALLOWED_GATEWAY_IPS = {hosts_literal}
        LISTEN_PORTS = ({int(web_port)}, {int(api_port)})
        PREFERRED_CLIENT_IP = {preferred_literal}
        PROBE_PORT = {int(api_port)}
        CACHE_TTL_SEC = 20.0
        IP_FREEBIND = getattr(socket, "IP_FREEBIND", 15)

        _cached_target_ip: str | None = None
        _cached_at: float = 0.0
        _discovery_lock: asyncio.Lock | None = None


        def _assert_internal_vpn_ip(ip_str: str) -> None:
            addr = ipaddress.ip_address(ip_str)
            if addr.is_unspecified or str(addr) in {{"0.0.0.0", "::"}}:
                raise RuntimeError(f"Refusing unspecified bind IP {{ip_str}}")
            if addr in ipaddress.ip_network("172.17.0.0/16"):
                raise RuntimeError(f"Refusing Azure eth0 bind IP {{ip_str}}")
            if not (
                addr in ipaddress.ip_network("10.9.0.0/24")
                or addr in ipaddress.ip_network("10.8.0.0/24")
            ):
                raise RuntimeError(f"Bind IP {{ip_str}} outside OpenVPN tun subnets")


        def _parse_openvpn_status_candidates() -> list[str]:
            candidates: list[str] = []
            patterns = (
                "/var/log/openvpn/*status*.log",
                "/etc/openvpn/*status*.log",
                "/run/openvpn*/*status*",
            )
            ip_re = re.compile(r"\\b(10\\.(?:9|8)\\.0\\.(?:[2-9]|[1-9]\\d|1\\d\\d|2[0-4]\\d|25[0-4]))\\b")
            for pat in patterns:
                for path in sorted(glob.glob(pat)):
                    try:
                        with open(path, "r", encoding="utf-8", errors="replace") as fh:
                            text = fh.read(65536)
                    except OSError:
                        continue
                    for m in ip_re.finditer(text):
                        ip_val = m.group(1)
                        if ip_val not in ALLOWED_GATEWAY_IPS and ip_val not in candidates:
                            candidates.append(ip_val)
            return candidates


        async def _probe_peer(ip_str: str, port: int = PROBE_PORT, timeout: float = 0.75) -> bool:
            try:
                _, writer = await asyncio.wait_for(asyncio.open_connection(ip_str, port), timeout=timeout)
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
                return True
            except (TimeoutError, asyncio.TimeoutError, OSError):
                return False


        async def resolve_mac_mini_vpn_ip(*, force_refresh: bool = False) -> str | None:
            global _cached_target_ip, _cached_at, _discovery_lock
            if _discovery_lock is None:
                _discovery_lock = asyncio.Lock()

            now = time.monotonic()
            if not force_refresh and _cached_target_ip and (now - _cached_at) < CACHE_TTL_SEC:
                return _cached_target_ip

            async with _discovery_lock:
                now = time.monotonic()
                if not force_refresh and _cached_target_ip and (now - _cached_at) < CACHE_TTL_SEC:
                    return _cached_target_ip

                priority_ips: list[str] = []
                for candidate in ([_cached_target_ip, PREFERRED_CLIENT_IP] + _parse_openvpn_status_candidates()):
                    if candidate and candidate not in ALLOWED_GATEWAY_IPS and candidate not in priority_ips:
                        priority_ips.append(candidate)

                for ip_str in priority_ips:
                    if await _probe_peer(ip_str, PROBE_PORT, timeout=1.0):
                        _cached_target_ip = ip_str
                        _cached_at = time.monotonic()
                        return ip_str

                # Fallback subnet scan across 10.9.0.2..254 then 10.8.0.2..254 with bounded concurrency
                sem = asyncio.Semaphore(32)
                scan_ips = [
                    f"{{prefix}}.{{host}}"
                    for prefix in ("10.9.0", "10.8.0")
                    for host in range(2, 255)
                    if f"{{prefix}}.{{host}}" not in priority_ips
                ]

                async def _check(ip_val: str) -> str | None:
                    async with sem:
                        return ip_val if await _probe_peer(ip_val, PROBE_PORT, timeout=0.6) else None

                results = await asyncio.gather(*(_check(ip_val) for ip_val in scan_ips))
                for hit in results:
                    if hit:
                        _cached_target_ip = hit
                        _cached_at = time.monotonic()
                        return hit
                return None


        async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while True:
                    chunk = await reader.read(65536)
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
            except (ConnectionError, asyncio.CancelledError, OSError):
                pass
            finally:
                try:
                    if writer.can_write_eof():
                        writer.write_eof()
                except OSError:
                    pass


        async def _handle_client(
            client_reader: asyncio.StreamReader,
            client_writer: asyncio.StreamWriter,
            target_port: int,
        ) -> None:
            target_ip = await resolve_mac_mini_vpn_ip(force_refresh=False)
            upstream_pair = None
            if target_ip:
                try:
                    upstream_pair = await asyncio.wait_for(
                        asyncio.open_connection(target_ip, target_port),
                        timeout=4.0,
                    )
                except (TimeoutError, asyncio.TimeoutError, OSError):
                    upstream_pair = None

            if upstream_pair is None:
                target_ip = await resolve_mac_mini_vpn_ip(force_refresh=True)
                if target_ip:
                    try:
                        upstream_pair = await asyncio.wait_for(
                            asyncio.open_connection(target_ip, target_port),
                            timeout=4.0,
                        )
                    except (TimeoutError, asyncio.TimeoutError, OSError):
                        upstream_pair = None

            if upstream_pair is None:
                try:
                    client_writer.close()
                    await client_writer.wait_closed()
                except OSError:
                    pass
                return

            up_reader, up_writer = upstream_pair
            t1 = asyncio.create_task(_pipe(client_reader, up_writer))
            t2 = asyncio.create_task(_pipe(up_reader, client_writer))
            try:
                await asyncio.gather(t1, t2)
            finally:
                for w in (up_writer, client_writer):
                    try:
                        w.close()
                    except OSError:
                        pass


        async def main() -> int:
            servers: list[asyncio.AbstractServer] = []
            for gw_ip in ALLOWED_GATEWAY_IPS:
                _assert_internal_vpn_ip(gw_ip)
                for port in LISTEN_PORTS:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    try:
                        sock.setsockopt(socket.IPPROTO_IP, IP_FREEBIND, 1)
                    except OSError:
                        pass
                    sock.setblocking(False)
                    sock.bind((gw_ip, port))

                    async def _cb(
                        r: asyncio.StreamReader,
                        w: asyncio.StreamWriter,
                        p: int = port,
                    ) -> None:
                        await _handle_client(r, w, p)

                    srv = await asyncio.start_server(_cb, sock=sock, backlog=256)
                    servers.append(srv)
                    print(f"[multica-azure-gw-bridge] bound internal {{gw_ip}}:{{port}}", flush=True)

            initial_ip = await resolve_mac_mini_vpn_ip(force_refresh=True)
            print(f"[multica-azure-gw-bridge] initial Mac mini VPN peer: {{initial_ip}}", flush=True)

            stop_event = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, stop_event.set)
                except NotImplementedError:
                    pass

            try:
                await stop_event.wait()
            finally:
                for srv in servers:
                    srv.close()
                for srv in servers:
                    try:
                        await srv.wait_closed()
                    except OSError:
                        pass
            return 0


        if __name__ == "__main__":
            raise SystemExit(asyncio.run(main()))
        '''
    )


def render_azure_systemd_unit(
    *,
    script_install_path: str = "/usr/local/bin/multica-vpn-gateway-bridge.py",
) -> str:
    return textwrap.dedent(
        f"""\
        [Unit]
        Description=Multica Internal-Only OpenVPN Gateway TCP Bridge (10.9.0.1 & 10.8.0.1 -> Mac mini VPN peer)
        After=network-online.target openvpn.service openvpn@server-udp.service openvpn@server-tcp.service
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart=/usr/bin/python3 {script_install_path}
        Restart=always
        RestartSec=2
        NoNewPrivileges=true
        PrivateTmp=true

        [Install]
        WantedBy=multi-user.target
        """
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="multica_vpn_bridge.py",
        description="Option A Multica Internal OpenVPN -> Mac mini -> NAS TCP Bridge",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_serve = sub.add_parser("serve", help="Run async dual-port TCP forwarder (:13000 & :18080 -> NAS)")
    p_serve.add_argument("--bind-host", default=DEFAULT_BIND_HOST)
    p_serve.add_argument("--upstream-host", default=DEFAULT_UPSTREAM_HOST)
    p_serve.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    p_serve.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    p_serve.add_argument("--connect-timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT)
    p_serve.add_argument(
        "--forward",
        action="append",
        dest="extra_forwards",
        default=None,
        help="Custom forward spec [bind_host:]listen_port:upstream_host:upstream_port",
    )

    p_inst = sub.add_parser("install-launchd", help="Install and start com.tianpli.multica.vpn-bridge LaunchAgent")
    p_inst.add_argument("--label", default=DEFAULT_LAUNCHD_LABEL)
    p_inst.add_argument("--plist-path", type=Path, default=None)
    p_inst.add_argument("--script-path", type=Path, default=None)
    p_inst.add_argument("--python-bin", default=None)
    p_inst.add_argument("--bind-host", default=DEFAULT_BIND_HOST)
    p_inst.add_argument("--upstream-host", default=DEFAULT_UPSTREAM_HOST)
    p_inst.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    p_inst.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    p_inst.add_argument("--log-dir", type=Path, default=None)
    p_inst.add_argument("--use-main-repo-script", action="store_true", default=False)
    p_inst.add_argument("--dry-run", action="store_true")
    p_inst.add_argument("--json", action="store_true", default=True)

    p_stat = sub.add_parser("status", help="Check LaunchAgent status, utun OpenVPN IP, and NAS reachability")
    p_stat.add_argument("--label", default=DEFAULT_LAUNCHD_LABEL)
    p_stat.add_argument("--plist-path", type=Path, default=None)
    p_stat.add_argument("--upstream-host", default=DEFAULT_UPSTREAM_HOST)
    p_stat.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    p_stat.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    p_stat.add_argument("--skip-network-probes", action="store_true")
    p_stat.add_argument("--json", action="store_true", default=True)

    p_az = sub.add_parser(
        "generate-azure-bridge-script",
        help="Generate internal-only Azure OpenVPN gateway bridge script or systemd unit",
    )
    p_az.add_argument("--bind-host", action="append", dest="bind_hosts", default=None)
    p_az.add_argument("--preferred-client-ip", default=DEFAULT_PREFERRED_MAC_VPN_IP)
    p_az.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    p_az.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    p_az.add_argument("--emit-systemd-unit", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    ns = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if ns.subcommand == "serve":
        mappings = parse_port_mappings(
            bind_host=ns.bind_host,
            upstream_host=ns.upstream_host,
            web_port=ns.web_port,
            api_port=ns.api_port,
            extra_forwards=ns.extra_forwards,
        )
        return asyncio.run(run_bridge_forever(mappings, connect_timeout=ns.connect_timeout))

    if ns.subcommand == "install-launchd":
        chosen_script = (
            default_canonical_script_path()
            if ns.use_main_repo_script and ns.script_path is None
            else ns.script_path
        )
        report = install_launchd_agent(
            label=ns.label,
            plist_path=ns.plist_path,
            script_path=chosen_script,
            python_bin=ns.python_bin,
            bind_host=ns.bind_host,
            upstream_host=ns.upstream_host,
            web_port=ns.web_port,
            api_port=ns.api_port,
            log_dir=ns.log_dir,
            dry_run=ns.dry_run,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    if ns.subcommand == "status":
        report = inspect_bridge_status(
            label=ns.label,
            plist_path=ns.plist_path,
            upstream_host=ns.upstream_host,
            web_port=ns.web_port,
            api_port=ns.api_port,
            skip_network_probes=ns.skip_network_probes,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    if ns.subcommand == "generate-azure-bridge-script":
        hosts = tuple(ns.bind_hosts) if ns.bind_hosts else DEFAULT_AZURE_GATEWAY_HOSTS
        if ns.emit_systemd_unit:
            sys.stdout.write(render_azure_systemd_unit())
        else:
            sys.stdout.write(
                render_azure_gateway_bridge_script(
                    bind_hosts=hosts,
                    preferred_client_ip=ns.preferred_client_ip,
                    web_port=ns.web_port,
                    api_port=ns.api_port,
                )
            )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
