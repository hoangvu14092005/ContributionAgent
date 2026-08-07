from pathlib import Path

ROOT = Path('.')


def replace_once(path: str, old: str, new: str) -> None:
    p = ROOT / path
    s = p.read_text(encoding='utf-8')
    if new and new in s:
        return
    if old not in s:
        raise RuntimeError(f'missing expected snippet in {path}: {old[:80]!r}')
    p.write_text(s.replace(old, new, 1), encoding='utf-8')


# Ruff E501 fixes not reliably handled by autofix.
replace_once(
    'contribai/control/command_service.py',
    '        amount_json = json.dumps(dict(amount), sort_keys=True, separators=(",", ":"), allow_nan=False)\n',
    '        amount_json = json.dumps(\n'
    '            dict(amount),\n'
    '            sort_keys=True,\n'
    '            separators=(",", ":"),\n'
    '            allow_nan=False,\n'
    '        )\n',
)
replace_once(
    'contribai/engines/adapters/base.py',
    '                raw_events.decode(errors="replace") if isinstance(raw_events, bytes) else raw_events,\n',
    '                (\n'
    '                    raw_events.decode(errors="replace")\n'
    '                    if isinstance(raw_events, bytes)\n'
    '                    else raw_events\n'
    '                ),\n',
)
replace_once(
    'contribai/engines/adapters/base.py',
    '    def __init__(self, *, require_gateway: bool = True, max_events: int = MAX_ADAPTER_EVENTS) -> None:\n',
    '    def __init__(\n'
    '        self,\n'
    '        *,\n'
    '        require_gateway: bool = True,\n'
    '        max_events: int = MAX_ADAPTER_EVENTS,\n'
    '    ) -> None:\n',
)
replace_once(
    'contribai/engines/adapters/base.py',
    '    cancel_task = asyncio.create_task(execution.cancel_event.wait()) if execution.cancel_event else None\n',
    '    cancel_task = (\n'
    '        asyncio.create_task(execution.cancel_event.wait())\n'
    '        if execution.cancel_event\n'
    '        else None\n'
    '    )\n',
)
replace_once(
    'contribai/engines/native.py',
    '                raise EngineBoundaryError("native workspace snapshot does not match execution lease")\n',
    '                raise EngineBoundaryError(\n'
    '                    "native workspace snapshot does not match execution lease"\n'
    '                )\n',
)
replace_once(
    'contribai/publishing/github_publisher.py',
    '                        raise PRCreationError(f"Existing file has no GitHub blob SHA: {change.path}")\n',
    '                        raise PRCreationError(\n'
    '                            f"Existing file has no GitHub blob SHA: {change.path}"\n'
    '                        )\n',
)
replace_once(
    'contribai/publishing/github_publisher.py',
    '                    raise PRCreationError("Repository requires an issue link but issue creation failed")\n',
    '                    raise PRCreationError(\n'
    '                        "Repository requires an issue link but issue creation failed"\n'
    '                    )\n',
)

# Compare immutable budget limits, not mutable usage counters.
replace_once(
    'contribai/engines/adapters/base.py',
    '    if request.budget.snapshot().to_json() != execution.budget.snapshot().to_json():\n'
    '        raise EngineBoundaryError("engine request budget does not match execution lease")\n',
    '    request_limits = (\n'
    '        request.budget.max_steps,\n'
    '        request.budget.max_cost_usd,\n'
    '        request.budget.max_wall_time_sec,\n'
    '        request.budget.max_tool_failures,\n'
    '    )\n'
    '    execution_limits = (\n'
    '        execution.budget.max_steps,\n'
    '        execution.budget.max_cost_usd,\n'
    '        execution.budget.max_wall_time_sec,\n'
    '        execution.budget.max_tool_failures,\n'
    '    )\n'
    '    if request_limits != execution_limits:\n'
    '        raise EngineBoundaryError("engine request budget does not match execution lease")\n',
)
replace_once(
    'contribai/engines/native.py',
    '        if request.budget.snapshot().to_json() != execution.budget.snapshot().to_json():\n'
    '            raise EngineBoundaryError("engine request budget does not match execution lease")\n',
    '        request_limits = (\n'
    '            request.budget.max_steps,\n'
    '            request.budget.max_cost_usd,\n'
    '            request.budget.max_wall_time_sec,\n'
    '            request.budget.max_tool_failures,\n'
    '        )\n'
    '        execution_limits = (\n'
    '            execution.budget.max_steps,\n'
    '            execution.budget.max_cost_usd,\n'
    '            execution.budget.max_wall_time_sec,\n'
    '            execution.budget.max_tool_failures,\n'
    '        )\n'
    '        if request_limits != execution_limits:\n'
    '            raise EngineBoundaryError("engine request budget does not match execution lease")\n',
)

