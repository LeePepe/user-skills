from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .shared import *

def _load_linear_token() -> str:
    env_val = os.environ.get("LINEAR_API_KEY", "").strip()
    if env_val:
        return env_val
    for raw_path in (
        os.environ.get("GTT_LINEAR_CREDENTIALS_FILE", "~/.config/linear/credentials.json"),
        "~/.clip/linear/credentials.json",
    ):
        path = Path(raw_path).expanduser()
        try:
            if path.is_symlink():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, Mapping):
                val = str(data.get("headerValue") or data.get("key") or "").strip()
                if val:
                    return val
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    key_re = re.compile(r"^\s*(?:export\s+)?LINEAR_API_KEY\s*=\s*(['\"]?)([^'\"#\s]+)\1\s*$", re.MULTILINE)
    for rc in ("~/.zshenv", "~/.zshrc"):
        try:
            match = key_re.search(Path(rc).expanduser().read_text(encoding="utf-8"))
            if match:
                return match.group(2).strip()
        except OSError:
            continue
    return ""


def _linear_query(token: str, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        os.environ.get("LINEAR_API_URL", "https://api.linear.app/graphql"),
        data=body,
        headers={"Content-Type": "application/json", "Authorization": token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise LinearSyncError(str(exc)) from exc
    if result.get("errors"):
        raise LinearSyncError(str(result["errors"]))
    return result.get("data", {})

def sync_linear(
    roots: Sequence[Mapping[str, Any]],
    *,
    cache_path: Path = DEFAULT_CACHE,
    dry_run: bool = False,
    offline: bool = False,
    client=None,
    child_items: Sequence[Mapping[str, Any]] = (),
    snapshot_roots: Mapping[str, Mapping[str, Any]] | None = None,
    teams_timeline_path: Path = DEFAULT_TEAMS_TIMELINE,
    confirm_teams_timeline: bool = False,
) -> dict[str, Any]:
    """Upsert Linear roots and exactly three workstream children; never write sources."""
    desired = []
    fallback_timeline = load_teams_timeline(teams_timeline_path, confirm=confirm_teams_timeline)
    cached_ws_by_root: dict[str, dict[str, dict[str, Any]]] = {}
    if not dry_run and cache_path.is_file():
        cached_doc = _read_json(cache_path, {})
        for cached_root in (cached_doc.get("roots") or []) if isinstance(cached_doc, Mapping) else []:
            if not isinstance(cached_root, Mapping) or not cached_root.get("root_id"):
                continue
            rid = str(cached_root["root_id"])
            cached_ws_by_root[rid] = {
                str(w.get("stream")): dict(w["linear"])
                for w in (cached_root.get("workstreams") or [])
                if isinstance(w, Mapping) and w.get("stream") and isinstance(w.get("linear"), Mapping)
            }
    for root in roots:
        rid = str(root["root_id"])
        snap_root = dict((snapshot_roots or {}).get(rid) or {})
        if rid == str(fallback_timeline.get("root_id", "china-release")) and not snap_root.get("teams_timeline"):
            snap_root["teams_timeline"] = fallback_timeline
        if rid in cached_ws_by_root and not snap_root.get("workstream_links"):
            snap_root["workstream_links"] = cached_ws_by_root[rid]
        streams = snap_root.get("workstreams") if isinstance(snap_root, Mapping) else {}
        streams = streams if isinstance(streams, Mapping) else {}
        desired.append({
            "root_id": rid,
            "name": root.get("name", rid),
            "label": "root-tracker",
            "team": "TIA",
            "teams_timeline": snap_root.get("teams_timeline"),
            "_snap_root": snap_root,
            "_root_def": dict(root),
            "description": build_root_markdown(root, snap_root),
            "workstreams": [
                {
                    "stream": stream,
                    "title": _workstream_title(str(root.get("name", rid)), stream),
                    "description": _workstream_markdown(root, stream, (streams.get(stream) or {}).get("items") or []),
                    "items": [dict(item) for item in (streams.get(stream) or {}).get("items", []) if isinstance(item, Mapping)],
                }
                for stream in WORKSTREAMS
            ],
        })
    if dry_run or offline or client is None:
        for record in desired:
            record.pop("_snap_root", None)
            record.pop("_root_def", None)
        result = {"mode": "dry-run" if dry_run else "offline", "writes": [], "roots": desired}
        if not dry_run:
            _write_json(cache_path, {"schema_version": 1, "updated_at": utc_now(), "source": "offline", "roots": desired})
        return result
    if getattr(client, "boundary", None) != "linear":
        raise OneWayBoundaryError("Project Root Tracker may write only to Linear")
    for record in desired:
        root_def = record.pop("_root_def", {})
        snap_root = record.pop("_snap_root", {})
        parent = client.upsert_root(record)
        record["linear"] = parent
        if hasattr(client, "deduplicate_root_children"):
            client.deduplicate_root_children(parent, str(record.get("name") or record["root_id"]))
        timeline = record.get("teams_timeline")
        if str(record.get("root_id")) == "china-release" and _timeline_is_confirmed(timeline) and hasattr(client, "upsert_project_milestone"):
            record["project_milestone"] = client.upsert_project_milestone("china-release", str(record.get("name") or "China Release"), timeline)
        for workstream in record["workstreams"]:
            workstream["linear"] = client.upsert_workstream(parent, record, workstream)
        live_links = {
            str(w["stream"]): dict(w["linear"])
            for w in record["workstreams"]
            if isinstance(w.get("linear"), Mapping) and (w["linear"].get("identifier") or w["linear"].get("url"))
        }
        if live_links:
            updated_desc = build_root_markdown(root_def, snap_root, workstream_links=live_links)
            if updated_desc != record["description"]:
                record["description"] = updated_desc
                record["linear"] = client.upsert_root(record)
        if hasattr(client, "enforce_tia83"):
            client.enforce_tia83(roots)
        if hasattr(client, "ensure_related"):
            for workstream in record["workstreams"]:
                if workstream["stream"] == "PR & PoP":
                    client.ensure_related(workstream["linear"], record["root_id"])
    _write_json(cache_path, {"schema_version": 1, "updated_at": utc_now(), "source": "linear", "roots": desired})
    return {"mode": "linear", "writes": len(desired), "roots": desired}


class LinearClient:
    boundary = "linear"
    PROJECT_BY_ROOT = {
        "china-release": "Work Business",
        "sapphire-notification": "Work Business",
        "sapphire-smartbanner": "Work Business",
    }
    ROOT_LABEL_ID = "04dc1e1a-6803-4590-acd3-4e54b25bb4ae"
    IN_PROGRESS_ID = "d7d9992e-4736-4af0-b5ab-dce211b48bc8"
    CANCELED_ID = "17bbd5ea-a873-4f97-8bad-00885358f408"
    DUPLICATE_ID = "3be8ce2f-ea8b-48e5-aabe-5e2511f05aef"

    def __init__(self, token: str, team_key: str = "TIA") -> None:
        self.token = token
        self.team_key = team_key
        self.team_id: str | None = None
        self.projects: dict[str, dict[str, Any]] | None = None

    def _mutate(self, mutation: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        return _linear_query(self.token, mutation, variables)

    def _team(self) -> str:
        if self.team_id:
            return self.team_id
        data = _linear_query(self.token, "query($key:String!){team(id:$key){id}}", {"key": self.team_key})
        team = data.get("team") or {}
        if not team.get("id"):
            raise LinearSyncError(f"Linear team not found: {self.team_key}")
        self.team_id = team["id"]
        return self.team_id

    def _projects(self) -> dict[str, dict[str, Any]]:
        if self.projects is not None:
            return self.projects
        query = "query($team:String!){team(id:$team){projects{nodes{id name}}}}"
        nodes = _linear_query(self.token, query, {"team": self.team_key}).get("team", {}).get("projects", {}).get("nodes", [])
        self.projects = {str(node.get("name")): node for node in nodes if node.get("id") and node.get("name")}
        return self.projects

    def _project_and_milestone(self, root_id: str, root_name: str) -> tuple[str | None, str | None]:
        project_name = self.PROJECT_BY_ROOT.get(root_id, "Work Business")
        project = self._projects().get(project_name)
        if not project:
            return None, None
        query = "query($id:String!){project(id:$id){projectMilestones{nodes{id name}}}}"
        nodes = _linear_query(self.token, query, {"id": project["id"]}).get("project", {}).get("projectMilestones", {}).get("nodes", [])
        milestone = next((node for node in nodes if str(node.get("name", "")).casefold() == root_name.casefold()), None)
        return str(project["id"]), str(milestone["id"]) if milestone else None

    def upsert_project_milestone(self, root_id: str, root_name: str, timeline: Mapping[str, Any]) -> dict[str, Any]:
        """Update only the confirmed Teams timeline fields on the matching milestone."""
        _, milestone_id = self._project_and_milestone(root_id, root_name)
        if not milestone_id:
            raise LinearSyncError(f"Linear milestone not found: {root_name}")
        input_data = {"description": _timeline_description(timeline)}
        if timeline.get("target_date"):
            input_data["targetDate"] = str(timeline["target_date"])
        mutation = """mutation($id:String!,$input:ProjectMilestoneUpdateInput!){projectMilestoneUpdate(id:$id,input:$input){success projectMilestone{id name targetDate description}}}"""
        result = self._mutate(mutation, {"id": milestone_id, "input": input_data})
        return (result.get("projectMilestoneUpdate") or {}).get("projectMilestone") or result.get("projectMilestoneUpdate", {})

    def deduplicate_root_children(self, parent: Mapping[str, Any], root_name: str) -> dict[str, dict[str, Any]]:
        """Ensure each [Root] issue keeps at most 3 canonical workstream sub-issues."""
        parent_id = parent.get("id")
        if not parent_id:
            return {}
        query = """query($id:String!){issue(id:$id){children(first:50){nodes{id identifier title url createdAt state{id name type}}}}}"""
        try:
            nodes = ((_linear_query(self.token, query, {"id": str(parent_id)}).get("issue") or {}).get("children") or {}).get("nodes") or []
        except LinearSyncError:
            return {}
        by_stream: dict[str, list[dict[str, Any]]] = {stream: [] for stream in WORKSTREAMS}
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            state_type = str((node.get("state") or {}).get("type") or "").casefold()
            if state_type in {"canceled", "duplicate"}:
                continue
            title = str(node.get("title") or "")
            if title == f"[{root_name}] PR 进度与待办" or title.startswith(f"[{root_name} · PR & PoP]"):
                by_stream["PR & PoP"].append(dict(node))
            elif title.startswith(f"[{root_name} · Bugs & Devsync]"):
                by_stream["Bugs & Devsync"].append(dict(node))
            elif title.startswith(f"[{root_name} · Repo & Skill Sinks]"):
                by_stream["Repo & Skill Sinks"].append(dict(node))
        canonical: dict[str, dict[str, Any]] = {}
        for stream, matches in by_stream.items():
            if not matches:
                continue
            matches.sort(key=lambda item: (str(item.get("createdAt") or ""), str(item.get("identifier") or "")))
            canonical[stream] = matches[0]
            for dup in matches[1:]:
                dup_id = dup.get("id")
                if not dup_id:
                    continue
                mutation = """mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success}}"""
                try:
                    self._mutate(mutation, {"id": str(dup_id), "input": {"parentId": None, "stateId": self.CANCELED_ID}})
                except LinearSyncError:
                    continue
        return canonical

    def _find_issue(self, title: str, parent_id: str | None = None) -> dict[str, Any] | None:
        exact_query = """query($teamId:ID!,$title:String!){issues(filter:{team:{id:{eq:$teamId}},title:{eq:$title}},first:50){nodes{id identifier title url description state{type} parent{ id }}}}"""
        prefix_query = """query($teamId:ID!,$prefix:String!){issues(filter:{team:{id:{eq:$teamId}},title:{contains:$prefix}},first:100){nodes{id identifier title url description state{type} parent{ id }}}}"""
        try:
            nodes = _linear_query(self.token, exact_query, {"teamId": self._team(), "title": title}).get("issues", {}).get("nodes", [])
        except LinearSyncError:
            return None
        for node in nodes:
            if str((node.get("state") or {}).get("type") or "").casefold() in {"canceled", "duplicate"}:
                continue
            if parent_id is None or (node.get("parent") or {}).get("id") == parent_id:
                return node
        if not title.endswith("PR 进度与待办"):
            return None
        prefix = title.split("]", 1)[0] + " · PR & PoP]"
        try:
            nodes = _linear_query(self.token, prefix_query, {"teamId": self._team(), "prefix": prefix}).get("issues", {}).get("nodes", [])
        except LinearSyncError:
            return None
        for node in nodes:
            if str((node.get("state") or {}).get("type") or "").casefold() in {"canceled", "duplicate"}:
                continue
            node_title = str(node.get("title") or "")
            if node_title.startswith(prefix) and (parent_id is None or (node.get("parent") or {}).get("id") == parent_id):
                return node
        return None

    @staticmethod
    def _preserve_manual_checks(old_desc: str, new_desc: str) -> str:
        checked_prefixes = set()
        for line in (old_desc or "").splitlines():
            m = re.match(r"^\s*[-*]\s+\[[xX]\]\s+(\*\*\[[^\]]+\]\*\*|\*\*`[^`]+`[^*]*\*\*|`[^`]+`\s+\*\*[^*]+\*\*)", line)
            if m:
                checked_prefixes.add(m.group(1))
        if not checked_prefixes:
            return new_desc
        out_lines = []
        for line in new_desc.splitlines():
            if line.lstrip().startswith("- [ ] "):
                for prefix in checked_prefixes:
                    if prefix in line:
                        line = line.replace("- [ ] ", "- [x] ", 1)
                        break
            out_lines.append(line)
        return "\n".join(out_lines)

    def upsert_root(self, record: Mapping[str, Any]) -> dict[str, Any]:
        title = f"[Root] {record['name']}"
        project_id, milestone_id = self._project_and_milestone(str(record.get("root_id", "")), str(record["name"]))
        existing = self._find_issue(title)
        desc = str(record["description"])
        if existing and existing.get("description"):
            desc = self._preserve_manual_checks(str(existing["description"]), desc)
        input_data = {
            "description": desc,
            "labelIds": [self.ROOT_LABEL_ID],
            "stateId": self.IN_PROGRESS_ID,
            "priority": 2,
        }
        if project_id:
            input_data["projectId"] = project_id
        if milestone_id:
            input_data["projectMilestoneId"] = milestone_id
        if existing and existing.get("id"):
            query = """mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id identifier url}}}"""
            result = self._mutate(query, {"id": existing["id"], "input": input_data})
            return (result.get("issueUpdate") or {}).get("issue") or result.get("issueUpdate", {})
        create_input = {"teamId": self._team(), "title": title, **input_data}
        query = """mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id identifier url}}}"""
        result = self._mutate(query, {"input": create_input})
        return (result.get("issueCreate") or {}).get("issue") or result.get("issueCreate", {})

    def _completed_linear_ids_from_supervisor(self) -> set[str]:
        sup = _merge_discovery(_read_json(DEFAULT_SUPERVISOR, {}), _read_json(DEFAULT_LATEST_DISCOVERY, {}))
        pending = sup.get("pending_decisions") if isinstance(sup, Mapping) else {}
        if not isinstance(pending, Mapping):
            return set()
        return {
            str(item.get("linear_decision_issue_identifier"))
            for item in pending.values()
            if isinstance(item, Mapping)
            and str(item.get("status") or "").casefold() == "completed"
            and item.get("linear_decision_issue_identifier")
        }

    def active_tia83_for_root(self, root_id: str, roots: Sequence[Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
        roots = list(roots or load_roots())
        completed_ids = self._completed_linear_ids_from_supervisor()
        parent = self._tia83()
        active = []
        for child in ((parent.get("children") or {}).get("nodes") or []):
            title = str(child.get("title") or "")
            if not (title.startswith("[PR Action]") or title.startswith("[PR Decision]")):
                continue
            if str((child.get("state") or {}).get("type") or "").casefold() == "completed":
                continue
            if str(child.get("identifier") or "") in completed_ids:
                continue
            if classify_jev(child, roots).get("root_id") == root_id:
                active.append(dict(child))
        return active

    def upsert_workstream(self, parent: Mapping[str, Any], record: Mapping[str, Any], workstream: Mapping[str, Any]) -> dict[str, Any]:
        title = str(workstream["title"])
        project_id, milestone_id = self._project_and_milestone(str(record.get("root_id", "")), str(record.get("name", "")))
        description = str(workstream.get("description") or "")
        if workstream.get("stream") == "PR & PoP":
            live_children = self.active_tia83_for_root(str(record.get("root_id", "")))
            pop_children = [
                child for child in live_children
                if str(child.get("title") or "").startswith("[PR Action]")
                and str((child.get("state") or {}).get("name") or "").casefold() not in {"done", "canceled", "duplicate"}
            ]
            dec_children = [
                child for child in live_children
                if str(child.get("title") or "").startswith("[PR Decision]")
                and str((child.get("state") or {}).get("name") or "").casefold() not in {"done", "canceled", "duplicate"}
            ]
            if pop_children or dec_children:
                base_items = [dict(item) for item in workstream.get("items", []) if isinstance(item, Mapping)]
                live_items: list[dict[str, Any]] = []
                for child in pop_children + dec_children:
                    child_title = str(child.get("title") or "")
                    pr_match = re.search(r"#(\d+)", child_title)
                    pr_num = int(pr_match.group(1)) if pr_match else None
                    sha_match = re.search(r"\(([0-9a-f]{7,40})\)", child_title)
                    description_text = str(child.get("description") or "")
                    description_data = _read_json_from_text(description_text, {})
                    head_match = re.search(r"\*\*Head:\*\*\s*`([^`]+)`", description_text)
                    decision_match = re.search(r"—\s*([A-Za-z0-9_-]+)\s*$", child_title)
                    kind = "owner_action" if child in pop_children else "owner_decision"
                    identifier = child.get("identifier") or child.get("id") or ""
                    clean_title = re.sub(r"^\[PR (?:Action|Decision)\]\s*", "", child_title).strip()
                    live_items.append({
                        "id": f"PR#{pr_num}" if pr_num else identifier,
                        "type": "pr",
                        "pr_number": pr_num,
                        "title": clean_title or child_title,
                        "url": f"https://msft.ghe.com/san/bing-growth-sapphire-app/pull/{pr_num}" if pr_num else child.get("url"),
                        "signal": "WAIT_POP_OR_MERGE" if kind == "owner_action" else "P0_BLOCKING_DECISION",
                        "state": "OPEN",
                        "head_sha": description_data.get("head_sha") or (head_match.group(1) if head_match else (sha_match.group(1) if sha_match else "")),
                        "unresolved_count": description_data.get("unresolved_count", 0),
                        "pending_decisions": [{
                            "kind": kind,
                            "status": "awaiting_owner",
                            "decision_id": description_data.get("decision_id") or (decision_match.group(1) if decision_match else identifier),
                            "linear_identifier": identifier,
                            "url": child.get("url") or f"https://linear.app/tianpeili/issue/{identifier}",
                        }],
                    })
                combined_items = [dict(item) for item in base_items]
                for live_item in live_items:
                    match = next(
                        (item for item in combined_items
                         if str(item.get("pr_number") or "") == str(live_item.get("pr_number") or "")),
                        None,
                    )
                    if match is None:
                        combined_items.append(live_item)
                        continue
                    match.update({
                        "signal": live_item["signal"],
                        "state": live_item["state"],
                        "url": match.get("url") or live_item.get("url"),
                        "head_sha": match.get("head_sha") or live_item.get("head_sha"),
                        "unresolved_count": live_item.get("unresolved_count", match.get("unresolved_count", 0)),
                    })
                    live_decision = live_item["pending_decisions"][0]
                    pending_decisions = [dict(entry) for entry in match.get("pending_decisions", []) if isinstance(entry, Mapping)]
                    existing = next((entry for entry in pending_decisions if entry.get("kind") == live_decision["kind"]), None)
                    if existing is None:
                        pending_decisions.append(live_decision)
                    else:
                        existing.update({key: value for key, value in live_decision.items() if value not in (None, "")})
                    match["pending_decisions"] = pending_decisions
                description = _workstream_markdown(record, "PR & PoP", combined_items)
        input_data: dict[str, Any] = {
            "description": description,
            "parentId": parent.get("id"),
            "title": title,
        }
        if project_id:
            input_data["projectId"] = project_id
        if milestone_id:
            input_data["projectMilestoneId"] = milestone_id
        existing = self._find_issue(title, parent.get("id"))
        if existing and existing.get("id"):
            query = """mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id identifier url}}}"""
            result = self._mutate(query, {"id": existing["id"], "input": input_data})
            return (result.get("issueUpdate") or {}).get("issue") or result.get("issueUpdate", {})
        create_input = {"teamId": self._team(), "title": title, **input_data}
        query = """mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id identifier url}}}"""
        result = self._mutate(query, {"input": create_input})
        return (result.get("issueCreate") or {}).get("issue") or result.get("issueCreate", {})

    def _tia83(self) -> dict[str, Any]:
        query = """query($id:String!){issue(id:$id){id children(first:100){nodes{id identifier title description labelIds labels{nodes{id name}} state{id type name} priority project{id name} projectMilestone{id name}}} team{id states{nodes{id type name}} labels(first:100){nodes{id name}} projects(first:50){nodes{id name projectMilestones(first:50){nodes{id name}}}}}}}"""
        return _linear_query(self.token, query, {"id": "TIA-83"}).get("issue") or {}

    def enforce_tia83(self, roots: Sequence[Mapping[str, Any]]) -> list[str]:
        parent = self._tia83()
        completed_ids = self._completed_linear_ids_from_supervisor()
        team = parent.get("team") or {}
        states = ((team.get("states") or {}).get("nodes") or [])
        labels = ((team.get("labels") or {}).get("nodes") or [])
        todo = next((state for state in states if str(state.get("name") or "").casefold() == "todo"), None)
        todo_id = (todo or {}).get("id") or "1a23b243-a6a0-4973-bf05-e6b39c13f883"
        done = next((state for state in states if str(state.get("name") or "").casefold() == "done"), None)
        done_id = (done or {}).get("id") or "4ec80720-d769-4133-925e-02dbf1845567"
        needs = next((label for label in labels if str(label.get("name") or "").casefold() == "needs decision"), None)
        needs_id = (needs or {}).get("id") or "f8b8718c-1500-4c0d-b5cc-bd9a97d9a6d5"
        projects = ((team.get("projects") or {}).get("nodes") or [])
        work_project = next((project for project in projects if str(project.get("name") or "").casefold() == "work business"), None)
        changed: list[str] = []
        for child in ((parent.get("children") or {}).get("nodes") or []):
            title = str(child.get("title") or "")
            if not (title.startswith("[PR Action]") or title.startswith("[PR Decision]") or "awaiting_owner" in str(child.get("description") or "")):
                continue
            classification = classify_jev(child, roots)
            root_id = classification.get("root_id")
            root = next((value for value in roots if value.get("root_id") == root_id), None)
            if root is None:
                continue
            root_name = str(root.get("name") or root_id)
            project = work_project or child.get("project") or {}
            milestones = ((project.get("projectMilestones") or {}).get("nodes") or [])
            milestone = next((value for value in milestones if str(value.get("name") or "").casefold() == root_name.casefold()), None)
            if milestone is None and root_id == "china-release":
                milestone = next((value for value in milestones if str(value.get("name") or "").casefold() == "sapphire china bugs"), None)
            updates: dict[str, Any] = {}
            is_completed = str(child.get("identifier") or "") in completed_ids or str((child.get("state") or {}).get("type") or "").casefold() == "completed"
            target_state_id = done_id if is_completed else todo_id
            if (child.get("state") or {}).get("id") != target_state_id:
                updates["stateId"] = target_state_id
            if not is_completed and child.get("priority") != 1:
                updates["priority"] = 1
            existing_ids = set(child.get("labelIds") or [])
            existing_names = {str(label.get("name") or "").casefold() for label in ((child.get("labels") or {}).get("nodes") or [])}
            if not is_completed and needs_id not in existing_ids and "needs decision" not in existing_names:
                updates["labelIds"] = sorted(existing_ids | {needs_id})
            project_id = project.get("id")
            if project_id and (child.get("project") or {}).get("id") != project_id:
                updates["projectId"] = project_id
            milestone_id = (milestone or {}).get("id")
            if milestone_id and (child.get("projectMilestone") or {}).get("id") != milestone_id:
                updates["projectMilestoneId"] = milestone_id
            if not updates:
                continue
            mutation = """mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id identifier}}}"""
            self._mutate(mutation, {"id": child["id"], "input": updates})
            changed.append(str(child.get("identifier") or child.get("id")))
        return changed

    def ensure_related(self, workstream: Mapping[str, Any], root_id: str) -> list[str]:
        workstream_id = workstream.get("id")
        if not workstream_id:
            return []
        parent = self._tia83()
        roots = load_roots()
        related: list[str] = []
        for child in ((parent.get("children") or {}).get("nodes") or []):
            title = str(child.get("title") or "")
            if not (title.startswith("[PR Action]") or title.startswith("[PR Decision]")):
                continue
            classification = classify_jev(child, roots)
            if classification.get("root_id") != root_id or (child.get("state") or {}).get("type") == "completed":
                continue
            mutation = """mutation($input:IssueRelationCreateInput!){issueRelationCreate(input:$input){success issueRelation{id}}}"""
            try:
                self._mutate(mutation, {"input": {"issueId": workstream_id, "relatedIssueId": child.get("id"), "type": "related"}})
            except LinearSyncError:
                continue
            related.append(str(child.get("identifier") or child.get("id")))
        return related

    def upsert_child(self, parent: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
        title = item.get("title", item.get("id", "tracking item"))
        existing = self._find_issue(str(title), parent.get("id"))
        if existing and existing.get("id"):
            query = """mutation($id:ID!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id identifier url}}}"""
            result = self._mutate(query, {"id": existing["id"], "input": {"description": json.dumps(dict(item), ensure_ascii=False)}})
            return result.get("issueUpdate", {})
        query = """mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id identifier url}}}"""
        result = self._mutate(query, {"input": {"teamId": self._team(), "title": title, "description": json.dumps(dict(item), ensure_ascii=False), "parentId": parent.get("id")}})
        return result.get("issueCreate", {})
