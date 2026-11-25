from .common import *
from .data_loaders import *
from .trace_builders import *
from .collection import *

# ---------------------------------------------------------------------------
# Exporters (Phoenix OTLP HTTP + Langfuse Batch Ingestion)
# ---------------------------------------------------------------------------


def load_obs_config(config_path: Path = DEFAULT_OBS_CONFIG) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if config_path.is_file():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
    phoenix_cfg = cfg.get("phoenix") if isinstance(cfg.get("phoenix"), dict) else {}
    langfuse_cfg = cfg.get("langfuse") if isinstance(cfg.get("langfuse"), dict) else {}
    return {
        "phoenix_otlp_url": os.environ.get(
            "PHOENIX_OTLP_HTTP_URL",
            str(phoenix_cfg.get("otlp_http_endpoint") or "http://127.0.0.1:6006/v1/traces"),
        ),
        "phoenix_project": os.environ.get(
            "PHOENIX_PROJECT_NAME",
            str(phoenix_cfg.get("project_name") or "agent-observability"),
        ),
        "langfuse_url": os.environ.get(
            "LANGFUSE_HOST",
            str(langfuse_cfg.get("local_url") or "http://127.0.0.1:13100"),
        ).rstrip("/"),
        "langfuse_public_key": os.environ.get("LANGFUSE_PUBLIC_KEY", str(langfuse_cfg.get("public_key") or "")),
        "langfuse_secret_key": os.environ.get("LANGFUSE_SECRET_KEY", str(langfuse_cfg.get("secret_key") or "")),
    }


def export_to_phoenix(
    traces: list[TraceRecord],
    *,
    otlp_url: str,
    project_name: str,
    batch_size: int = 20,
    timeout: int = 25,
) -> dict[str, Any]:
    sent_traces = 0
    sent_spans = 0
    batches = 0
    for idx in range(0, len(traces), batch_size):
        chunk = traces[idx : idx + batch_size]
        payload = encode_otlp_traces_protobuf(project_name, chunk)
        req = Request(
            otlp_url,
            data=payload,
            headers={"Content-Type": "application/x-protobuf"},
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Phoenix OTLP HTTP {resp.status}")
            resp.read()
        batches += 1
        sent_traces += len(chunk)
        sent_spans += sum(len(t.spans) for t in chunk)
    return {
        "ok": True,
        "endpoint": otlp_url,
        "project": project_name,
        "batches": batches,
        "traces_exported": sent_traces,
        "spans_exported": sent_spans,
    }


def export_to_langfuse(
    traces: list[TraceRecord],
    *,
    base_url: str,
    public_key: str,
    secret_key: str,
    event_batch_size: int = 80,
    timeout: int = 25,
) -> dict[str, Any]:
    if not public_key or not secret_key:
        raise ValueError("Langfuse credentials missing; configure ~/.local/share/agent-observability/config.json")
    all_events: list[dict[str, Any]] = []
    for tr in traces:
        all_events.extend(build_langfuse_ingestion_events(tr))

    auth_token = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
    ingestion_url = f"{base_url.rstrip('/')}/api/public/ingestion"
    successes = 0
    errors: list[Any] = []
    batches = 0

    for idx in range(0, len(all_events), event_batch_size):
        chunk = all_events[idx : idx + event_batch_size]
        body = json.dumps({"batch": chunk}, ensure_ascii=False).encode("utf-8")
        req = Request(
            ingestion_url,
            data=body,
            headers={
                "Authorization": f"Basic {auth_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            successes += len(parsed.get("successes", chunk))
            if parsed.get("errors"):
                errors.extend(parsed["errors"])
        batches += 1

    return {
        "ok": len(errors) == 0,
        "endpoint": ingestion_url,
        "batches": batches,
        "traces_exported": len(traces),
        "events_submitted": len(all_events),
        "events_succeeded": successes,
        "errors": errors[:10],
    }


def summarize_traces(goal_traces: list[TraceRecord], session_traces: list[TraceRecord]) -> dict[str, Any]:
    all_traces = goal_traces + session_traces
    cat_counts = {c: 0 for c in ("root", *SPAN_CATEGORIES)}
    complete_goals = 0
    complete_sessions = 0
    for tr in goal_traces:
        cats = {sp.category for sp in tr.spans if sp.category != "root"}
        if all(c in cats for c in SPAN_CATEGORIES):
            complete_goals += 1
    for tr in session_traces:
        cats = {sp.category for sp in tr.spans if sp.category != "root"}
        if all(c in cats for c in SPAN_CATEGORIES):
            complete_sessions += 1
    for tr in all_traces:
        for sp in tr.spans:
            cat_counts[sp.category] = cat_counts.get(sp.category, 0) + 1
    return {
        "goal_traces": len(goal_traces),
        "goal_traces_complete_5_categories": complete_goals,
        "session_traces": len(session_traces),
        "session_traces_complete_5_categories": complete_sessions,
        "total_traces": len(all_traces),
        "total_spans": sum(len(t.spans) for t in all_traces),
        "span_categories": cat_counts,
        "sample_goals": [t.entity_id for t in goal_traces[:8]],
        "sample_sessions": [t.entity_id for t in session_traces[:5]],
    }
