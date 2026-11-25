"""Standard-library Linear GraphQL client with OAuth refresh and pagination."""
from __future__ import annotations

from ._imports import *

class GraphQLClient:
    def __init__(
        self,
        api_key: str,
        transport: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        *, allow_refresh: bool = True,
    ) -> None:
        self.transport = transport or self._request
        self.api_key = api_key
        self.allow_refresh = allow_refresh

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
                "Authorization": (f"Bearer {self.api_key}" if _oauth_app_enabled() and not self.api_key.startswith("Bearer ") else self.api_key),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 401 and not _retried and self.allow_refresh:
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
          assignee {{ id name displayName }}
          delegate {{ id name displayName }}
          state {{ id name type }}
          team {{ id key name }}
          project {{ id name }}
          projectMilestone {{ id name }}
          parent {{ id identifier title }}
          labels {{ nodes {{ id name }} }}
          comments {{ nodes {{ id body createdAt updatedAt parent {{ id }} user {{ id name displayName app }} botActor {{ id name type }} }} pageInfo {{ hasNextPage endCursor }} }}
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
        self._paginate_comments(issue)
        return issue

    def archive_issue(self, issue_id: str) -> bool:
        data = self.execute(
            """
            mutation ArchiveIssue($id: String!) {
              issueArchive(id: $id) {
                success
              }
            }
            """,
            {"id": issue_id},
        )
        return bool((data.get("issueArchive") or {}).get("success"))

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
        assignee_id: str | None = None,
        delegate_id: str | None = None,
        create_as_user: str | None = None,
        allow_archive_retry: bool = True,
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
        if assignee_id:
            input_data["assigneeId"] = assignee_id
        if delegate_id:
            input_data["delegateId"] = delegate_id
        if create_as_user and _oauth_app_enabled():
            input_data["createAsUser"] = create_as_user
        try:
            data = self.execute(
                f"""
                mutation CreateIssue($input: IssueCreateInput!) {{
                  issueCreate(input: $input) {{
                    success
                    issue {{
                      id identifier url title description priority updatedAt
                      assignee {{ id name displayName }}
                      delegate {{ id name displayName }}
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
        except LinearSyncError as exc:
            if allow_archive_retry and _is_usage_limit_error(str(exc)):
                archive_completed_issues(
                    self,
                    team_key="TIA",
                    retention_days=2,
                    max_active_issues=150,
                    mode="auto",
                )
                return self.create_issue(
                    team_id=team_id,
                    title=title,
                    description=description,
                    state_id=state_id,
                    label_ids=label_ids,
                    project_id=project_id,
                    project_milestone_id=project_milestone_id,
                    parent_id=parent_id,
                    assignee_id=assignee_id,
                    delegate_id=delegate_id,
                    create_as_user=create_as_user,
                    allow_archive_retry=False,
                )
            raise
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
        assignee_id: str | None = None,
        delegate_id: str | None = None,
        clear_assignee: bool = False,
        clear_delegate: bool = False,
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
        if assignee_id is not None or clear_assignee:
            input_data["assigneeId"] = assignee_id
        if delegate_id is not None or clear_delegate:
            input_data["delegateId"] = delegate_id
        data = self.execute(
            f"""
            mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {{
              issueUpdate(id: $id, input: $input) {{
                success
                issue {{
                  id identifier url title description priority updatedAt
                  assignee {{ id name displayName }}
                  delegate {{ id name displayName }}
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

    def create_comment(self, issue_id: str, body: str, create_as_user: str | None = None, parent_id: str | None = None) -> str | None:
        input_data: dict[str, Any] = {"issueId": issue_id, "body": body}
        if parent_id:
            input_data["parentId"] = parent_id
        if create_as_user and _oauth_app_enabled():
            input_data["createAsUser"] = create_as_user
        data = self.execute(
            """
            mutation CreateComment($input: CommentCreateInput!) {
              commentCreate(input: $input) { success comment { id parent { id } } }
            }
            """,
            {"input": input_data},
        )
        result = data.get("commentCreate", {})
        if not result.get("success"):
            raise LinearSyncError("Linear did not create the comment")
        return (result.get("comment") or {}).get("id")

    def _paginate_comments(self, issue: dict[str, Any]) -> None:
        comments = issue.get("comments") or {}
        page = comments.get("pageInfo") or {}
        if not page.get("hasNextPage"): return
        nodes = list(comments.get("nodes", [])); after = page.get("endCursor")
        cursors: set[str] = set()
        while page.get("hasNextPage"):
            if not after or after in cursors:
                raise LinearSyncError("Linear comment pagination did not advance")
            cursors.add(after)
            data = self.execute(
                """query IssueComments($id: String!, $after: String) {
                  issue(id: $id) { comments(first: 100, after: $after) {
                    nodes { id body createdAt updatedAt parent { id } user { id name displayName app } botActor { id name type } }
                    pageInfo { hasNextPage endCursor }
                  } }
                }""", {"id": str(issue.get("id")), "after": after})
            block = (data.get("issue") or {}).get("comments") or {}
            nodes.extend(block.get("nodes", []))
            page = block.get("pageInfo") or {}
            after = page.get("endCursor")
        issue["comments"] = {"nodes": sorted(nodes, key=lambda c: (str(c.get("createdAt") or ""), str(c.get("id") or ""))), "pageInfo": {"hasNextPage": False}}

    def issue_scope(self, identifier: str) -> dict[str, Any]:
        data = self.execute(
            """query IssueScope($id: String!) {
              issue(id: $id) { id identifier title team { id key name }
                project { id name } parent { id identifier title } }
            }""", {"id": identifier})
        issue = data.get("issue")
        if not isinstance(issue, dict):
            raise LinearSyncError(f"Linear ancestor '{identifier}' was not found")
        return issue

    def list_issues(
        self, team_key: str, *, state: str = "", project: str = "",
        query: str = "", limit: int | None = 25, updated_after: str = "",
    ) -> list[dict[str, Any]]:
        issue_filter: dict[str, Any] = {"team": {"key": {"eq": team_key}}}
        if updated_after:
            issue_filter["updatedAt"] = {"gte": updated_after}
        issues: list[dict[str, Any]] = []
        after = None
        cursors: set[str] = set()
        while limit is None or len(issues) < limit:
            data = self.execute(
                """query ListIssues($filter: IssueFilter!, $first: Int!, $after: String) {
                  issues(filter: $filter, first: $first, after: $after, orderBy: updatedAt) {
                    nodes {
                      id identifier title url description priority createdAt updatedAt completedAt archivedAt
                      assignee { id name displayName } delegate { id name displayName }
                      state { id name type } project { id name } team { id key name }
                      parent { id identifier title project { id name } team { id key name } parent { id } }
                      comments(first: 100) { nodes { id body createdAt updatedAt parent { id }
                        user { id name displayName app } botActor { id name type } }
                        pageInfo { hasNextPage endCursor } }
                      labels { nodes { id name } }
                    }
                    pageInfo { hasNextPage endCursor }
                  }
                }""",
                {"filter": issue_filter, "first": 100 if limit is None else min(limit - len(issues), 100), "after": after},
            )
            block = data.get("issues") or {}
            issues.extend(block.get("nodes", []))
            page = block.get("pageInfo") or {}
            if not page.get("hasNextPage"): break
            after = page.get("endCursor")
            if not after or after in cursors:
                raise LinearSyncError("Linear issue pagination did not advance")
            cursors.add(after)
        for issue in issues:
            self._paginate_comments(issue)
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
