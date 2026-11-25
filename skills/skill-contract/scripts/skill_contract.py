#!/usr/bin/env python3
"""Small CLI facade for the skill-contract implementation modules.

The facade deliberately keeps the historical public names (audit_package,
sink_rule, case-card, mental-model, regex-gate) available to importers.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from skill_runtime import (  # noqa: E402
    SelfDisclosingArgumentParser,
    SkillVersionInfo,
    compact_json_dumps,
    is_valid_skill_slug,
    read_copilot_sessions,
    redact_payload,
    redact_text,
    resolve_skill_version as runtime_resolve_skill_version,
    verify_commit_exists,
)
from contract_core import core_common as _common  # noqa: E402
from contract_core import core_facts as _facts  # noqa: E402
from contract_core.core_common import *  # noqa: F401,F403,E402
from contract_core.core_eval import *  # noqa: F401,F403,E402
from contract_core.core_facts import *  # noqa: F401,F403,E402
from contract_core.core_routing import *  # noqa: F401,F403,E402
from contract_core.core_cli import *  # noqa: F401,F403,E402


# Keep monkey-patching _discover_packages on this historical module effective.
def _discover_packages(skill: str | None = None, repo=None):
    return _common._discover_packages(skill=skill, repo=repo)


def audit(args):
    return _facts.audit(args, discover_packages=_discover_packages)


# Re-exported by design: callers import audit_package, parser, audit, and friends.
__all__ = [name for name in globals() if not name.startswith("_")]


if __name__ == "__main__":
    raise SystemExit(main())
