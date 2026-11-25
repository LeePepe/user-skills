from __future__ import annotations

from ._imports import *

from .constants_and_paths import _run_command

def _quota_protection_payload() -> dict[str, Any]:
    return {
        "active": True,
        "caller_bot": "grok",
        "reason": GROK_QUOTA_PROTECTION_REASON,
        "supervision_disabled": True,
        "routine_disabled": True,
    }

def _record_has_grok_model(record: dict[str, Any]) -> bool:
    for field in ("model", "resolved_model", "agent_model", "logical_group", "agent"):
        if "grok" in _text(record.get(field)).lower():
            return True
    return False

def is_grok_bot_caller(
    args: Any = None,
    *,
    caller_bot: str = "",
    no_supervision: bool = False,
    no_routine: bool = False,
    agent_records: Iterable[dict[str, Any]] | None = None,
    herdr_bin: str | None = None,
    runner: CommandRunner = _run_command,
    probe_herdr: bool = True,
) -> bool:
    """Detect whether the caller is Grok Bot or explicitly requested no supervision/routine polling."""
    if (
        no_supervision
        or no_routine
        or bool(getattr(args, "no_supervision", False))
        or bool(getattr(args, "no_routine", False))
    ):
        return True
    effective_caller_bot = _text(caller_bot or getattr(args, "caller_bot", "")).lower()
    if "grok" in effective_caller_bot:
        return True
    grok_bot_env = _text(os.environ.get("GROK_BOT")).lower()
    if grok_bot_env in {"1", "true", "yes"} or "grok" in grok_bot_env:
        return True
    for env_var in GROK_ENV_MODEL_VARS:
        if "grok" in _text(os.environ.get(env_var)).lower():
            return True
    caller_pane = _text(os.environ.get("HERDR_PANE_ID"))
    if caller_pane:
        records: list[dict[str, Any]] = []
        if agent_records is not None:
            records = [dict(item) for item in agent_records if isinstance(item, dict)]
        elif probe_herdr and not bool(getattr(args, "no_herdr", False)):
            binary = _text(
                herdr_bin
                or getattr(args, "herdr_bin", "")
                or os.environ.get("HERDR_BIN")
                or shutil.which("herdr"),
                "herdr",
            )
            try:
                res = runner([binary, "agent", "list"], timeout=2.0)
                if getattr(res, "returncode", 1) == 0:
                    records = _records(_parse_json_output(getattr(res, "stdout", "")))
            except (OSError, subprocess.SubprocessError):
                records = []
        for rec in records:
            pane_val = _text(rec.get("pane_id") or rec.get("paneId") or rec.get("id"))
            if pane_val == caller_pane and _record_has_grok_model(rec):
                return True
    return False

def _resolve_quota_protection(
    args: Any = None,
    *,
    plan: dict[str, Any] | None = None,
    apply_receipt: dict[str, Any] | None = None,
    caller_bot: str = "",
    no_supervision: bool = False,
    no_routine: bool = False,
    agent_records: Iterable[dict[str, Any]] | None = None,
    herdr_bin: str | None = None,
    runner: CommandRunner = _run_command,
    probe_herdr: bool = True,
) -> dict[str, Any]:
    plan_obj = plan if isinstance(plan, dict) else {}
    receipt_obj = apply_receipt if isinstance(apply_receipt, dict) else {}
    active = (
        _text(plan_obj.get("quota_protection_mode")) == GROK_QUOTA_PROTECTION_MODE
        or bool((plan_obj.get("quota_protection") or {}).get("active"))
        or _text(receipt_obj.get("quota_protection_mode")) == GROK_QUOTA_PROTECTION_MODE
        or bool((receipt_obj.get("quota_protection") or {}).get("active"))
        or is_grok_bot_caller(
            args,
            caller_bot=caller_bot,
            no_supervision=no_supervision,
            no_routine=no_routine,
            agent_records=agent_records,
            herdr_bin=herdr_bin,
            runner=runner,
            probe_herdr=probe_herdr,
        )
    )
    if active:
        return _quota_protection_payload()
    return {
        "active": False,
        "caller_bot": _text(caller_bot or getattr(args, "caller_bot", "")),
        "reason": "",
        "supervision_disabled": False,
        "routine_disabled": False,
    }