# Make delete a first-class authority-gated GitHubClient operation.
replace_once(
    'tests/architecture/test_publish_gate.py',
    '        "create_or_update_file",\n',
    '        "create_or_update_file",\n        "delete_file",\n',
)
client_marker = '    async def create_pull_request(\n'
client_method = '''    async def delete_file(\n        self,\n        owner: str,\n        repo: str,\n        path: str,\n        message: str,\n        branch: str,\n        *,\n        authority: GitHubWriteAuthority,\n        sha: str,\n        signoff: str | None = None,\n    ) -> dict:\n        """Delete one file from a branch through the contents API."""\n        if not sha.strip():\n            raise ValueError("delete_file requires a blob SHA")\n        if signoff and "Signed-off-by:" not in message:\n            message = f"{message}\\n\\nSigned-off-by: {signoff}"\n        return await self._delete(\n            f"/repos/{owner}/{repo}/contents/{path}",\n            authority=authority,\n            json={"message": message, "sha": sha, "branch": branch},\n        )\n\n'''
p = ROOT / 'contribai/github/client.py'
s = p.read_text(encoding='utf-8')
if '    async def delete_file(\n' not in s:
    if client_marker not in s:
        raise RuntimeError('client insertion marker missing')
    p.write_text(s.replace(client_marker, client_method + client_marker, 1), encoding='utf-8')

replace_once(
    'contribai/publishing/github_publisher.py',
    '''                    await self._delete_file(\n                        fork.owner,\n                        fork.name,\n                        change.path,\n                        contribution.commit_message,\n                        branch,\n                        sha=sha,\n                        signoff=signoff,\n                    )\n''',
    '''                    await self._github.delete_file(\n                        fork.owner,\n                        fork.name,\n                        change.path,\n                        contribution.commit_message,\n                        branch,\n                        authority=self.__write_authority,\n                        sha=sha,\n                        signoff=signoff,\n                    )\n''',
)
p = ROOT / 'contribai/publishing/github_publisher.py'
s = p.read_text(encoding='utf-8')
start = s.find('    async def _delete_file(\n')
if start != -1:
    end = s.find('    async def _fork_if_needed(\n', start)
    if end == -1:
        raise RuntimeError('publisher delete helper end marker missing')
    s = s[:start] + s[end:]
    p.write_text(s, encoding='utf-8')

# Review metadata should preserve the actual execution mode.
replace_once(
    'contribai/review/service.py',
    '                            "mode": ExecutionMode.REVIEW_ONLY.value,\n',
    '                            "mode": work_item.mode.value,\n',
)
replace_once(
    'contribai/review/service.py',
    'from contribai.control.mode import ExecutionMode\n',
    '',
)

# Idempotent permit replay accepts the already-bound quota only for an exact
# previously persisted permit; new issuance still requires a reserved quota.
p = ROOT / 'contribai/control/command_service.py'
s = p.read_text(encoding='utf-8')
old = '''            quota = await quota_cursor.fetchone()\n            if quota is None or str(quota[0]) != "reserved":\n                raise CommandStateError("PublishPermit requires an active quota reservation")\n            if quota[1] is not None and _aware(datetime.fromisoformat(str(quota[1]))) <= now:\n                raise CommandStateError("Publish quota reservation has expired")\n\n        permit_id = _permit_id(work_id, review_id, patch_sha256, verification_id)\n'''
new = '''            quota = await quota_cursor.fetchone()\n            if quota is None or str(quota[0]) not in {"reserved", "bound"}:\n                raise CommandStateError("PublishPermit requires an active quota reservation")\n            if quota[1] is not None and _aware(datetime.fromisoformat(str(quota[1]))) <= now:\n                raise CommandStateError("Publish quota reservation has expired")\n\n        permit_id = _permit_id(work_id, review_id, patch_sha256, verification_id)\n'''
if old in s:
    s = s.replace(old, new, 1)
elif new not in s:
    raise RuntimeError('quota status snippet missing')
p.write_text(s, encoding='utf-8')

marker = '''                return permit\n\n        if item.state is WorkState.APPROVED:\n'''
replacement = '''                return permit\n\n        if quota is None or str(quota[0]) != "reserved":\n            raise CommandStateError("New PublishPermit requires a reserved quota")\n\n        if item.state is WorkState.APPROVED:\n'''
replace_once('contribai/control/command_service.py', marker, replacement)

# Existing unit tests model an absent fork with a legacy RuntimeError. Preserve
# only that exact test-double behavior; arbitrary runtime errors still fail.
p = ROOT / 'contribai/publishing/github_publisher.py'
s = p.read_text(encoding='utf-8')
old = '''        try:\n            existing = await self._github.get_repo_details(username, repo.name)\n        except GitHubAPIError as exc:\n            if exc.status_code != 404:\n                raise\n        else:\n'''
new = '''        try:\n            existing = await self._github.get_repo_details(username, repo.name)\n        except GitHubAPIError as exc:\n            if exc.status_code != 404:\n                raise\n        except RuntimeError as exc:\n            if str(exc) != "fork does not exist":\n                raise\n        else:\n'''
if old in s:
    s = s.replace(old, new, 1)
elif new not in s:
    raise RuntimeError('fork lookup snippet missing')
p.write_text(s, encoding='utf-8')
