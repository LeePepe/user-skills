#!/usr/bin/env python3
"""Synchronize goal-task handoffs with a Linear issue.

The script deliberately uses only the Python standard library.  It is safe to
run from queue hooks because ``sync-ledger --quiet`` is fail-open.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://api.linear.app/graphql"
MARKER_START = "<!-- GTT-LINEAR-SYNC:START -->"
MARKER_END = "<!-- GTT-LINEAR-SYNC:END -->"
KEY_RE = re.compile(r"^\s*(?:export\s+)?LINEAR_API_KEY\s*=\s*(['\"]?)([^'\"#\s]+)\1\s*$", re.MULTILINE)
ISSUE_RE = re.compile(r"(?m)^\s*-\s*Linear:\s*([A-Z][A-Z0-9]*-\d+)(?:\s+\(([^)]+)\))?")
EVENT_RE = re.compile(r"(?m)^\s*-\s+[^|]+\s+\|\s+(.*)$")
PR_RE = re.compile(r"(?:github\.com/.+/(?:pull|pulls)/\d+|PR(?:\s*#|=)\s*\d+)", re.IGNORECASE)


class LinearSyncError(RuntimeError):
    """Expected, user-facing Linear synchronization failure."""


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
    """Read the key from the environment, then the standard zsh startup files."""
    value = os.environ.get("LINEAR_API_KEY", "").strip()
    if value:
        return value
    for path in (Path("~/.zshenv").expanduser(), Path("~/.zshrc").expanduser()):
        value = _read_key_file(path)
        if value:
            return value
    return None


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

    def _request(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
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
        fields = """
          id identifier url title description
          state { id name type }
          team { id key name }
          project { id name }
          labels { nodes { id name } }
          comments { nodes { id body } }
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
        data = self.execute(
            """
            mutation CreateIssue($input: IssueCreateInput!) {
              issueCreate(input: $input) {
                success
                issue {
                  id identifier url title description
                  state { id name type }
                  team { id key name }
                  project { id name }
                  labels { nodes { id name } }
                  comments { nodes { id body } }
                }
              }
            }
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
    ) -> dict[str, Any]:
        input_data: dict[str, Any] = {}
        if description is not None:
            input_data["description"] = description
        if state_id is not None:
            input_data["stateId"] = state_id
        if label_ids is not None:
            input_data["labelIds"] = label_ids
        if project_id is not None:
            input_data["projectId"] = project_id
        data = self.execute(
            """
            mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
              issueUpdate(id: $id, input: $input) {
                success
                issue {
                  id identifier url title description
                  state { id name type }
                  team { id key name }
                  project { id name }
                  labels { nodes { id name } }
                  comments { nodes { id body } }
                }
              }
            }
            """,
            {"id": issue_id, "input": input_data},
        )
        result = data.get("issueUpdate", {})
        if not result.get("success") or not result.get("issue"):
            raise LinearSyncError("Linear did not update the issue")
        return result["issue"]

    def create_comment(self, issue_id: str, body: str) -> None:
        data = self.execute(
            """
            mutation CreateComment($input: CommentCreateInput!) {
              commentCreate(input: $input) { success comment { id } }
            }
            """,
            {"input": {"issueId": issue_id, "body": body}},
        )
        if not data.get("commentCreate", {}).get("success"):
            raise LinearSyncError("Linear did not create the comment")


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
    combined = f"{text} {repo_text}".casefold()
    if any(keyword in combined for keyword in (
        "bing", "sapphire", "flightbook", "oneauth", "serp", "fastlane",
        "dsat", "cer", "msn", "san sa",
    )):
        return "Bing"
    if any(keyword in combined for keyword in (
        "user-skills", "workflow", "goal-task", "linear-board", "bot-tl",
        "retro-loop", "story-craft", "herdr", "clockwork", "cron", "6dq",
        "shared-ci", "skill",
    )):
        return "Agent Workflow"
    if any(keyword in combined for keyword in ("vitalstride", "voxpocket", "aidash", "localis")):
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


def _active_blocking_events(events: list[dict[str, str]]) -> list[dict[str, str]]:
    blocking_types = {
        "TASK_BLOCKED",
        "INPUT_BLOCKED",
        "REVIEW_FAIL",
        "NEED_GO",
        "NEED_OWNER_DECISION",
    }
    active: dict[str, dict[str, str]] = {}
    unkeyed: list[dict[str, str]] = []
    for event in events:
        event_type = event.get("EVT", "").upper()
        event_id = event.get("id", "")
        if event_type in blocking_types:
            if event_id:
                active[event_id] = event
            else:
                unkeyed.append(event)
        elif event_type in {"RESOLVED", "ACK"} and event_id:
            active.pop(event_id, None)
    return [*active.values(), *unkeyed]


def _parse_tasks(ledger_text: str, queue_data: dict[str, Any] | None) -> list[dict[str, str]]:
    queue_by_id = {
        str(task.get("task_id")): task
        for task in (queue_data or {}).get("tasks", [])
        if task.get("task_id")
    }
    tasks: list[dict[str, str]] = []
    in_table = False
    for line in ledger_text.splitlines():
        if line.strip() == "## Tasks":
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.startswith("|") or line.count("|") < 6:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells[0] in {"ID", "---"} or cells[0].startswith("---"):
            continue
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


def _description_block(
    goal_name: str,
    ledger_text: str,
    tasks: list[dict[str, str]],
) -> str:
    header_lines: list[str] = []
    for line in ledger_text.splitlines():
        if line.startswith("## Tasks"):
            break
        if line.startswith("- ") and ":" in line:
            header_lines.append(line)
    summary = "\n".join(header_lines) or f"- Goal: {goal_name}"
    checklist: list[str] = []
    for task in tasks:
        status = task["status"]
        checked = "x" if status == "done" else " "
        suffix = status
        if task["pr"] and task["pr"] not in {"—", "-"}:
            suffix += f", {task['pr']}"
        checklist.append(f"- [{checked}] {task['task_id']}: {task['title']} ({suffix})")
    return (
        MARKER_START
        + "\n## Goal Summary\n"
        + summary
        + "\n\n## Tasks\n"
        + ("\n".join(checklist) if checklist else "- [ ] No tasks recorded")
        + "\n"
        + MARKER_END
    )


def _updated_description(existing: str | None, block: str) -> str:
    current = existing or ""
    start = current.find(MARKER_START)
    if start < 0:
        return (current.rstrip() + "\n\n" + block).lstrip()
    end = current.find(MARKER_END, start + len(MARKER_START))
    if end < 0:
        return current[:start].rstrip() + "\n\n" + block
    end += len(MARKER_END)
    return current[:start].rstrip() + "\n\n" + block + current[end:]


def _aggregate_state(team: dict[str, Any], tasks: list[dict[str, str]], events: list[dict[str, str]], header: dict[str, str]) -> dict[str, Any] | None:
    event_names = {event.get("EVT", "").upper() for event in events}
    header_status = header.get("status", "").casefold()
    if "GOAL_DONE" in event_names or (tasks and all(task["status"] in {"done", "cancelled"} for task in tasks) and any(task["status"] == "done" for task in tasks)):
        return _state(team, "Done")
    if header_status == "cancelled" or "GOAL_CANCELLED" in event_names or "CANCELLED" in event_names:
        return _state(team, "Canceled")
    statuses = [task["status"] for task in tasks]
    if any(status in {"in_progress", "changes_requested", "blocked"} for status in statuses):
        return _state(team, "In Progress")
    if any(status == "in_review" for status in statuses) and all(status in {"in_review", "done", "cancelled"} for status in statuses):
        return _state(team, "In Review", "In Progress")
    if statuses and all(status in {"planned", "ready"} for status in statuses):
        return _state(team, "Todo")
    return _state(team, "Todo", "In Progress")


def _issue_comment_bodies(issue: dict[str, Any]) -> list[str]:
    return [str(node.get("body", "")) for node in issue.get("comments", {}).get("nodes", [])]


def _read_ledger(handoff_dir: Path) -> str:
    ledger_path = handoff_dir / "ledger.md"
    try:
        return ledger_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LinearSyncError(f"Unable to read ledger: {ledger_path}: {exc}") from exc


def _sync_issue(
    client: GraphQLClient,
    issue: dict[str, Any],
    team: dict[str, Any],
    handoff_dir: Path,
    *,
    event_note: str | None = None,
    project_name: str | None = None,
) -> dict[str, Any]:
    ledger_text = _read_ledger(handoff_dir)
    goal_name, tasks, events, header = _load_handoff(handoff_dir)
    inferred_project = _infer_project_name(project_name, ledger_text, str(handoff_dir))
    project_id = _project_id(team, inferred_project)
    current_project_id = str((issue.get("project") or {}).get("id") or "")
    explicit_project = bool(project_name and project_name.strip())
    description = _updated_description(
        issue.get("description"),
        _description_block(goal_name, ledger_text, tasks),
    )
    target_state = _aggregate_state(team, tasks, events, header)
    labels = [str(node["id"]) for node in issue.get("labels", {}).get("nodes", []) if node.get("id")]
    blocking = _active_blocking_events(events)
    blocked_label = _label(team, "Blocked") or _label(team, "NEED_GO")
    if blocking and blocked_label and blocked_label.get("id") not in labels:
        labels.append(str(blocked_label["id"]))
    elif not blocking and blocked_label:
        labels = [label_id for label_id in labels if label_id != str(blocked_label["id"])]
    issue = client.update_issue(
        str(issue["id"]),
        description=description,
        state_id=str(target_state["id"]) if target_state else None,
        label_ids=labels,
        project_id=project_id
        if project_id and (explicit_project or not current_project_id) and project_id != current_project_id
        else None,
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
            client.create_comment(str(issue["id"]), body)
            existing_comments.append(body)
    for event in events:
        event_type = event.get("EVT", "").upper()
        if event_type not in {"PR_CREATED", "DONE", "GOAL_DONE"} and not PR_RE.search(
            f"{event.get('ref', '')} {event.get('note', '')}"
        ):
            continue
        event_id = event.get("id", "")
        marker = f"<!-- GTT-LINEAR-MILESTONE:{event_id} -->"
        if event_id and not any(marker in body for body in existing_comments):
            body = f"{marker}\n**{event_type}**"
            if event.get("task"):
                body += f" · Task `{event['task']}`"
            if event.get("ref"):
                body += f"\n{event['ref']}"
            client.create_comment(str(issue["id"]), body)
            existing_comments.append(body)
    if event_note:
        marker = f"<!-- GTT-LINEAR-NOTE:{event_note} -->"
        if not any(marker in body for body in existing_comments):
            client.create_comment(str(issue["id"]), f"{marker}\n{event_note}")
            existing_comments.append(marker)
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
    path.write_text(text, encoding="utf-8")


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


def cmd_idea(args: argparse.Namespace) -> dict[str, Any]:
    client = _client_or_fail()
    team = client.team(args.team)
    project_name = _infer_project_name(
        getattr(args, "project", ""),
        f"{args.title} {args.description}",
        getattr(args, "repo", ""),
    )
    project_id = _project_id(team, project_name)
    target_state = _state(team, args.state, "Backlog")
    label = _label(team, args.label) if args.label else None
    issue = client.create_issue(
        team_id=str(team["id"]),
        title=args.title,
        description=args.description or "",
        state_id=str(target_state["id"]) if target_state else None,
        label_ids=[str(label["id"])] if label else None,
        project_id=project_id,
    )
    return {
        "identifier": issue.get("identifier"),
        "url": issue.get("url"),
        "state": issue.get("state", {}).get("name"),
        "id": issue.get("id"),
        "project": (issue.get("project") or {}).get("name") or (project_name if project_id else None),
    }


def cmd_ensure_goal(args: argparse.Namespace) -> dict[str, Any]:
    handoff_dir = Path(args.handoff_dir).expanduser().resolve()
    handoff_dir.mkdir(parents=True, exist_ok=True)
    client = _client_or_fail()
    with _queue_lock(handoff_dir):
        ledger_text = _read_ledger(handoff_dir)
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
            )
            _bind_issue_line(handoff_dir, str(issue["identifier"]), str(issue.get("url") or ""))
            identifier = str(issue["identifier"])
        issue = _sync_issue(
            client,
            issue,
            team,
            handoff_dir,
            project_name=getattr(args, "project", ""),
        )
    return {
        "ok": True,
        "identifier": identifier,
        "url": issue.get("url"),
        "state": issue.get("state", {}).get("name"),
        "project": (issue.get("project") or {}).get("name"),
    }


def cmd_sync_ledger(args: argparse.Namespace) -> dict[str, Any]:
    handoff_dir = Path(args.handoff_dir).expanduser().resolve()
    if (
        getattr(args, "quiet", False)
        and _is_isolated_handoff_dir(handoff_dir)
        and os.environ.get("GTT_LINEAR_SYNC") != "1"
    ):
        return {"ok": True, "skipped": "isolated_handoff_dir"}
    client = _client_or_fail()
    with _queue_lock(handoff_dir):
        ledger_text = _read_ledger(handoff_dir)
        identifier = _bound_issue(ledger_text)
        team = client.team("TIA")
        if not identifier:
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
            )
            _bind_issue_line(handoff_dir, str(issue["identifier"]), str(issue.get("url") or ""))
            identifier = str(issue["identifier"])
        else:
            issue = client.issue(identifier)
        issue = _sync_issue(
            client,
            issue,
            team,
            handoff_dir,
            event_note=args.event_note,
            project_name=getattr(args, "project", ""),
        )
    return {
        "ok": True,
        "identifier": issue.get("identifier"),
        "url": issue.get("url"),
        "state": issue.get("state", {}).get("name"),
        "project": (issue.get("project") or {}).get("name"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="linear-sync.py", description="Sync goal-task ledgers with Linear.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    idea = subparsers.add_parser("idea", help="Capture an idea in the Linear team inbox.")
    idea.add_argument("--title", required=True)
    idea.add_argument("--description", default="")
    idea.add_argument("--team", default="TIA")
    idea.add_argument("--state", default="Triage")
    idea.add_argument("--label", default="")
    idea.add_argument("--project", default="")

    ensure = subparsers.add_parser("ensure-goal", help="Bind or create the Linear issue for a Goal.")
    ensure.add_argument("--handoff-dir", required=True)
    ensure.add_argument("--issue", default="")
    ensure.add_argument("--team", default="TIA")
    ensure.add_argument("--project", default="")

    sync = subparsers.add_parser("sync-ledger", help="Synchronize a Goal ledger to its Linear issue.")
    sync.add_argument("--handoff-dir", required=True)
    sync.add_argument("--event-note", default="")
    sync.add_argument("--quiet", action="store_true")
    sync.add_argument("--project", default="")
    return parser


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
