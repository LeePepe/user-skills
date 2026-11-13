#!/usr/bin/env python3
"""Synchronize team-lead handoffs with a Linear issue.

The script deliberately uses only the Python standard library.  It is safe to
run from queue hooks because ``sync-ledger --quiet`` is fail-open.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


API_URL = "https://api.linear.app/graphql"
OAUTH_TOKEN_URL = "https://api.linear.app/oauth/token"
OAUTH_AUTHORIZE_URL = "https://linear.app/oauth/authorize"
ISSUE_HISTORY_FIELDS = (
    "history { nodes { id createdAt actor { id name displayName app } "
    "botActor { id name type } fromState { id name type } toState { id name type } "
    "fromTitle toTitle fromPriority toPriority updatedDescription } }"
)
MARKER_START = "<!-- GTT-LINEAR-SYNC:START -->"
MARKER_END = "<!-- GTT-LINEAR-SYNC:END -->"
KEY_RE = re.compile(r"^\s*(?:export\s+)?LINEAR_API_KEY\s*=\s*(['\"]?)([^'\"#\s]+)\1\s*$", re.MULTILINE)
ISSUE_RE = re.compile(r"(?mi)^\s*-\s*\**(?:Linear(?:\s+Issue)?)\**\s*:\s*([A-Z][A-Z0-9]*-\d+)(?:\s+\(([^)]+)\))?")
EVENT_RE = re.compile(r"(?m)^\s*-\s+[^|]+\s+\|\s+(.*)$")
PR_RE = re.compile(r"(?:github\.com/.+/(?:pull|pulls)/\d+|PR(?:\s*#|=)\s*\d+)", re.IGNORECASE)
DEFAULT_MODEL_PRICES = {
    "gpt-5.6-luna": {"input_per_mtok": 1.25, "output_per_mtok": 10.0, "cache_read_per_mtok": 0.125, "cache_write_per_mtok": 1.25},
    "gpt-5.6-sol": {"input_per_mtok": 1.25, "output_per_mtok": 10.0, "cache_read_per_mtok": 0.125, "cache_write_per_mtok": 1.25},
    "gemini-4-argon": {"input_per_mtok": 1.25, "output_per_mtok": 5.0, "cache_read_per_mtok": 0.125, "cache_write_per_mtok": 1.25},
    "mai-code-2-flash": {"input_per_mtok": 0.30, "output_per_mtok": 2.50, "cache_read_per_mtok": 0.03, "cache_write_per_mtok": 0.30},
    "default": {"input_per_mtok": 1.0, "output_per_mtok": 4.0, "cache_read_per_mtok": 0.1, "cache_write_per_mtok": 1.0},
}


class LinearSyncError(RuntimeError):
    """Expected, user-facing Linear synchronization failure."""


def mask_value(value: str) -> str:
    return "****" if len(value) <= 4 else f"{value[:2]}****{value[-2:]}"


def _credential_path() -> Path:
    return Path(os.environ.get("GTT_LINEAR_CREDENTIALS_FILE", "~/.config/linear/credentials.json")).expanduser()


def _clip_credential_path() -> Path:
    return Path(os.environ.get("CLIP_HOME", "~/.clip")).expanduser() / "linear" / "credentials.json"


def _has_symlink_component(path: Path) -> bool:
    current = path
    while True:
        if current.is_symlink():
            return True
        if current == current.parent:
            return False
        current = current.parent


def load_credential_info() -> tuple[str | None, str | None, Path | None]:
    for path in (_credential_path(), _clip_credential_path()):
        try:
            if _has_symlink_component(path):
                raise LinearSyncError(f"Refusing symlinked credential path: {path}")
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                value = str(data.get("headerValue") or data.get("key") or "").strip()
                if value:
                    return value, "credentials", path
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    for path in (Path("~/.zshenv").expanduser(), Path("~/.zshrc").expanduser()):
        value = _read_key_file(path)
        if value:
            return value, str(path), path
    return None, None, None


def _credential_record() -> dict[str, Any]:
    path = _credential_path()
    try:
        if _has_symlink_component(path):
            raise LinearSyncError(f"Refusing symlinked credential path: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _oauth_app_enabled() -> bool:
    record = _credential_record()
    header_value = str(record.get("headerValue") or "")
    return (
        str(record.get("actor") or "").casefold() == "app"
        or (
            header_value.startswith("Bearer ")
            and bool(header_value[len("Bearer "):].strip())
        )
    )


def _atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    if _has_symlink_component(path):
        raise LinearSyncError(f"Refusing symlinked credential path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.chmod(mode)
    os.replace(temp, path)
    path.chmod(mode)


def save_credentials(
    key: str,
    header_name: str = "Authorization",
    actor: str | None = None,
    *,
    refresh_token: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    expires_at: str | None = None,
) -> Path:
    existing = _credential_record()
    payload: dict[str, Any] = {
        "type": "header", "headerName": header_name, "headerValue": key,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    effective_actor = actor if actor is not None else existing.get("actor")
    if effective_actor:
        payload["actor"] = effective_actor
    for field, val in (
        ("refreshToken", refresh_token if refresh_token is not None else existing.get("refreshToken")),
        ("clientId", client_id if client_id is not None else existing.get("clientId")),
        ("clientSecret", client_secret if client_secret is not None else existing.get("clientSecret")),
        ("expiresAt", expires_at if expires_at is not None else existing.get("expiresAt")),
    ):
        if val:
            payload[field] = val
    path = _credential_path()
    _atomic_write_text(path, json.dumps(payload) + "\n")
    return path


def refresh_oauth_credentials() -> str | None:
    record = _credential_record()
    refresh_token = str(record.get("refreshToken") or "").strip()
    client_id = str(record.get("clientId") or "").strip()
    client_secret = str(record.get("clientSecret") or "").strip()
    if not (refresh_token and client_id and client_secret):
        return None
    body = urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode("utf-8")
    req = Request(
        OAUTH_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=15) as resp:
            tok = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    access_token = str(tok.get("access_token") or "").strip()
    if not access_token:
        return None
    header_value = f"Bearer {access_token}"
    new_refresh = str(tok.get("refresh_token") or refresh_token).strip()
    expires_in = tok.get("expires_in")
    expires_at = (
        datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + int(expires_in), tz=timezone.utc).isoformat()
        if isinstance(expires_in, (int, float))
        else None
    )
    save_credentials(
        header_value,
        str(record.get("headerName") or "Authorization"),
        actor=str(record.get("actor") or "app"),
        refresh_token=new_refresh,
        client_id=client_id,
        client_secret=client_secret,
        expires_at=expires_at,
    )
    return header_value


def resolve_mode(
    explicit_mode: str | None,
    *,
    subcommand: str = "",
    source: str = "",
    handoff_dir: Path | None = None,
    quiet: bool = False,
) -> tuple[str, str | None]:
    value = (explicit_mode or "").strip().lower()
    if value:
        if value not in {"off", "observe", "auto"}:
            raise LinearSyncError(f"invalid mode: {value}")
        return value, None
    value = os.environ.get("GTT_LINEAR_MODE", "").strip().lower()
    if value:
        if value not in {"off", "observe", "auto"}:
            raise LinearSyncError(f"invalid GTT_LINEAR_MODE: {value}")
        return value, None
    if handoff_dir and _is_isolated_handoff_dir(handoff_dir) and quiet and os.environ.get("GTT_LINEAR_SYNC") != "1":
        return "off", "isolated_handoff_dir"
    if os.environ.get("GTT_LINEAR_SYNC") == "0":
        return "off", "env_disabled"
    if subcommand == "idea" and source.strip().lower() in {"proactive", "retro", "retro-loop", "telemetry", "cron"}:
        return "observe", None
    return "auto", None


def _is_isolated_handoff_dir(handoff_dir: Path) -> bool:
    resolved = handoff_dir.expanduser().resolve()
    isolated_roots = (
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var/folders"),
        Path("/private/var/folders"),
    )
    if any(resolved == root or root in resolved.parents for root in isolated_roots):
        return True
    if any(part.lower().startswith(("tmp", ".tmp")) for part in resolved.parts):
        return True
    return resolved.parent.name != "handoff"


def _read_key_file(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = KEY_RE.search(text)
    return match.group(2) if match else None


def load_api_key() -> str | None:
    """Read credentials from env, protected files, then shell startup files."""
    value = os.environ.get("LINEAR_API_KEY", "").strip()
    if value:
        return value
    return load_credential_info()[0]


class GraphQLClient:
    def __init__(
        self,
        api_key: str,
        transport: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.transport = transport or self._request
        self.api_key = api_key

    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self.transport(query, variables or {})
        if not isinstance(payload, dict):
            raise LinearSyncError("Linear returned an invalid response")
        errors = payload.get("errors")
        if errors:
            message = "; ".join(str(item.get("message", item)) for item in errors)
            raise LinearSyncError(f"Linear GraphQL error: {message}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LinearSyncError("Linear response did not contain data")
        return data

    def _request(self, query: str, variables: dict[str, Any], *, _retried: bool = False) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = Request(
            API_URL,
            data=body,
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 401 and not _retried:
                refreshed = refresh_oauth_credentials()
                if refreshed:
                    self.api_key = refreshed
                    return self._request(query, variables, _retried=True)
            detail = ""
            try:
                body = json.loads(exc.read().decode("utf-8"))
                detail = "; ".join(
                    str(item.get("message", item)) for item in body.get("errors", [])
                )
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            suffix = f": {detail}" if detail else ""
            raise LinearSyncError(f"Linear request failed: HTTP {exc.code}{suffix}") from exc
        except (URLError, TimeoutError, ValueError) as exc:
            raise LinearSyncError(f"Linear request failed: {exc}") from exc

    def team_metadata(self, team_key: str) -> dict[str, Any]:
        data = self.execute(
            """
            query TeamMetadata($key: String!) {
              teams(filter: {key: {eq: $key}}) {
                nodes {
                  id key name
                  states { nodes { id name type } }
                  labels { nodes { id name } }
                  projects { nodes { id name } }
                }
              }
            }
            """,
            {"key": team_key},
        )
        nodes = data.get("teams", {}).get("nodes", [])
        if not nodes:
            raise LinearSyncError(f"Linear team '{team_key}' was not found")
        return nodes[0]

    def team(self, key: str) -> dict[str, Any]:
        return self.team_metadata(key)

    def issue(self, identifier: str) -> dict[str, Any]:
        fields = f"""
          id identifier url title description priority updatedAt
          state {{ id name type }}
          team {{ id key name }}
          project {{ id name }}
          projectMilestone {{ id name }}
          parent {{ id identifier title }}
          labels {{ nodes {{ id name }} }}
          comments {{ nodes {{ id body }} }}
          {ISSUE_HISTORY_FIELDS}
        """
        direct_query = f"""
            query IssueByIdentifier($id: String!) {{
              issue(id: $id) {{
                {fields}
              }}
            }}
            """
        try:
            data = self.execute(direct_query, {"id": identifier})
            issue = data.get("issue")
        except LinearSyncError as direct_error:
            issue = None
            direct_failure = direct_error
        else:
            direct_failure = None
        if not isinstance(issue, dict):
            fallback_query = f"""
                query IssueByIdentifierFallback($identifier: String!) {{
                  issues(filter: {{identifier: {{eq: $identifier}}}}, first: 1) {{
                    nodes {{
                      {fields}
                    }}
                  }}
                }}
                """
            try:
                fallback_data = self.execute(fallback_query, {"identifier": identifier})
            except LinearSyncError:
                if direct_failure is not None:
                    raise direct_failure
                raise
            nodes = fallback_data.get("issues", {}).get("nodes", [])
            issue = next(
                (node for node in nodes if str(node.get("identifier")) == identifier),
                None,
            )
        if not issue:
            raise LinearSyncError(f"Linear issue '{identifier}' was not found")
        return issue

    def create_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        state_id: str | None = None,
        label_ids: list[str] | None = None,
        project_id: str | None = None,
        project_milestone_id: str | None = None,
        parent_id: str | None = None,
        create_as_user: str | None = None,
    ) -> dict[str, Any]:
        input_data: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "description": description,
        }
        if state_id:
            input_data["stateId"] = state_id
        if label_ids:
            input_data["labelIds"] = label_ids
        if project_id:
            input_data["projectId"] = project_id
        if project_milestone_id:
            input_data["projectMilestoneId"] = project_milestone_id
        if parent_id:
            input_data["parentId"] = parent_id
        if create_as_user and _oauth_app_enabled():
            input_data["createAsUser"] = create_as_user
        data = self.execute(
            f"""
            mutation CreateIssue($input: IssueCreateInput!) {{
              issueCreate(input: $input) {{
                success
                issue {{
                  id identifier url title description priority updatedAt
                  state {{ id name type }}
                  team {{ id key name }}
                  project {{ id name }}
                  projectMilestone {{ id name }}
                  parent {{ id identifier title }}
                  labels {{ nodes {{ id name }} }}
                  comments {{ nodes {{ id body }} }}
                  {ISSUE_HISTORY_FIELDS}
                }}
              }}
            }}
            """,
            {"input": input_data},
        )
        result = data.get("issueCreate", {})
        if not result.get("success") or not result.get("issue"):
            raise LinearSyncError("Linear did not create the issue")
        return result["issue"]

    def update_issue(
        self,
        issue_id: str,
        *,
        description: str | None = None,
        state_id: str | None = None,
        label_ids: list[str] | None = None,
        project_id: str | None = None,
        project_milestone_id: str | None = None,
        parent_id: str | None = None,
        clear_parent: bool = False,
        title: str | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        input_data: dict[str, Any] = {}
        if description is not None:
            input_data["description"] = description
        if title is not None:
            input_data["title"] = title
        if state_id is not None:
            input_data["stateId"] = state_id
        if label_ids is not None:
            input_data["labelIds"] = label_ids
        if project_id is not None:
            input_data["projectId"] = project_id
        if project_milestone_id is not None:
            input_data["projectMilestoneId"] = project_milestone_id
        if parent_id is not None or clear_parent:
            input_data["parentId"] = parent_id
        if priority is not None:
            input_data["priority"] = priority
        data = self.execute(
            f"""
            mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {{
              issueUpdate(id: $id, input: $input) {{
                success
                issue {{
                  id identifier url title description priority updatedAt
                  state {{ id name type }}
                  team {{ id key name }}
                  project {{ id name }}
                  projectMilestone {{ id name }}
                  parent {{ id identifier title }}
                  labels {{ nodes {{ id name }} }}
                  comments {{ nodes {{ id body }} }}
                  {ISSUE_HISTORY_FIELDS}
                }}
              }}
            }}
            """,
            {"id": issue_id, "input": input_data},
        )
        result = data.get("issueUpdate", {})
        if not result.get("success") or not result.get("issue"):
            raise LinearSyncError("Linear did not update the issue")
        return result["issue"]

    def create_comment(self, issue_id: str, body: str, create_as_user: str | None = None) -> None:
        input_data: dict[str, Any] = {"issueId": issue_id, "body": body}
        if create_as_user and _oauth_app_enabled():
            input_data["createAsUser"] = create_as_user
        data = self.execute(
            """
            mutation CreateComment($input: CommentCreateInput!) {
              commentCreate(input: $input) { success comment { id } }
            }
            """,
            {"input": input_data},
        )
        if not data.get("commentCreate", {}).get("success"):
            raise LinearSyncError("Linear did not create the comment")

    def list_issues(
        self, team_key: str, *, state: str = "", project: str = "",
        query: str = "", limit: int = 25,
    ) -> list[dict[str, Any]]:
        data = self.execute(
            """
            query ListIssues($team: String!, $first: Int!) {
              issues(filter: {team: {key: {eq: $team}}}, first: $first, orderBy: updatedAt) {
                nodes {
                  id identifier title url description priority updatedAt
                  state { id name type } project { id name }
                  labels { nodes { id name } }
                }
              }
            }
            """,
            {"team": team_key, "first": limit},
        )
        issues = data.get("issues", {}).get("nodes", [])
        def matches(issue: dict[str, Any]) -> bool:
            state_value = issue.get("state") or {}
            project_value = issue.get("project") or {}
            return (
                (not state or state.casefold() in {str(state_value.get("name", "")).casefold(), str(state_value.get("type", "")).casefold()})
                and (not project or project.casefold() == str(project_value.get("name", "")).casefold())
                and (not query or query.casefold() in f"{issue.get('identifier', '')} {issue.get('title', '')} {issue.get('description', '')}".casefold())
            )
        return [issue for issue in issues if isinstance(issue, dict) and matches(issue)]

    def find_issue_by_title(self, team_key: str, title: str) -> dict[str, Any] | None:
        data = self.execute(
            """
            query IssueByTitle($team: String!, $title: String!) {
              issues(filter: {team: {key: {eq: $team}}, title: {eq: $title}}, first: 20) {
                nodes {
                  id identifier url title parent { id identifier title }
                  project { id name } projectMilestone { id name }
                  state { id name type } priority labels { nodes { id name } }
                }
              }
            }
            """,
            {"team": team_key, "title": title},
        )
        nodes = data.get("issues", {}).get("nodes", [])
        return nodes[0] if nodes else None

    def project_milestones(self, project_id: str) -> list[dict[str, Any]]:
        data = self.execute(
            """
            query ProjectMilestones($id: String!) {
              project(id: $id) { projectMilestones { nodes { id name description targetDate } } }
            }
            """,
            {"id": project_id},
        )
        return data.get("project", {}).get("projectMilestones", {}).get("nodes", [])

    def find_issue_by_dedup_marker(self, team_id: str, marker: str) -> dict[str, Any] | None:
        data = self.execute(
            """
            query IssueByDedupMarker($teamId: ID!, $marker: String!) {
              issues(filter: {team: {id: {eq: $teamId}}, description: {contains: $marker}}, first: 50) {
                nodes {
                  id identifier url state { id name type } project { name }
                }
              }
            }
            """,
            {"teamId": team_id, "marker": marker},
        )
        for issue in data.get("issues", {}).get("nodes", []):
            state = issue.get("state") or {}
            if str(state.get("type", "")).casefold() not in {"completed", "canceled", "cancelled"}:
                return issue
        return None


def _state(team: dict[str, Any], name: str, fallback: str | None = None) -> dict[str, Any] | None:
    states = team.get("states", {}).get("nodes", [])
    wanted = name.casefold()
    for state in states:
        if str(state.get("name", "")).casefold() == wanted:
            return state
    if fallback:
        return _state(team, fallback)
    return None


def _label(team: dict[str, Any], name: str) -> dict[str, Any] | None:
    wanted = name.casefold()
    for label in team.get("labels", {}).get("nodes", []):
        if str(label.get("name", "")).casefold() == wanted:
            return label
    return None


def _infer_project_name(explicit_project: str | None, text: str, repo_text: str = "") -> str:
    if explicit_project and explicit_project.strip():
        return explicit_project.strip()
    ledger_project = re.search(r"(?mi)^\s*-\s*Project:\s*(\S.*?)\s*$", text)
    if ledger_project:
        return ledger_project.group(1).strip()
    cleaned_text = re.sub(r"(?i)(?:contract\s*=\s*|合同(?:版本|sha)\s*:\s*)team-lead\S*", "", text)
    combined = f"{cleaned_text} {repo_text}".casefold()
    if any(keyword in combined for keyword in (
        "bing", "sapphire", "flightbook", "oneauth", "serp", "fastlane",
        "dsat", "cer", "msn", "san sa",
    )):
        return "Bing"
    if any(keyword in combined for keyword in (
        "user-skills", "workflow", "team-lead", "linear-board", "bot-tl",
        "retro-loop", "story-craft", "herdr", "launchd", "cron", "6dq",
        "shared-ci", "skill",
    )):
        return "Agent Workflow"
    if any(keyword in combined for keyword in (
        "vitalstride", "voxpocket", "aidash", "localis", "financial",
        "fin-", "workout", "activeworkout", "webiq", "strata",
    )):
        return "Personal project"
    return "Agent Workflow"


def _project_id(team: dict[str, Any], project_name: str | None) -> str | None:
    if not project_name:
        return None
    wanted = project_name.casefold()
    for project in (team.get("projects") or {}).get("nodes") or []:
        if str(project.get("name", "")).casefold() == wanted:
            project_id = project.get("id")
            return str(project_id) if project_id else None
    return None


DOMAIN_MAP_PATH = Path(__file__).resolve().parents[2] / "personal-m2-assistant" / "references" / "m2-domain-map.json"
DOMAIN_PROJECT_NAMES = {
    "work-business": "Work Business",
    "user-skills": "user-skills",
    "personal-core-gateways": "Personal Core & Gateways",
    "personal-products-home": "Personal Products & Home",
}
EXTRA_TOPIC = {"topic_id": "financial", "group": "personal-products-home", "title": "Financial"}
ROOT_NAME_OVERRIDES = {
    "sapphire-china-bugs": "China Release",
    "sapphire-notification": "Sapphire Notification",
    "sapphire-smartbanner": "Sapphire SmartBanner",
}


def _domain_topics() -> list[dict[str, str]]:
    try:
        data = json.loads(DOMAIN_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    topics: list[dict[str, str]] = []
    for group in data.get("groups", []) if isinstance(data, dict) else []:
        if not isinstance(group, dict):
            continue
        for topic in group.get("topics", []) if isinstance(group.get("topics"), list) else []:
            if isinstance(topic, dict) and topic.get("topic_id"):
                topics.append({
                    "topic_id": str(topic["topic_id"]),
                    "group": str(topic.get("group") or ""),
                    "title": str(topic.get("title") or topic["topic_id"]),
                    "keywords": " ".join(str(x) for x in topic.get("keywords", [])),
                    "aliases": " ".join(str(x) for x in topic.get("aliases", [])),
                })
    topics.append(EXTRA_TOPIC)
    return topics


def _goal_topic(goal_name: str, ledger_text: str, handoff_dir: Path | None = None) -> dict[str, str] | None:
    goal_key = str(goal_name or "").strip().casefold()
    topics = {topic["topic_id"]: topic for topic in _domain_topics()}
    exact = {
        "project-root-tracker": "skills-workflow",
        "ios-snapshot-skill": "skills-repo-ci",
        "devsync1030": "sapphire-china-bugs",
    }
    if goal_key in exact:
        return topics.get(exact[goal_key])
    text = f"{goal_name} {ledger_text} {handoff_dir or ''}".casefold()
    explicit = (
        ("sapphire-china-bugs", ("devsync", "china release", "bing cn", "bingchina", "ab114", "manageapps")),
        ("sapphire-notification", ("notification", "push", "mai notification")),
        ("sapphire-smartbanner", ("smartbanner", "universal link", "universal_link", "upsell")),
        ("aidash-aidata", ("aidash", "aidata")),
        ("dsh-plugins", ("dsh", "deepseek harness", "session sink", "omni hub")),
        ("gateway-herdr-jev", ("herdr", "magpie", "falcon", "gateway", "copilot-bg-watch")),
        ("financial", ("financial", "fin-", "fin product")),
        ("voxpocket", ("voxpocket", "vox-pocket")),
        ("vitalstride", ("vitalstride", "workout")),
        ("helix", ("helix",)),
        ("davici", ("davici",)),
        ("nas-smart-home", ("nas", "home assistant", "node-red", "mijia")),
        ("learning-and-blog", ("learning", "blog", "learn-", "knowledge vault")),
        ("skills-design", ("designer", "design", "visual", "studio")),
        ("skills-research-knowledge", ("research", "knowledge", "digest")),
        ("skills-retro-story", ("retro", "story-craft", "story maintenance")),
        ("skills-repo-ci", ("repo", "skill-contract", "snapshot", "ci", "precommit", "test")),
    )
    for topic_id, markers in explicit:
        if any(marker in text for marker in markers):
            return topics.get(topic_id) or {"topic_id": topic_id, "group": "", "title": topic_id}
    if any(marker in text for marker in ("workflow", "m2", "linear", "team-lead", "dispatcher", "skills")):
        return topics.get("skills-workflow")
    return None


def _root_name(topic: dict[str, str]) -> str:
    return ROOT_NAME_OVERRIDES.get(topic.get("topic_id", ""), topic.get("title", topic.get("topic_id", "")))


def _parse_events(text: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for match in EVENT_RE.finditer(text):
        fields: dict[str, str] = {}
        for field in match.group(1).split(" | "):
            if "=" in field:
                key, value = field.split("=", 1)
                fields[key.strip()] = value.strip()
        if fields.get("EVT"):
            events.append(fields)
    return events


def _normalize_task_status(raw_status: str) -> str:
    token = str(raw_status or "").split(",", 1)[0].split(":", 1)[0].strip().casefold()
    return token


def _active_blocking_events(
    events: list[dict[str, str]],
    tasks: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    blocking_types = {
        "TASK_BLOCKED",
        "INPUT_BLOCKED",
        "REVIEW_FAIL",
        "NEED_GO",
        "NEED_OWNER_DECISION",
    }
    clearing_types = {"RESOLVED", "ACK", "DONE", "REVIEW_PASS", "PR_MERGED", "GOAL_DONE"}
    active: dict[str, dict[str, str]] = {}
    unkeyed: list[dict[str, str]] = []
    for event in events:
        event_type = event.get("EVT", "").upper()
        event_id = event.get("id", "")
        task_id = event.get("task", "")
        if event_type in blocking_types:
            if event_id:
                active[event_id] = event
            else:
                unkeyed.append(event)
        elif event_type in clearing_types:
            if event_id:
                active.pop(event_id, None)
            if task_id:
                for k in [k for k, v in active.items() if v.get("task") == task_id]:
                    active.pop(k, None)
                unkeyed = [v for v in unkeyed if v.get("task") != task_id]
    if tasks is not None:
        resolved_tasks = {
            str(t.get("task_id", ""))
            for t in tasks
            if _normalize_task_status(t.get("status", "")) in {"done", "cancelled", "in_review"}
        }
        if resolved_tasks:
            active = {k: v for k, v in active.items() if v.get("task") not in resolved_tasks}
            unkeyed = [v for v in unkeyed if v.get("task") not in resolved_tasks]
        active_tasks = {v.get("task") for v in [*active.values(), *unkeyed] if v.get("task")}
        for t in tasks:
            if _normalize_task_status(t.get("status", "")) == "blocked" and t.get("task_id") not in active_tasks:
                unkeyed.append({"EVT": "TASK_BLOCKED", "task": str(t.get("task_id", "")), "note": str(t.get("status", ""))})
    return [*active.values(), *unkeyed]


def _parse_tasks(ledger_text: str, queue_data: dict[str, Any] | None) -> list[dict[str, str]]:
    queue_by_id = {
        str(task.get("task_id")): task
        for task in (queue_data or {}).get("tasks", [])
        if task.get("task_id")
    }
    tasks: list[dict[str, str]] = []
    in_table = False
    headers: dict[str, int] | None = None
    aliases = {
        "id": {"id", "task", "task id"},
        "title": {"title", "标题"},
        "status": {"status", "状态"},
        "pr": {"pr", "pr/head", "pr / head"},
        "owner": {"owner", "负责", "写者"},
        "receipts": {"receipts", "验收证据／回执"},
    }
    for line in ledger_text.splitlines():
        if line.strip() == "## Tasks":
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not line.startswith("|") or line.count("|") < 2:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        normalized = [re.sub(r"\s+", " ", c.casefold()) for c in cells]
        if normalized and all(set(c) <= {"-", ":"} for c in normalized):
            continue
        if normalized and any(normalized[0] == value for value in aliases["id"]):
            in_table = True
            headers = {}
            for index, cell in enumerate(normalized):
                for key, names in aliases.items():
                    if cell in names:
                        headers[key] = index
                        break
            continue
        if not in_table:
            continue
        if headers:
            get = lambda key: cells[headers[key]] if headers.get(key, 999) < len(cells) else ""
            task_id, title, status, pr = get("id"), get("title"), get("status"), get("pr")
        else:
            task_id, _, title, _, status, pr = (cells + [""] * 6)[:6]
        if not re.match(r"^[A-Za-z0-9_.-]+$", task_id):
            continue
        queued = queue_by_id.get(task_id, {})
        tasks.append(
            {
                "task_id": task_id,
                "title": str(queued.get("title") or title),
                "status": str(queued.get("status") or status),
                "pr": str(queued.get("pr") or pr or ""),
            }
        )
    return tasks


def _load_handoff(handoff_dir: Path) -> tuple[str, list[dict[str, str]], list[dict[str, str]], dict[str, str]]:
    ledger_path = handoff_dir / "ledger.md"
    if not ledger_path.is_file():
        raise LinearSyncError(f"Missing ledger: {ledger_path}")
    ledger_text = _read_ledger(handoff_dir)
    queue_data: dict[str, Any] | None = None
    queue_path = handoff_dir / "queue.json"
    if queue_path.is_file():
        try:
            loaded = json.loads(queue_path.read_text(encoding="utf-8"))
            queue_data = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            queue_data = None
    goal_match = re.search(r"(?m)^#\s*Goal:\s*(.+?)\s*$", ledger_text)
    goal_name = goal_match.group(1).strip() if goal_match else handoff_dir.name
    header: dict[str, str] = {}
    for line in ledger_text.splitlines():
        if line.startswith("- ") and ":" in line:
            key, value = line[2:].split(":", 1)
            header[key.strip().casefold()] = value.strip()
    return goal_name, _parse_tasks(ledger_text, queue_data), _parse_events(ledger_text), header


def _has_meaningful_attribution(meta: dict[str, Any] | None) -> bool:
    if not meta:
        return False
    if any(meta.get(key) not in (None, "", "—") for key in ("tokens", "cost")):
        return True
    try:
        return float(meta.get("duration_sec", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _infer_github_repo(goal_name: str, ledger_text: str, handoff_dir: Path | None = None) -> str | None:
    repo_match = re.search(r"\brepo=([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", ledger_text)
    if repo_match:
        return repo_match.group(1)
    combined = f"{goal_name} {ledger_text} {handoff_dir or ''}".casefold()
    if "financial" in combined or "fin-" in combined:
        return "LeePepe/financial"
    if "vitalstride" in combined or "workout" in combined:
        return "LeePepe/VitalStride"
    if "voxpocket" in combined or "voxkit" in combined:
        return "LeePepe/VoxPocket"
    if "aidash" in combined:
        return "LeePepe/AIDash"
    if "user-skills" in combined or "linear-sync" in combined or "workflow-hub" in combined:
        return "LeePepe/user-skills"
    return None


def _format_pr_ref(raw_pr: str, repo: str | None) -> str:
    text = str(raw_pr or "").strip()
    if not text or text in {"—", "-"}:
        return ""
    if "http://" in text or "https://" in text:
        return text
    if repo:
        def _repl(m: re.Match[str]) -> str:
            num = m.group(1)
            return f"[PR #{num}](https://github.com/{repo}/pull/{num})"
        linked = re.sub(r"(?:PR\s*#?|#)(\d+)", _repl, text, flags=re.IGNORECASE)
        if linked != text:
            return linked
    return text


def _build_status_bar(tasks: list[dict[str, str]]) -> str:
    if not tasks:
        return ""
    total = len(tasks)
    done_list = [t for t in tasks if _normalize_task_status(t.get("status", "")) == "done"]
    review_list = [t for t in tasks if _normalize_task_status(t.get("status", "")) == "in_review"]
    progress_list = [t for t in tasks if _normalize_task_status(t.get("status", "")) in {"in_progress", "changes_requested"}]
    blocked_list = [t for t in tasks if _normalize_task_status(t.get("status", "")) == "blocked"]
    planned_list = [t for t in tasks if _normalize_task_status(t.get("status", "")) in {"planned", "ready"}]
    parts = [f"`{len(done_list)}/{total} 已完成`"]
    if review_list:
        ids = ", ".join(t["task_id"] for t in review_list[:3])
        parts.append(f"`{len(review_list)} 审查中 ({ids})`")
    if progress_list:
        ids = ", ".join(t["task_id"] for t in progress_list[:3])
        parts.append(f"`{len(progress_list)} 进行中 ({ids})`")
    if blocked_list:
        ids = ", ".join(t["task_id"] for t in blocked_list[:3])
        parts.append(f"`🚨 {len(blocked_list)} 阻塞 ({ids})`")
    if planned_list:
        nxt = planned_list[0]
        short_title = re.sub(r"\s*[（(].*?[)）]", "", nxt["title"]).strip() or nxt["title"][:32]
        parts.append(f"`下一步：{nxt['task_id']} {short_title}`")
    return "**📊 当前状态**：" + " · ".join(parts)


def _description_block(
    goal_name: str,
    ledger_text: str,
    tasks: list[dict[str, str]],
    handoff_dir: Path | None = None,
) -> str:
    header_lines: list[str] = []
    machine_keys = {
        "协调者 bot", "协调者", "tl", "机器", "machine", "合同 sha", "合同版本",
        "contract", "contract sha", "linear", "linear issue", "状态", "status", "project",
    }
    found_goal = False
    for line in ledger_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("## Tasks") or stripped.startswith("## Events") or stripped.startswith("|"):
            break
        match = re.match(r"^\s*[-*]\s+(.*?)\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        key = match.group(1).strip().strip("*").strip()
        value = match.group(2).strip()
        if not value:
            continue
        if " | " in line and re.search(r"(?:EVT=|evt=|id=|ts=)", line):
            continue
        if re.match(r"^[-*]\s+(?:`?ts=|20\d\d-\d\d-\d\d)", line, re.IGNORECASE):
            continue
        if key.casefold() in machine_keys:
            continue
        found_goal = found_goal or key.casefold() in {"目标", "goal"}
        header_lines.append(f"- {key}: {value}")
    if not found_goal and tasks:
        titles = [str(task.get("title", "")) for task in tasks[:3]]
        header_lines.insert(0, f"- 目标: {'; '.join(titles) if len(tasks) > 1 else titles[0]}")
    summary = "\n".join(header_lines) or f"- Goal: {goal_name}"
    repo = _infer_github_repo(goal_name, ledger_text, handoff_dir)
    checklist: list[str] = []
    attribution = _collect_task_attribution(handoff_dir, ledger_text, tasks)
    status_badges = {
        "in_review": "👀 *In Review*",
        "in_progress": "🔨 *In Progress*",
        "changes_requested": "🔧 *Changes Requested*",
        "blocked": "🚨 *Blocked*",
        "planned": "⏳ *Planned*",
        "ready": "⏳ *Ready*",
    }
    for task in tasks:
        raw_status = task["status"]
        norm = _normalize_task_status(raw_status)
        checked = "x" if norm == "done" else " "
        pr_str = _format_pr_ref(task.get("pr", ""), repo)
        meta = attribution.get(task["task_id"])
        attr_str = ""
        if _has_meaningful_attribution(meta):
            attr_str = f"runs={meta['runs']}, duration={meta['duration_sec']}s, tokens={meta['tokens']}, cost={meta['cost']}"
        details = [x for x in (pr_str, attr_str) if x]
        detail_suffix = f" ({'; '.join(details)})" if details else ""
        if norm == "done":
            checklist.append(f"- [{checked}] **{task['task_id']}**: {task['title']}{detail_suffix}")
        else:
            badge = status_badges.get(norm, f"*{raw_status}*")
            if norm == "blocked" and ":" in raw_status:
                reason = raw_status.split(":", 1)[1].strip()
                badge = f"🚨 *Blocked: {reason}*"
            checklist.append(f"- [{checked}] **{task['task_id']}**: {task['title']} — {badge}{detail_suffix}")
    attribution_block = ""
    goal = attribution.get("__goal__")
    if _has_meaningful_attribution(goal):
        attribution_block = (
            "\n\n## Execution & Cost Attribution\n"
            f"- runs: {goal['runs']}; duration: {goal['duration_sec']}s; "
            f"tokens: {goal['tokens']}; cost: {goal['cost']}"
        )
    status_bar = _build_status_bar(tasks)
    status_section = f"{status_bar}\n\n" if status_bar else ""
    return (
        MARKER_START
        + "\n"
        + status_section
        + "## Tasks\n"
        + ("\n".join(checklist) if checklist else "- [ ] No tasks recorded")
        + "\n\n## Goal Summary\n"
        + summary
        + attribution_block
        + "\n"
        + MARKER_END
    )


def _format_tokens(value: int, lower_bound: bool = False) -> str:
    if not value:
        return "—"
    suffix = "K" if value >= 1000 else ""
    number = value / 1000 if suffix else value
    result = f"{number:.1f}{suffix}" if suffix else str(value)
    return f"≥{result}" if lower_bound else result


def _format_cost(value: float, lower_bound: bool = False) -> str:
    if value <= 0:
        return "—"
    result = f"${value:.2f}"
    return f"≥{result}" if lower_bound else result


def _model_prices() -> dict[str, dict[str, float]]:
    prices = {key: dict(value) for key, value in DEFAULT_MODEL_PRICES.items()}
    csv_path = os.environ.get("GTT_MODEL_PRICES_CSV", "").strip()
    if not csv_path:
        return prices
    try:
        import csv
        with Path(csv_path).expanduser().open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                model = str(row.get("model") or row.get("model_name") or "").strip()
                if not model:
                    continue
                prices[model] = {
                    key: float(row.get(key, 0) or 0)
                    for key in ("input_per_mtok", "output_per_mtok", "cache_read_per_mtok", "cache_write_per_mtok")
                }
    except (OSError, ValueError, TypeError):
        pass
    return prices


def _parse_numeric(value: Any) -> float:
    if value in (None, "", "—", "-"):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").lstrip("≥~$").rstrip("sS")
    match = re.match(r"^([+-]?\d+(?:\.\d+)?)\s*([kKmM])?$", text)
    if not match:
        return 0.0
    num = float(match.group(1))
    suffix = (match.group(2) or "").casefold()
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    return num


def _run_cost(run: dict[str, Any], prices: dict[str, dict[str, float]]) -> float:
    if run.get("cost_usd") not in (None, ""):
        return _parse_numeric(run.get("cost_usd"))
    model = str(run.get("model") or "default")
    price = prices.get(model, prices["default"])
    return sum(
        _parse_numeric(run.get(token_key, 0)) / 1_000_000 * price[price_key]
        for token_key, price_key in (
            ("input_tokens", "input_per_mtok"),
            ("output_tokens", "output_per_mtok"),
            ("cache_read_tokens", "cache_read_per_mtok"),
            ("cache_write_tokens", "cache_write_per_mtok"),
        )
    )


def _metric_runs(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if any(key in value for key in ("input_tokens", "output_tokens", "cost_usd", "model", "duration_sec")):
            return [value]
        return [item for item in value.values() if isinstance(item, dict)]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _receipt_field(text: str, field: str) -> str:
    match = re.search(
        rf"(?mi)^\s*-\s*\**{re.escape(field)}\**\s*:\s*`?([^`\r\n]+?)`?\s*$",
        text,
    )
    return match.group(1).strip() if match else ""


def _collect_task_attribution(
    handoff_dir: Path | None, ledger_text: str, tasks: list[dict[str, str]]
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    task_ids = {task["task_id"] for task in tasks}
    prices = _model_prices()

    def item_for(task_id: str) -> dict[str, Any]:
        return metrics.setdefault(task_id, {
            "runs": 0, "metered": 0, "duration": 0.0, "tokens_raw": 0,
            "cost_raw": 0.0, "v5_verdict": "", "receipts": [],
        })

    def add_run(task_id: str, run: dict[str, Any], receipt: str | None = None) -> None:
        if task_id not in task_ids:
            return
        item = item_for(task_id)
        item["runs"] += 1
        token_count = sum(int(_parse_numeric(run.get(key, 0))) for key in (
            "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "tokens",
        ))
        if token_count:
            item["metered"] += 1
            item["tokens_raw"] += token_count
        item["duration"] += _parse_numeric(run.get("duration_sec", 0))
        item["cost_raw"] += _run_cost(run, prices)
        item["v5_verdict"] = str(run.get("v5_verdict") or item["v5_verdict"])
        if receipt and receipt not in item["receipts"]:
            item["receipts"].append(receipt)

    for event in _parse_events(ledger_text):
        task_id = event.get("task")
        if task_id in task_ids and ("tokens" in event or "duration_sec" in event):
            add_run(task_id, {"tokens": event.get("tokens", 0), "duration_sec": event.get("duration_sec", 0)})
    if handoff_dir and handoff_dir.is_dir():
        queue_path = handoff_dir / "queue.json"
        try:
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            for task in queue.get("tasks", []):
                task_id = str(task.get("task_id", ""))
                for run in _metric_runs(task.get("run_metrics")):
                    add_run(task_id, run)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        receipts = handoff_dir / "receipts"
        if receipts.is_dir():
            for path in receipts.glob("*.md"):
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                match_task = re.search(r"(?mi)^\s*-\s*\**(?:Task|任务)\**\s*:\s*`?([A-Za-z0-9_.-]+)", text)
                if not match_task or match_task.group(1) not in task_ids:
                    continue
                task_id = match_task.group(1)
                run = {
                    "tokens": _receipt_field(text, "Tokens").replace(",", ""),
                    "duration_sec": _receipt_field(text, "Duration").split()[0] if _receipt_field(text, "Duration") else 0,
                    "cost_usd": _receipt_field(text, "Cost").replace("$", ""),
                    "model": _receipt_field(text, "Model"),
                    "v5_verdict": _receipt_field(text, "V5 Verdict"),
                }
                add_run(task_id, run, str(path.relative_to(handoff_dir)))
    if not metrics:
        return {}
    total_runs = sum(int(item["runs"]) for item in metrics.values())
    total_metered = sum(int(item["metered"]) for item in metrics.values())
    total_tokens = sum(int(item["tokens_raw"]) for item in metrics.values())
    total_cost = sum(float(item["cost_raw"]) for item in metrics.values())
    output: dict[str, dict[str, Any]] = {}
    for task_id, item in metrics.items():
        lower = item["runs"] > item["metered"] > 0
        output[task_id] = {
            "runs": item["runs"], "duration_sec": f"{item['duration']:.1f}",
            "tokens": _format_tokens(item["tokens_raw"], lower),
            "cost": _format_cost(item["cost_raw"], lower),
            "receipts": item["receipts"],
            "v5_verdict": item["v5_verdict"],
        }
    output["__goal__"] = {
        "runs": total_runs, "duration_sec": f"{sum(float(i['duration']) for i in metrics.values()):.1f}",
        "tokens": _format_tokens(total_tokens, total_runs > total_metered > 0),
        "cost": _format_cost(total_cost, total_runs > total_metered > 0),
    }
    return output


def _strip_duplicate_task_sections(prefix: str) -> str:
    lines = prefix.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        if not re.match(r"^##\s+Tasks(?:\s+Checklist.*)?\s*$", lines[index].rstrip("\r\n"), re.IGNORECASE):
            index += 1
            continue
        end = index + 1
        while end < len(lines) and not re.match(r"^##\s+", lines[end]):
            end += 1
        body = [line.strip() for line in lines[index + 1:end] if line.strip()]
        if body and all(re.match(r"^-\s+\[[ xX]\]", line) for line in body):
            del lines[index:end]
            continue
        index = end
    return "".join(lines)


def _updated_description(existing: str | None, block: str) -> str:
    current = existing or ""
    start = current.find(MARKER_START)
    if start < 0:
        prefix = _strip_duplicate_task_sections(current).rstrip()
        return (prefix + "\n\n" + block) if prefix else block
    end = current.find(MARKER_END, start + len(MARKER_START))
    prefix = _strip_duplicate_task_sections(current[:start]).rstrip()
    if end < 0:
        return (prefix + "\n\n" + block) if prefix else block
    end += len(MARKER_END)
    suffix = current[end:]
    return ((prefix + "\n\n") if prefix else "") + block + suffix


def _aggregate_state(team: dict[str, Any], tasks: list[dict[str, str]], events: list[dict[str, str]], header: dict[str, str]) -> dict[str, Any] | None:
    event_names = {event.get("EVT", "").upper() for event in events}
    header_status = header.get("status", "").casefold()
    statuses = [_normalize_task_status(task.get("status", "")) for task in tasks]
    if "GOAL_DONE" in event_names or (statuses and all(s in {"done", "cancelled"} for s in statuses) and any(s == "done" for s in statuses)):
        return _state(team, "Done")
    if header_status == "cancelled" or "GOAL_CANCELLED" in event_names or "CANCELLED" in event_names:
        return _state(team, "Canceled")
    if any(status in {"in_progress", "changes_requested", "blocked"} for status in statuses):
        return _state(team, "In Progress")
    if any(status == "in_review" for status in statuses) and all(status in {"in_review", "done", "cancelled"} for status in statuses):
        return _state(team, "In Review", "In Progress")
    if any(status in {"done", "in_review"} for status in statuses):
        return _state(team, "In Progress")
    if statuses and all(status in {"planned", "ready"} for status in statuses):
        return _state(team, "Todo")
    return _state(team, "Todo", "In Progress")


def _issue_comment_bodies(issue: dict[str, Any]) -> list[str]:
    return [str(node.get("body", "")) for node in issue.get("comments", {}).get("nodes", [])]


def _comment_mutation(issue_id: str, body: str, create_as_user: str | None) -> dict[str, Any]:
    input_data: dict[str, Any] = {"issueId": issue_id, "body": body}
    if create_as_user and _oauth_app_enabled():
        input_data["createAsUser"] = create_as_user
    return {
        "operation": "CreateComment",
        "graphql_operation_name": "CreateComment",
        "variables": {"input": input_data},
    }


def _read_ledger(handoff_dir: Path) -> str:
    ledger_path = handoff_dir / "ledger.md"
    try:
        return ledger_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LinearSyncError(f"Unable to read ledger: {ledger_path}: {exc}") from exc


def _ledger_tl(text: str) -> str | None:
    match = re.search(r"(?mi)^\s*-\s*TL:\s*(\S.*?)\s*$", text)
    return match.group(1).strip() if match and match.group(1).strip() else None


def _resolve_goal_route(
    client: GraphQLClient,
    team: dict[str, Any],
    goal_name: str,
    ledger_text: str,
    handoff_dir: Path,
) -> dict[str, str] | None:
    topic = _goal_topic(goal_name, ledger_text, handoff_dir)
    if not topic:
        return None
    project_name = DOMAIN_PROJECT_NAMES.get(topic.get("group", ""), "")
    project_id = _project_id(team, project_name)
    if not project_id:
        return None
    milestone_name = _root_name(topic)
    milestones = client.project_milestones(project_id)
    milestone = next((item for item in milestones if str(item.get("name", "")).casefold() == milestone_name.casefold()), None)
    if not milestone or not milestone.get("id"):
        return None
    return {
        "topic_id": topic.get("topic_id", ""),
        "project_id": str(project_id),
        "milestone_id": str(milestone["id"]),
    }


def _sync_issue(
    client: GraphQLClient,
    issue: dict[str, Any],
    team: dict[str, Any],
    handoff_dir: Path,
    *,
    event_note: str | None = None,
    project_name: str | None = None,
    mode: str = "auto",
    create_as_user: str | None = None,
) -> dict[str, Any]:
    ledger_text = _read_ledger(handoff_dir)
    goal_name, tasks, events, header = _load_handoff(handoff_dir)
    attribution = _collect_task_attribution(handoff_dir, ledger_text, tasks)
    inferred_project = _infer_project_name(project_name, ledger_text, str(handoff_dir))
    project_id = _project_id(team, inferred_project)
    goal_route = None
    if mode != "observe" and str(issue.get("title") or "").startswith("[Goal]"):
        goal_route = _resolve_goal_route(client, team, goal_name, ledger_text, handoff_dir)
        if goal_route:
            project_id = goal_route["project_id"]
            inferred_project = next(
                (value for value in DOMAIN_PROJECT_NAMES.values() if _project_id(team, value) == project_id),
                inferred_project,
            )
    current_project_id = str((issue.get("project") or {}).get("id") or "")
    current_milestone_id = str((issue.get("projectMilestone") or {}).get("id") or "")
    current_parent_id = str((issue.get("parent") or {}).get("id") or "")
    explicit_project = bool(project_name and project_name.strip())
    description = _updated_description(
        issue.get("description"),
        _description_block(goal_name, ledger_text, tasks, handoff_dir),
    )
    target_state = _aggregate_state(team, tasks, events, header)
    labels = [str(node["id"]) for node in issue.get("labels", {}).get("nodes", []) if node.get("id")]
    blocking = _active_blocking_events(events, tasks)
    blocked_labels = [
        lbl for lbl in (
            _label(team, "Needs Decision"),
            _label(team, "Blocked"),
            _label(team, "NEED_GO"),
        ) if lbl and lbl.get("id")
    ]
    if blocking and blocked_labels:
        primary_blocked_id = str(blocked_labels[0]["id"])
        if primary_blocked_id not in labels:
            labels.append(primary_blocked_id)
    elif not blocking and blocked_labels:
        blocked_ids = {str(lbl["id"]) for lbl in blocked_labels}
        labels = [label_id for label_id in labels if label_id not in blocked_ids]
    target_project_id = project_id if project_id and (explicit_project or not current_project_id or goal_route) else None
    target_milestone_id = goal_route.get("milestone_id") if goal_route and goal_route.get("milestone_id") else None
    target_parent_id = None
    clear_goal_parent = goal_route is not None
    current_state_id = str((issue.get("state") or {}).get("id") or "")
    current_labels = [str(node["id"]) for node in issue.get("labels", {}).get("nodes", []) if node.get("id")]
    state_changed = bool(target_state and str(target_state.get("id") or "") != current_state_id)
    labels_changed = set(labels) != set(current_labels)
    project_changed = bool(target_project_id and target_project_id != current_project_id)
    milestone_changed = bool(target_milestone_id and target_milestone_id != current_milestone_id)
    parent_changed = clear_goal_parent and bool(current_parent_id)
    description_changed = description != str(issue.get("description") or "")
    has_field_changes = description_changed or state_changed or labels_changed or project_changed or milestone_changed or parent_changed
    planned_mutations: list[dict[str, Any]] = []
    if has_field_changes:
        planned_mutations.append({"operation": "UpdateIssue", "graphql_operation_name": "UpdateIssue", "variables": {
            "id": str(issue["id"]), "input": {
                "description": description,
                "stateId": str(target_state["id"]) if target_state else None,
                "labelIds": labels,
                **({"projectId": target_project_id} if target_project_id else {}),
                **({"projectMilestoneId": target_milestone_id} if target_milestone_id else {}),
                **({"parentId": target_parent_id} if clear_goal_parent else {}),
            },
        }})
    if mode == "auto" and has_field_changes:
        issue = client.update_issue(
            str(issue["id"]), description=description,
            state_id=str(target_state["id"]) if target_state else None,
            label_ids=labels, project_id=target_project_id,
            project_milestone_id=target_milestone_id, parent_id=target_parent_id,
            clear_parent=clear_goal_parent,
        )
    existing_comments = _issue_comment_bodies(issue)
    for event in blocking:
        event_id = event.get("id", "")
        marker = f"<!-- GTT-LINEAR-EVENT:{event_id} -->"
        if event_id and not any(marker in body for body in existing_comments):
            body = f"{marker}\n**{event.get('EVT')}** `{event_id}`"
            if event.get("task"):
                body += f" · Task `{event['task']}`"
            if event.get("note"):
                body += f"\n{event['note']}"
            planned_mutations.append(_comment_mutation(str(issue["id"]), body, create_as_user))
            if mode == "auto":
                client.create_comment(str(issue["id"]), body, create_as_user)
            existing_comments.append(body)
    task_ids = {t["task_id"] for t in tasks}
    for event in events:
        event_type = event.get("EVT", "").upper()
        task_id = event.get("task", "")
        if event_type not in {"DONE", "GOAL_DONE"}:
            continue
        if event_type != "GOAL_DONE" and task_id not in task_ids:
            continue
        meta = attribution.get(task_id) if task_id else None
        if event_type != "GOAL_DONE" and not _has_meaningful_attribution(meta) and not PR_RE.search(
            f"{event.get('ref', '')} {event.get('note', '')}"
        ):
            continue
        event_id = event.get("id", "")
        marker = f"<!-- GTT-LINEAR-MILESTONE:{event_id} -->"
        if event_id and not any(marker in body for body in existing_comments):
            body = f"{marker}\n**{event_type}**"
            if task_id:
                body += f" · Task `{task_id}`"
                if _has_meaningful_attribution(meta):
                    body += f"\nRun attribution: {meta['runs']} run(s), {meta['tokens']}, {meta['cost']}"
                    if meta.get("receipts"):
                        body += f" · receipt `{meta['receipts'][0]}`"
            if event.get("ref"):
                body += f"\n{event['ref']}"
            planned_mutations.append(_comment_mutation(str(issue["id"]), body, create_as_user))
            if mode == "auto":
                client.create_comment(str(issue["id"]), body, create_as_user)
            existing_comments.append(body)
    if event_note:
        marker = f"<!-- GTT-LINEAR-NOTE:{event_note} -->"
        if not any(marker in body for body in existing_comments):
            note_body = f"{marker}\n{event_note}"
            planned_mutations.append(_comment_mutation(str(issue["id"]), note_body, create_as_user))
            if mode == "auto":
                client.create_comment(str(issue["id"]), note_body, create_as_user)
            existing_comments.append(marker)
    if mode == "observe":
        return {
            **issue,
            "diff": {"has_changes": has_field_changes, "issue_create": False,
                     "fields": [key for key, changed in (("description", description_changed), ("state", state_changed), ("labels", labels_changed), ("project", project_changed), ("milestone", milestone_changed), ("parent", parent_changed)) if changed],
                     "comments_to_create": [m["variables"]["input"]["body"] for m in planned_mutations if m["operation"] == "CreateComment"],
                     "local_effects": []},
            "planned_mutations": planned_mutations,
        }
    issue["_mutations_executed"] = len(planned_mutations) if mode == "auto" else 0
    return issue


def _bind_issue_line(handoff_dir: Path, identifier: str, url: str) -> None:
    path = handoff_dir / "ledger.md"
    text = _read_ledger(handoff_dir)
    binding = f"- Linear: {identifier}" + (f" ({url})" if url else "")
    if ISSUE_RE.search(text):
        text = ISSUE_RE.sub(binding, text, count=1)
    else:
        lines = text.splitlines()
        insert_at = next((index for index, line in enumerate(lines) if line.startswith("## Tasks")), len(lines))
        lines[insert_at:insert_at] = [binding, ""]
        text = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    _atomic_write_text(path, text, 0o600)


@contextlib.contextmanager
def _queue_lock(handoff_dir: Path) -> Iterator[None]:
    lock_path = handoff_dir / ".queue.lock"
    if lock_path.is_symlink():
        raise LinearSyncError(f"Refusing symlinked lock: {lock_path}")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _bound_issue(text: str) -> str | None:
    match = ISSUE_RE.search(text)
    return match.group(1) if match else None


def _client_or_fail() -> GraphQLClient:
    key = load_api_key()
    if not key:
        raise LinearSyncError("LINEAR_API_KEY is not configured")
    return GraphQLClient(key)


def _offline_snapshot(value: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not value:
        return None
    try:
        path = Path(value)
        raw = path.read_text(encoding="utf-8") if path.is_file() else value
        data = json.loads(raw)
        return data.get("team", {}), data.get("issue", {})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise LinearSyncError(f"invalid offline snapshot: {exc}") from exc


def _synthetic_team() -> dict[str, Any]:
    return {"id": "offline-team", "key": "TIA", "name": "TIA", "states": {"nodes": [
        {"id": "todo", "name": "Todo"}, {"id": "progress", "name": "In Progress"},
        {"id": "backlog", "name": "Backlog"}, {"id": "done", "name": "Done"},
    ]}, "labels": {"nodes": []}, "projects": {"nodes": []}}


def _virtual_issue(identifier: str = "") -> dict[str, Any]:
    return {
        "id": "offline-issue",
        "identifier": identifier,
        "url": "",
        "description": "",
        "state": {},
        "labels": {"nodes": []},
        "comments": {"nodes": []},
        "project": None,
    }


def _is_closed_issue(issue: dict[str, Any]) -> bool:
    state = issue.get("state") or {}
    return str(state.get("type") or state.get("name") or "").casefold() in {
        "completed", "canceled", "cancelled", "done",
    }


def _issue_summary(issue: dict[str, Any]) -> dict[str, Any]:
    state = issue.get("state") or {}
    project = issue.get("project") or {}
    return {
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "state": state.get("name") or state.get("type"),
        "project": project.get("name") if project else None,
        "priority": issue.get("priority"),
        "url": issue.get("url"),
        "updatedAt": issue.get("updatedAt"),
    }


def _issue_detail(issue: dict[str, Any]) -> dict[str, Any]:
    result = _issue_summary(issue)
    result.update({
        "description": issue.get("description"),
        "labels": [node.get("name") for node in (issue.get("labels") or {}).get("nodes", [])],
        "comments": (issue.get("comments") or {}).get("nodes", []),
        "history": (issue.get("history") or {}).get("nodes", []),
    })
    return result


def cmd_list_issues(args: argparse.Namespace) -> dict[str, Any]:
    client = _client_or_fail()
    issues = client.list_issues(
        args.team, state=args.state, project=args.project, query=args.query, limit=args.limit,
    )
    return {"ok": True, "command": "list-issues", "team": args.team, "issues": [_issue_summary(issue) for issue in issues]}


def cmd_get_issue(args: argparse.Namespace) -> dict[str, Any]:
    return {"ok": True, "command": "get-issue", "issue": _issue_detail(_client_or_fail().issue(args.id))}


def _update_mutation(issue: dict[str, Any], input_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation": "UpdateIssue", "graphql_operation_name": "UpdateIssue",
        "variables": {"id": str(issue.get("id") or issue.get("identifier")), "input": input_data},
    }


def cmd_update_issue(args: argparse.Namespace) -> dict[str, Any]:
    mode, skipped = resolve_mode(args.mode, subcommand="update-issue")
    if mode == "off":
        return {"ok": True, "command": "update-issue", "mode": "off", "decision": "off (skipped)",
                "skipped": skipped or "mode_off", "planned_mutations": []}
    requested = {
        key: value for key, value in {
            "title": args.title, "description": args.description, "priority": args.priority,
        }.items() if value is not None
    }
    if mode == "observe":
        if args.state:
            requested["stateId"] = args.state
        if args.project:
            requested["projectId"] = args.project
        if args.add_label:
            requested["labelIds"] = list(args.add_label)
        issue = {"id": args.id, "identifier": args.id}
    elif args.state or args.project or args.add_label:
        client = _client_or_fail()
        issue = client.issue(args.id)
        team = client.team(str((issue.get("team") or {}).get("key") or "TIA"))
        if args.state:
            state = _state(team, args.state)
            if not state:
                raise LinearSyncError(f"Linear state '{args.state}' was not found")
            requested["stateId"] = str(state["id"])
        if args.project:
            project_id = _project_id(team, args.project)
            if not project_id:
                raise LinearSyncError(f"Linear project '{args.project}' was not found")
            requested["projectId"] = project_id
        if args.add_label:
            labels = [str(node["id"]) for node in (issue.get("labels") or {}).get("nodes", []) if node.get("id")]
            for name in args.add_label:
                label = _label(team, name)
                if not label:
                    raise LinearSyncError(f"Linear label '{name}' was not found")
                if str(label["id"]) not in labels:
                    labels.append(str(label["id"]))
            requested["labelIds"] = labels
    else:
        issue = {"id": args.id, "identifier": args.id}
    if not requested:
        raise LinearSyncError("update-issue requires at least one update option")
    mutation = _update_mutation(issue, requested)
    if mode == "observe":
        return {"ok": True, "command": "update-issue", "mode": mode,
                "decision": "observe (would accept)", "would_accept": True,
                "planned_mutations": [mutation]}
    client = locals().get("client") or _client_or_fail()
    updated = client.update_issue(
        str(issue.get("id") or issue["identifier"]),
        title=requested.get("title"), description=requested.get("description"),
        state_id=requested.get("stateId"), project_id=requested.get("projectId"),
        priority=requested.get("priority"), label_ids=requested.get("labelIds"),
    )
    return {"ok": True, "command": "update-issue", "mode": mode, "decision": "accept",
            "issue": _issue_detail(updated)}


def cmd_comment(args: argparse.Namespace) -> dict[str, Any]:
    mode, skipped = resolve_mode(args.mode, subcommand="comment")
    mutation = _comment_mutation(args.id, args.body, args.create_as_user)
    if mode == "off":
        return {"ok": True, "command": "comment", "mode": "off", "decision": "off (skipped)",
                "skipped": skipped or "mode_off", "planned_mutations": []}
    if mode == "observe":
        return {"ok": True, "command": "comment", "mode": mode, "decision": "observe (would accept)",
                "would_accept": True, "planned_mutations": [mutation]}
    _client_or_fail().create_comment(args.id, args.body, args.create_as_user)
    return {"ok": True, "command": "comment", "mode": mode, "decision": "accept",
            "identifier": args.id}


def cmd_idea(args: argparse.Namespace) -> dict[str, Any]:
    mode, skipped = resolve_mode(getattr(args, "mode", None), subcommand="idea", source=getattr(args, "source", ""))
    if mode == "off":
        return {"schema_version": 1, "ok": True, "command": "idea", "mode": "off",
                "decision": "off (skipped)", "would_accept": False, "skipped": skipped or "mode_off",
                "planned_mutations": []}
    snapshot = _offline_snapshot(getattr(args, "offline_snapshot", ""))
    client = None if snapshot else (_client_or_fail() if mode == "auto" or load_api_key() else None)
    if snapshot:
        team = snapshot[0]
    elif client:
        try:
            team = client.team(args.team)
        except (LinearSyncError, AssertionError):
            if mode != "observe":
                raise
            team = _synthetic_team()
            client = None
    else:
        team = _synthetic_team()
    project_name = _infer_project_name(
        getattr(args, "project", ""),
        f"{args.title} {args.description}",
        getattr(args, "repo", ""),
    )
    project_id = _project_id(team, project_name)
    target_state = _state(team, args.state, "Backlog")
    label = _label(team, args.label) if args.label else None
    description = args.description or ""
    if getattr(args, "dedup_key", ""):
        marker = f"<!-- GTT-LINEAR-DEDUP:{args.dedup_key} -->"
        description += f"\n\n{marker}"
        existing = None
        if snapshot:
            snapshot_issue = snapshot[1]
            candidates = snapshot_issue.get("issues", []) if isinstance(snapshot_issue, dict) and isinstance(snapshot_issue.get("issues"), list) else (
                snapshot_issue if isinstance(snapshot_issue, list) else [snapshot_issue]
            )
            existing = next(
                (issue for issue in candidates if isinstance(issue, dict)
                 and marker in str(issue.get("description") or "") and not _is_closed_issue(issue)),
                None,
            )
        elif client:
            try:
                existing = client.find_issue_by_dedup_marker(str(team["id"]), marker)
            except (LinearSyncError, AssertionError):
                existing = None
        if existing:
            return {
                "ok": True, "mode": mode, "decision": "no-op (duplicate)",
                "would_accept": False, "duplicate_of": existing.get("identifier"),
                "identifier": existing.get("identifier"), "url": existing.get("url"),
                "state": (existing.get("state") or {}).get("name"),
                "project": (existing.get("project") or {}).get("name"),
                "planned_mutations": [],
            }
    mutation = {"operation": "CreateIssue", "graphql_operation_name": "CreateIssue", "variables": {"input": {
        "teamId": str(team["id"]), "title": args.title, "description": description,
        **({"stateId": str(target_state["id"])} if target_state else {}),
        **({"labelIds": [str(label["id"])]} if label else {}),
        **({"projectId": project_id} if project_id else {}),
    }}}
    if mode == "observe":
        return {"ok": True, "mode": mode, "decision": "observe (would accept)",
                "would_accept": True, "diff": {"has_changes": True, "issue_create": True, "fields": ["title", "description"]},
                "planned_mutations": [mutation]}
    if client is None:
        raise LinearSyncError("LINEAR_API_KEY is not configured")
    issue = client.create_issue(team_id=str(team["id"]), title=args.title, description=description,
                                state_id=str(target_state["id"]) if target_state else None,
                                label_ids=[str(label["id"])] if label else None, project_id=project_id)
    return {
        "identifier": issue.get("identifier"),
        "url": issue.get("url"),
        "state": issue.get("state", {}).get("name"),
        "id": issue.get("id"),
        "project": (issue.get("project") or {}).get("name") or (project_name if project_id else None),
    }


def cmd_ensure_goal(args: argparse.Namespace) -> dict[str, Any]:
    handoff_dir = Path(args.handoff_dir).expanduser().resolve()
    mode, skipped = resolve_mode(getattr(args, "mode", None), subcommand="ensure-goal", handoff_dir=handoff_dir)
    if mode == "off":
        return {"schema_version": 1, "ok": True, "command": "ensure-goal", "mode": "off",
                "decision": "off (skipped)", "would_accept": False, "skipped": skipped or "mode_off",
                "planned_mutations": []}
    snapshot = _offline_snapshot(getattr(args, "offline_snapshot", ""))
    create_as_user = getattr(args, "create_as_user", False)
    if mode == "observe":
        if not handoff_dir.is_dir():
            raise LinearSyncError(f"Missing handoff directory: {handoff_dir}")
        team, issue = snapshot or (_synthetic_team(), {})
        client = GraphQLClient("offline", lambda q, v: {"data": {}})
        ledger_text = _read_ledger(handoff_dir)
        actor_name = create_as_user and _ledger_tl(ledger_text) or None
        identifier = args.issue or _bound_issue(ledger_text)
        issue = issue or _virtual_issue(identifier)
        if identifier and not issue.get("identifier"):
            issue["identifier"] = identifier
        goal_name, _, _, _ = _load_handoff(handoff_dir)
        planned: list[dict[str, Any]] = []
        local_effects: list[dict[str, Any]] = []
        if args.issue and _bound_issue(ledger_text) != args.issue:
            local_effects.append({"operation": "BindIssueLine", "identifier": args.issue, "path": str(handoff_dir / "ledger.md")})
        if not identifier:
            if os.environ.get("GTT_LINEAR_AUTO_CREATE", "1") == "0":
                raise LinearSyncError("No Linear issue is bound and auto_create is disabled")
            project_name = _infer_project_name(getattr(args, "project", ""), ledger_text, str(handoff_dir))
            todo = _state(team, "Todo", "In Progress")
            planned.append({"operation": "CreateIssue", "graphql_operation_name": "CreateIssue", "variables": {"input": {
                "teamId": str(team["id"]), "title": f"[Goal] {goal_name}", "description": "",
                **({"stateId": str(todo["id"])} if todo else {}),
                **({"projectId": _project_id(team, project_name)} if _project_id(team, project_name) else {}),
                **({"createAsUser": actor_name} if actor_name and _oauth_app_enabled() else {}),
            }}})
            local_effects.append({"operation": "BindIssueLine", "identifier": "<created issue>", "path": str(handoff_dir / "ledger.md")})
            identifier = ""
        result = _sync_issue(
            client, issue, team, handoff_dir, project_name=getattr(args, "project", ""),
            mode="observe", create_as_user=actor_name,
        )
        planned = planned + result.get("planned_mutations", [])
        result["planned_mutations"] = planned
        result["diff"]["local_effects"] = local_effects
        if getattr(args, "record_observe", False):
            receipts = handoff_dir / "receipts"
            receipts.mkdir(parents=True, exist_ok=True)
            with (receipts / "linear-observe.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"command": "ensure-goal", "result": result["diff"]}) + "\n")
        return {
            "ok": True, "mode": mode,
            "decision": "observe (would accept)" if planned or local_effects else "observe (no-op)",
            "would_accept": bool(planned or local_effects), "identifier": identifier or None,
            "url": issue.get("url"), "state": (issue.get("state") or {}).get("name"),
            "project": (issue.get("project") or {}).get("name"),
            "diff": result["diff"], "planned_mutations": planned,
        }
    client = _client_or_fail()
    handoff_dir.mkdir(parents=True, exist_ok=True)
    with _queue_lock(handoff_dir):
        ledger_text = _read_ledger(handoff_dir)
        actor_name = create_as_user and _ledger_tl(ledger_text) or None
        identifier = args.issue or _bound_issue(ledger_text)
        team = client.team(args.team)
        if identifier:
            issue = client.issue(identifier)
            if args.issue and _bound_issue(ledger_text) != identifier:
                _bind_issue_line(handoff_dir, identifier, str(issue.get("url") or ""))
        else:
            if os.environ.get("GTT_LINEAR_AUTO_CREATE", "1") == "0":
                raise LinearSyncError("No Linear issue is bound and auto_create is disabled")
            goal_name, _, _, _ = _load_handoff(handoff_dir)
            project_name = _infer_project_name(getattr(args, "project", ""), ledger_text, str(handoff_dir))
            todo = _state(team, "Todo", "In Progress")
            issue = client.create_issue(
                team_id=str(team["id"]),
                title=f"[Goal] {goal_name}",
                description="",
                state_id=str(todo["id"]) if todo else None,
                project_id=_project_id(team, project_name),
                create_as_user=actor_name,
            )
            _bind_issue_line(handoff_dir, str(issue["identifier"]), str(issue.get("url") or ""))
            identifier = str(issue["identifier"])
        issue = _sync_issue(
            client,
            issue,
            team,
            handoff_dir,
            project_name=getattr(args, "project", ""),
            create_as_user=actor_name,
        )
    return {
        "ok": True,
        "mode": mode,
        "decision": "accept" if issue.get("_mutations_executed") else "no-op",
        "would_accept": bool(issue.get("_mutations_executed")),
        "identifier": identifier,
        "url": issue.get("url"),
        "state": issue.get("state", {}).get("name"),
        "project": (issue.get("project") or {}).get("name"),
    }


def cmd_sync_ledger(args: argparse.Namespace) -> dict[str, Any]:
    handoff_dir = Path(args.handoff_dir).expanduser().resolve()
    mode, skipped = resolve_mode(getattr(args, "mode", None), subcommand="sync-ledger", handoff_dir=handoff_dir, quiet=getattr(args, "quiet", False))
    if mode == "off":
        if skipped == "isolated_handoff_dir" and not getattr(args, "mode", None) and not os.environ.get("GTT_LINEAR_MODE"):
            return {"ok": True, "skipped": skipped}
        return {"schema_version": 1, "ok": True, "command": "sync-ledger", "mode": "off",
                "decision": "off (skipped)", "would_accept": False, "skipped": skipped or "mode_off",
                "planned_mutations": []}
    snapshot = _offline_snapshot(getattr(args, "offline_snapshot", ""))
    create_as_user = getattr(args, "create_as_user", False)
    if mode == "observe" and not snapshot:
        client = GraphQLClient("offline", lambda q, v: {"data": {}})
        team, issue = _synthetic_team(), {}
    elif snapshot:
        client = GraphQLClient("offline", lambda q, v: {"data": {}})
        team, issue = snapshot
    else:
        client = _client_or_fail()
    lock = contextlib.nullcontext() if mode == "observe" else _queue_lock(handoff_dir)
    with lock:
        ledger_text = _read_ledger(handoff_dir)
        actor_name = create_as_user and _ledger_tl(ledger_text) or None
        identifier = _bound_issue(ledger_text)
        if mode != "observe" and not snapshot:
            team = client.team("TIA")
        if not identifier:
            if os.environ.get("GTT_LINEAR_AUTO_CREATE", "1") == "0":
                raise LinearSyncError("No Linear issue is bound and auto_create is disabled")
            goal_name, _, _, _ = _load_handoff(handoff_dir)
            project_name = _infer_project_name(getattr(args, "project", ""), ledger_text, str(handoff_dir))
            todo = _state(team, "Todo", "In Progress")
            create_mutation = {"operation": "CreateIssue", "graphql_operation_name": "CreateIssue", "variables": {"input": {
                "teamId": str(team["id"]), "title": f"[Goal] {goal_name}", "description": "",
                **({"stateId": str(todo["id"])} if todo else {}),
                **({"projectId": _project_id(team, project_name)} if _project_id(team, project_name) else {}),
                **({"createAsUser": actor_name} if actor_name and _oauth_app_enabled() else {}),
            }}}
            if mode == "observe":
                issue = _virtual_issue()
                planned_create = create_mutation
            else:
                issue = client.create_issue(
                    team_id=str(team["id"]),
                    title=f"[Goal] {goal_name}",
                    description="",
                    state_id=str(todo["id"]) if todo else None,
                    project_id=_project_id(team, project_name),
                    create_as_user=actor_name,
                )
                _bind_issue_line(handoff_dir, str(issue["identifier"]), str(issue.get("url") or ""))
                identifier = str(issue["identifier"])
        elif not snapshot and mode != "observe":
            issue = client.issue(identifier)
        if (snapshot or mode == "observe") and not issue:
            issue = _virtual_issue(identifier or "TIA-OFFLINE")
        issue = _sync_issue(
            client,
            issue,
            team,
            handoff_dir,
            event_note=args.event_note,
            project_name=getattr(args, "project", ""),
            mode=mode,
            create_as_user=actor_name,
        )
        if mode == "observe" and not identifier:
            issue.setdefault("planned_mutations", []).insert(0, planned_create)
            issue.setdefault("diff", {}).setdefault("local_effects", []).append(
                {"operation": "BindIssueLine", "identifier": "<created issue>", "path": str(handoff_dir / "ledger.md")}
            )
    if mode == "observe" and (handoff_dir.joinpath("receipts").is_dir() or getattr(args, "record_observe", False)):
        handoff_dir.joinpath("receipts").mkdir(parents=True, exist_ok=True)
        path = handoff_dir / "receipts" / "linear-observe.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"command": "sync-ledger", "result": issue.get("diff", {})}, ensure_ascii=False) + "\n")
    return {
        "ok": True,
        "mode": mode,
        "decision": "observe (would accept)" if mode == "observe" and (issue.get("planned_mutations") or issue.get("diff", {}).get("local_effects")) else ("observe (no-op)" if mode == "observe" else ("accept" if issue.get("_mutations_executed") else "no-op")),
        "would_accept": bool(issue.get("planned_mutations") or issue.get("diff", {}).get("local_effects")) if mode == "observe" else bool(issue.get("_mutations_executed")),
        **({"diff": issue.get("diff"), "planned_mutations": issue.get("planned_mutations", [])} if mode == "observe" else {}),
        "identifier": issue.get("identifier"),
        "url": issue.get("url"),
        "state": issue.get("state", {}).get("name"),
        "project": (issue.get("project") or {}).get("name"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="linear-sync.py", description="Sync team-lead ledgers with Linear.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    idea = subparsers.add_parser("idea", help="Capture an idea in the Linear team inbox.")
    idea.add_argument("--title", required=True)
    idea.add_argument("--description", default="")
    idea.add_argument("--team", default="TIA")
    idea.add_argument("--state", default="Triage")
    idea.add_argument("--label", default="")
    idea.add_argument("--project", default="")
    idea.add_argument("--mode", choices=("off", "observe", "auto"))
    idea.add_argument("--source", default="")
    idea.add_argument("--dedup-key", default="")
    idea.add_argument("--repo", default="")
    idea.add_argument("--offline-snapshot", default="")

    ensure = subparsers.add_parser("ensure-goal", help="Bind or create the Linear issue for a Goal.")
    ensure.add_argument("--handoff-dir", required=True)
    ensure.add_argument("--issue", default="")
    ensure.add_argument("--team", default="TIA")
    ensure.add_argument("--project", default="")
    ensure.add_argument("--mode", choices=("off", "observe", "auto"))
    ensure.add_argument("--offline-snapshot", default="")
    ensure.add_argument("--record-observe", action="store_true")
    ensure.add_argument("--create-as-user", action="store_true")

    sync = subparsers.add_parser("sync-ledger", help="Synchronize a Goal ledger to its Linear issue.")
    sync.add_argument("--handoff-dir", required=True)
    sync.add_argument("--event-note", default="")
    sync.add_argument("--quiet", action="store_true")
    sync.add_argument("--project", default="")
    sync.add_argument("--mode", choices=("off", "observe", "auto"))
    sync.add_argument("--offline-snapshot", default="")
    sync.add_argument("--record-observe", action="store_true")
    sync.add_argument("--create-as-user", action="store_true")

    list_issues = subparsers.add_parser("list-issues", help="List issues on a Linear team.")
    list_issues.add_argument("--team", default="TIA")
    list_issues.add_argument("--state", default="")
    list_issues.add_argument("--project", default="")
    list_issues.add_argument("--query", default="")
    list_issues.add_argument("--limit", type=int, default=25)

    get_issue = subparsers.add_parser("get-issue", help="Show a Linear issue.")
    get_issue.add_argument("--id", required=True)

    update = subparsers.add_parser("update-issue", help="Update a Linear issue.")
    update.add_argument("--id", required=True)
    update.add_argument("--title")
    update.add_argument("--description")
    update.add_argument("--state")
    update.add_argument("--project")
    update.add_argument("--priority", type=int, choices=range(5))
    update.add_argument("--add-label", action="append", default=[])
    update.add_argument("--mode", choices=("off", "observe", "auto"))

    comment = subparsers.add_parser("comment", help="Comment on a Linear issue.")
    comment.add_argument("--id", required=True)
    comment.add_argument("--body", required=True)
    comment.add_argument("--create-as-user")
    comment.add_argument("--mode", choices=("off", "observe", "auto"))

    auth = subparsers.add_parser("auth", help="Manage Linear credentials.")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    auth_set = auth_sub.add_parser("set")
    auth_set.add_argument("--key-stdin", action="store_true")
    auth_set.add_argument("--header", default="Authorization")
    oauth_app = auth_sub.add_parser("oauth-app")
    oauth_app.add_argument("--key-stdin", action="store_true")
    oauth_app.add_argument("--header", default="Authorization")
    oauth_login = auth_sub.add_parser("oauth-login")
    oauth_login.add_argument("--client-id", default="")
    oauth_login.add_argument("--client-secret", default="")
    oauth_login.add_argument("--port", type=int, default=8765)
    auth_sub.add_parser("refresh")
    auth_sub.add_parser("show")
    auth_remove = auth_sub.add_parser("remove")
    auth_remove.add_argument("--force", action="store_true")
    return parser


def _oauth_interactive_login(client_id: str, client_secret: str, port: int = 8765) -> dict[str, Any]:
    import http.server
    import subprocess

    record = _credential_record()
    cid = (client_id or str(record.get("clientId") or "")).strip()
    csec = (client_secret or str(record.get("clientSecret") or "")).strip()
    if not (cid and csec):
        raise LinearSyncError("oauth-login requires --client-id and --client-secret (or saved clientId/clientSecret)")
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    auth_url = f"{OAUTH_AUTHORIZE_URL}?" + urlencode({
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "read,write,issues:create,comments:create",
        "actor": "app",
    })
    result: dict[str, Any] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            code = (parse_qs(parsed.query).get("code") or [""])[0]
            if not code:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Missing code")
                return
            data = urlencode({
                "grant_type": "authorization_code",
                "client_id": cid,
                "client_secret": csec,
                "redirect_uri": redirect_uri,
                "code": code,
            }).encode("utf-8")
            req = Request(
                OAUTH_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urlopen(req, timeout=15) as resp:
                tok = json.loads(resp.read().decode("utf-8"))
            access_token = str(tok.get("access_token") or "").strip()
            refresh_token = str(tok.get("refresh_token") or "").strip() or None
            expires_in = tok.get("expires_in")
            expires_at = (
                datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + int(expires_in), tz=timezone.utc).isoformat()
                if isinstance(expires_in, (int, float))
                else None
            )
            header_val = f"Bearer {access_token}"
            saved = save_credentials(
                header_val,
                "Authorization",
                actor="app",
                refresh_token=refresh_token,
                client_id=cid,
                client_secret=csec,
                expires_at=expires_at,
            )
            result.update({
                "ok": True,
                "command": "auth oauth-login",
                "path": str(saved),
                "mode": "0600",
                "masked_key": mask_value(header_val),
                "actor": "app",
                "has_refresh_token": bool(refresh_token),
                "expires_at": expires_at,
            })
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("✅ Linear GTT Bot (actor=app) 授权成功（已保存 refresh_token 自动续期）！可以关闭此页面。".encode("utf-8"))

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 120
    subprocess.run(["open", auth_url], check=False)
    while "ok" not in result:
        server.handle_request()
        if "ok" in result:
            break
    server.server_close()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "idea":
            payload = cmd_idea(args)
        elif args.subcommand == "ensure-goal":
            payload = cmd_ensure_goal(args)
        elif args.subcommand == "sync-ledger":
            payload = cmd_sync_ledger(args)
        elif args.subcommand == "list-issues":
            payload = cmd_list_issues(args)
        elif args.subcommand == "get-issue":
            payload = cmd_get_issue(args)
        elif args.subcommand == "update-issue":
            payload = cmd_update_issue(args)
        elif args.subcommand == "comment":
            payload = cmd_comment(args)
        elif args.subcommand == "auth":
            path = _credential_path()
            if args.auth_command in {"set", "oauth-app"}:
                key = sys.stdin.readline().strip() if args.key_stdin or not sys.stdin.isatty() else getpass.getpass("Linear API key: ")
                actor = "app" if args.auth_command == "oauth-app" else None
                saved = save_credentials(key, args.header, actor=actor)
                payload = {"ok": True, "command": f"auth {args.auth_command}", "path": str(saved), "mode": "0600", "masked_key": mask_value(key), **({"actor": actor} if actor else {})}
            elif args.auth_command == "oauth-login":
                payload = _oauth_interactive_login(args.client_id, args.client_secret, args.port)
            elif args.auth_command == "refresh":
                refreshed = refresh_oauth_credentials()
                if not refreshed:
                    raise LinearSyncError("Unable to refresh OAuth token (missing refreshToken/clientId/clientSecret or refresh rejected)")
                rec = _credential_record()
                payload = {
                    "ok": True,
                    "command": "auth refresh",
                    "path": str(path),
                    "masked_key": mask_value(refreshed),
                    "actor": rec.get("actor"),
                    "has_refresh_token": bool(rec.get("refreshToken")),
                    "expires_at": rec.get("expiresAt"),
                }
            elif args.auth_command == "show":
                key, source, source_path = load_credential_info()
                rec = _credential_record()
                payload = {"ok": True, "command": "auth show", "configured": bool(key),
                           "source": source, "path": str(source_path or path), "masked_key": mask_value(key or ""),
                           **({"actor": rec["actor"]} if rec.get("actor") else {}),
                           "has_refresh_token": bool(rec.get("refreshToken")),
                           **({"expires_at": rec["expiresAt"]} if rec.get("expiresAt") else {}),
                           "mode": oct((source_path or path).stat().st_mode & 0o777)[2:].zfill(4) if (source_path or path).exists() else None}
            else:
                removed = False
                if _has_symlink_component(path):
                    raise LinearSyncError(f"Refusing symlinked credential path: {path}")
                if path.exists():
                    path.unlink()
                    removed = True
                payload = {"ok": True, "command": "auth remove", "removed": removed}
        else:
            raise LinearSyncError(f"Unknown subcommand: {args.subcommand}")
    except LinearSyncError as exc:
        if args.subcommand == "sync-ledger" and args.quiet:
            return 0
        sys.stderr.write(f"linear-sync: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
