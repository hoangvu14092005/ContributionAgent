"""Haystack-style step functions for the ContribAI pipeline.

Layer C extracted the body of ``ContribPipeline._process_repo`` (and its
issue-mode sibling) into 5 + 2 step functions living here. Each step is
``async def step(ctx, state) -> None`` and mutates ``state`` in place.

The split mirrors the data-flow diagram in ``docs/ARCHITECTURE_V2.md``:

  Analysis mode:
    1. ``load_repo_context_step``     — pre-flight, guidelines, repo intel
    2. ``run_analysis_step``          — analyzer + pre-filter
    3. ``validate_findings_step``     — build context, dedup, LLM-validate
    4. ``generate_contribution_step`` — generate candidate
    5. ``submit_pr_step``             — review, create PR, compliance, CI

  Issue mode (reuses load + generate + submit):
    1. ``load_repo_context_step`` (shared)
    2. ``solve_issue_step``          — replaces run_analysis_step
    3. ``validate_issue_findings_step`` — thinner validate (no GitHub dedup)
    4. ``generate_contribution_step`` (shared)
    5. ``submit_pr_step`` (shared)

Module-level helpers (no ``self.``) live alongside their step. They can be
called directly from outside the pipeline (e.g. ``HumanReviewer`` still
uses ``_is_code_file`` from :mod:`contribai.orchestrator.pipeline`).

Cyclomatic target: each step ≤10 branches. ``radon cc -s steps.py``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from contribai.analysis.context_compressor import ContextCompressor
from contribai.core.events import Event, EventType
from contribai.core.models import (
    RepoContext,
)
from contribai.core.path_policy import is_invalid_repository_path, is_protected_meta_path
from contribai.github.guidelines import fetch_repo_guidelines
from contribai.orchestrator.pipeline_constants import (
    SKIP_DIRECTORIES,
    SKIP_EXTENSIONS,
    _titles_similar,
)

if TYPE_CHECKING:
    from contribai.llm.provider import LLMProvider
    from contribai.orchestrator.pipeline_core import PipelineContext, PipelineState

logger = logging.getLogger(__name__)


# ── Step 1: load_repo_context_step ───────────────────────────────────────────


async def load_repo_context_step(ctx: PipelineContext, state: PipelineState) -> None:
    """Step 1 — pre-flight checks and context loading.

    Reads from ``state.repo`` (set by caller). Writes:
      - ``state.cached_context`` (memory recall)
      - ``state.guidelines``
      - ``state.repo_profile``
      - ``state.pr_history_context``
      - ``state.extra_context`` (combined profile + PR history)

    Sets ``state.skip_reason`` when the repo is ineligible:
      - ``"ai_policy"`` if the repo bans AI-generated PRs
      - ``"pr_permissions"`` if the repo restricts PRs to collaborators
    """
    repo = state.repo

    # ── Memory recall — surfaces any cached analysis from prior runs ───
    cached_context = await ctx.memory.get_context(repo.full_name, "analysis_summary")
    if cached_context:
        state.cached_context = cached_context
        logger.info(
            "💾 Loaded cached context for %s (%d chars)",
            repo.full_name,
            len(cached_context),
        )
        await ctx.event_bus.emit(
            Event(
                type=EventType.MEMORY_RECALL,
                source="pipeline.load_repo_context_step",
                data={"repo": repo.full_name, "key": "analysis_summary"},
            )
        )

    # ── AI policy check — skip repos that ban AI-generated PRs ─────────
    if await _check_ai_policy(ctx.github, repo):
        logger.warning("🚫 %s has an AI policy that bans AI PRs, skipping.", repo.full_name)
        state.skip_reason = "ai_policy"
        return

    # ── PR permissions check — skip if PRs are collaborator-only ───────
    if await _check_pr_permissions(ctx.github, repo):
        logger.warning("%s restricts PRs to collaborators only, skipping.", repo.full_name)
        state.skip_reason = "pr_permissions"
        return

    # ── Fetch repo guidelines (CONTRIBUTING.md, PR template) ───────────
    state.guidelines = await fetch_repo_guidelines(ctx.github, repo.owner, repo.name)
    if state.guidelines.has_guidelines:
        logger.info(
            "Repo guidelines: commit=%s, %d template sections",
            state.guidelines.commit_format,
            len(state.guidelines.required_sections),
        )

    # ── Repo Intelligence — best-effort profile ────────────────────────
    try:
        state.repo_profile = await ctx.repo_intel.profile(repo.owner, repo.name)
    except Exception as e:
        logger.debug("Repo intelligence failed for %s: %s", repo.full_name, e)

    # ── Smart dedup — inject PR history into analysis context ───────────
    past_prs = await ctx.memory.get_repo_prs(repo.full_name)
    if past_prs:
        pr_lines = [f"  - [{pr.get('status', '?')}] {pr.get('title', '?')}" for pr in past_prs[:10]]
        state.pr_history_context = (
            "\n\nPREVIOUSLY SUBMITTED PRs (DO NOT repeat these):\n" + "\n".join(pr_lines)
        )
        logger.info("🔁 Injected %d past PRs into analysis context", len(past_prs))

    # ── Compose working-memory context (profile + PR history) ─────────
    if state.repo_profile or state.pr_history_context:
        extra = ""
        if state.repo_profile:
            extra += "\n\n" + state.repo_profile.to_prompt_context()
        if state.pr_history_context:
            extra += state.pr_history_context
        state.extra_context = extra
        await ctx.memory.store_context(
            repo.full_name,
            "repo_intelligence",
            extra,
            language=repo.language or "",
            ttl_hours=48.0,
        )


# ── Step 2: run_analysis_step ────────────────────────────────────────────────


async def run_analysis_step(ctx: PipelineContext, state: PipelineState) -> None:
    """Step 2 — run the analyzer and pre-filter findings.

    Reads ``state.repo``. Writes:
      - ``state.analysis``
      - ``state.findings`` (post-filter)

    Emits ``ANALYSIS_START`` / ``ANALYSIS_COMPLETE``. Saves a compact
    ``analysis_summary`` to memory. Sets ``state.skip_reason = "no_findings"``
    when the analyzer returns nothing or all findings are filtered out.
    """
    repo = state.repo
    ctx.set_task("analysis")

    logger.info("🔬 Analyzing code...")
    await ctx.event_bus.emit(
        Event(
            type=EventType.ANALYSIS_START,
            source="pipeline.run_analysis_step",
            data={"repo": repo.full_name},
        )
    )
    analysis = await ctx.analyzer.analyze(repo)
    await ctx.event_bus.emit(
        Event(
            type=EventType.ANALYSIS_COMPLETE,
            source="pipeline.run_analysis_step",
            data={"repo": repo.full_name, "findings": len(analysis.findings)},
        )
    )
    state.analysis = analysis
    state.result.findings_total = len(analysis.findings)

    # Record analysis count for memory / metrics
    try:
        await ctx.memory.record_analysis(
            repo.full_name,
            repo.language or "unknown",
            repo.stars,
            len(analysis.findings),
        )
    except Exception as e:
        logger.debug("record_analysis failed for %s: %s", repo.full_name, e)

    # Save compact summary as working memory (best-effort)
    try:
        summary = ContextCompressor.summarize_findings_compact(analysis.findings)
        await ctx.memory.store_context(
            repo.full_name,
            "analysis_summary",
            summary,
            language=repo.language or "",
            ttl_hours=72.0,
        )
        await ctx.event_bus.emit(
            Event(
                type=EventType.MEMORY_STORE,
                source="pipeline.run_analysis_step",
                data={"repo": repo.full_name, "key": "analysis_summary"},
            )
        )
    except Exception as e:
        logger.debug("Failed to save analysis_summary: %s", e)

    if not analysis.findings:
        logger.info("No findings for %s", repo.full_name)
        state.skip_reason = "no_findings"
        return

    # ── Pre-filter: drop non-code / low-value / protected findings ────
    state.findings = [f for f in analysis.findings if _is_actionable_finding(f)]

    if not state.findings:
        logger.info("All findings filtered (non-code targets) for %s", repo.full_name)
        state.skip_reason = "no_findings"
        return

    # Mutate the analysis object too — downstream steps rely on it.
    analysis.findings = state.findings
    logger.info(
        "Found %d issues (analyzed %d files in %.1fs)",
        len(state.findings),
        analysis.analyzed_files,
        analysis.analysis_duration_sec,
    )


# ── Step 3a: validate_findings_step (analysis mode) ──────────────────────────


async def validate_findings_step(ctx: PipelineContext, state: PipelineState) -> None:
    """Step 3 — build context, dedup, LLM-validate, cap.

    Reads ``state.findings`` and ``state.max_prs``. Writes:
      - ``state.file_tree``
      - ``state.relevant_files``
      - ``state.context`` (built from repo + tree + relevant files)
      - ``state.validated_findings`` (capped by ``max_findings_per_repo``)

    Sets ``state.skip_reason = "no_validated"`` if dedup/validation empties
    the list.
    """
    repo = state.repo
    findings = state.findings

    # ── Build RepoContext — fetch file tree + relevant files ───────────
    state.file_tree = await ctx.github.get_file_tree(repo.owner, repo.name)
    file_paths: list[str] = []
    for finding in findings[: state.max_prs]:
        if finding.file_path and finding.file_path not in state.relevant_files:
            file_paths.append(finding.file_path)
    state.relevant_files = await _fetch_relevant_files(ctx.github, repo, file_paths)
    state.context = RepoContext(
        repo=repo,
        file_tree=state.file_tree,
        relevant_files=state.relevant_files,
    )

    # ── Dedup — drop findings that overlap with past PRs ───────────────
    deduped = await _dedup_against_past_prs(ctx, repo, findings[: state.max_prs])

    if not deduped:
        logger.info("No new findings after duplicate filter for %s", repo.full_name)
        state.skip_reason = "no_validated"
        return

    # ── LLM validation — confirm findings are real, not false positives ─
    validated = await _validate_findings(
        ctx.llm,
        deduped,
        state.relevant_files,
        set_task=lambda t: ctx.set_task(t),
    )

    # ── Cap by max_findings_per_repo ───────────────────────────────────
    max_per_repo = getattr(ctx.config.pipeline, "max_findings_per_repo", 3)
    if len(validated) > max_per_repo:
        logger.info(
            "📉 Limiting to %d findings per repo (had %d)",
            max_per_repo,
            len(validated),
        )
        validated = validated[:max_per_repo]

    state.validated_findings = validated
    logger.info(
        "🔎 Validated %d/%d findings for %s",
        len(validated),
        min(len(findings), state.max_prs),
        repo.full_name,
    )

    if not validated:
        state.skip_reason = "no_validated"


# ── Step 4: generate_contribution_step ───────────────────────────────────────


async def generate_contribution_step(ctx: PipelineContext, state: PipelineState) -> None:
    """Step 4 — generate a contribution per validated finding.

    Reads ``state.validated_findings``, ``state.context``, ``state.guidelines``.
    Writes ``state.contributions`` and ``state.result.contributions_generated``.

    In dry-run mode the step still calls ``generator.generate`` (so the
    pipeline records what *would* have happened) but skips the human review
    gate and the eventual PR submission (handled by :func:`submit_pr_step`).
    """
    findings = state.validated_findings
    if not findings:
        state.skip_reason = "no_validated"
        return

    repo = state.repo
    context = state.context
    guidelines = state.guidelines

    for index, finding in enumerate(findings):
        logger.info("Generating fix for: %s", finding.title)
        ctx.set_task("code_gen")
        await ctx.event_bus.emit(
            Event(
                type=EventType.GENERATION_START,
                source="pipeline.generate_contribution_step",
                data={"repo": repo.full_name, "finding": finding.title},
            )
        )
        contribution = await ctx.generator.generate(finding, context, guidelines=guidelines)
        await ctx.event_bus.emit(
            Event(
                type=EventType.GENERATION_COMPLETE,
                source="pipeline.generate_contribution_step",
                data={
                    "repo": repo.full_name,
                    "finding": finding.title,
                    "success": contribution is not None,
                },
            )
        )

        if contribution is None:
            continue

        state.contributions.append(contribution)
        closes_issue = (
            state.closes_issues[index] if index < len(state.closes_issues) else None
        )
        from contribai.orchestrator.pipeline_core import ContributionEnvelope

        state.contribution_envelopes.append(
            ContributionEnvelope(contribution=contribution, closes_issue=closes_issue)
        )
        state.result.contributions_generated += 1

        # ── Dry run — record intent but stop before PR submission ─────
        if state.dry_run:
            logger.info("🏃 [DRY RUN] Would create PR: %s", contribution.title)
            continue


# ── Step 5: submit_pr_step ───────────────────────────────────────────────────


async def submit_pr_step(ctx: PipelineContext, state: PipelineState) -> None:
    """Step 5 — create a PR per contribution and run post-submission checks.

    Reads ``state.contributions``, ``state.closes_issue``, ``state.guidelines``.
    Writes ``state.prs``, ``state.result.prs``, ``state.result.errors``.

    In dry-run mode this step is a no-op (PR creation was already skipped
    at the :func:`generate_contribution_step` boundary).
    """
    if state.dry_run:
        return

    repo = state.repo
    guidelines = state.guidelines

    from contribai.orchestrator.pipeline_core import ContributionEnvelope

    envelopes = state.contribution_envelopes or [
        ContributionEnvelope(
            contribution=contribution,
            closes_issue=state.closes_issues[index]
            if index < len(state.closes_issues)
            else None,
        )
        for index, contribution in enumerate(state.contributions)
    ]

    for envelope in envelopes:
        contribution = envelope.contribution
        closes_issue = envelope.closes_issue
        quota_reserved = False
        if ctx.pr_quota is not None:
            quota_reserved = await ctx.pr_quota.try_acquire()
            if not quota_reserved:
                logger.warning("PR quota exhausted before publishing %s", contribution.title)
                break
        try:
            logger.info("📤 Creating PR: %s", contribution.title)
            if ctx.review_and_publish is None:
                pr_result = await ctx.pr_manager.create_pr(
                    contribution,
                    repo,
                    guidelines=guidelines,
                    closes_issue=closes_issue,
                )
            else:
                pr_result = await ctx.review_and_publish(
                    contribution,
                    contribution.finding,
                    repo,
                    guidelines,
                    closes_issue=closes_issue,
                )
            if pr_result is None:
                if quota_reserved:
                    await ctx.pr_quota.release()
                continue
            state.prs.append(pr_result)
            state.result.prs.append(pr_result)
            state.result.prs_created += 1

            await ctx.event_bus.emit(
                Event(
                    type=EventType.PR_CREATED,
                    source="pipeline.submit_pr_step",
                    data={
                        "repo": repo.full_name,
                        "pr_url": pr_result.pr_url,
                        "title": contribution.title,
                    },
                )
            )

            await ctx.memory.record_pr(
                repo=repo.full_name,
                pr_number=pr_result.pr_number,
                pr_url=pr_result.pr_url,
                title=contribution.title,
                pr_type=contribution.contribution_type.value,
                branch=pr_result.branch_name,
                fork=pr_result.fork_full_name,
            )

            # The legacy compliance helper may perform GitHub mutations.  A
            # controlled publisher has already bound every write to a permit,
            # so do not invoke an unscoped post-publish mutation here.
            if not ctx.controlled_publish:
                try:
                    logger.info("🔍 Checking PR compliance...")
                    await ctx.pr_manager.check_compliance_and_fix(
                        pr_result, contribution, guidelines=guidelines
                    )
                except Exception as e:
                    logger.warning("Compliance check failed: %s", e)

            # CI wait + auto-close — best effort
            try:
                if ctx.check_ci is None:
                    await _check_ci_and_close_if_failed(ctx.github, ctx.memory, repo, pr_result)
                else:
                    await ctx.check_ci(pr_result, repo)
            except Exception as e:
                logger.warning("CI check failed: %s", e)

        except Exception as e:
            if quota_reserved:
                await ctx.pr_quota.release()
            error = f"PR creation failed for {contribution.title}: {e}"
            logger.error(error)
            state.result.errors.append(error)
            await ctx.event_bus.emit(
                Event(
                    type=EventType.PIPELINE_ERROR,
                    source="pipeline.submit_pr_step",
                    data={"repo": repo.full_name, "error": error},
                )
            )


# ── Issue-mode steps ─────────────────────────────────────────────────────────


async def solve_issue_step(ctx: PipelineContext, state: PipelineState) -> None:
    """Step 2 (issue mode) — fetch solvable issues and plan multi-file fixes.

    Replaces :func:`run_analysis_step` for the issue-driven pipeline. Uses
    :class:`contribai.issues.solver.IssuerSolver` (built lazily inside the
    step if ``ctx.solver`` is ``None``).

    Writes:
      - ``state.context`` — populated from key files
      - ``state.findings`` — multi-file findings from ``solve_issue_deep``

    Sets ``state.skip_reason = "no_findings"`` when no solvable issues are
    found or all of them are filtered out.
    """
    repo = state.repo
    max_prs = state.max_prs

    from contribai.issues.solver import IssueSolver

    solver = ctx.solver or IssueSolver(llm=ctx.llm, github=ctx.github)
    issue_limit = max_prs if state.issue_number is None else max(max_prs, 20)
    issues = await solver.fetch_solvable_issues(repo, max_issues=issue_limit, max_complexity=3)
    if state.issue_number is not None:
        issues = [issue for issue in issues if issue.number == state.issue_number]
    if not issues:
        logger.info("No solvable issues found in %s", repo.full_name)
        state.skip_reason = "no_findings"
        return

    # ── Build a context from key files (top 10) ──────────────────────
    state.file_tree = await ctx.github.get_file_tree(repo.owner, repo.name)
    state.relevant_files = {}
    for fpath in identify_key_files(state.file_tree, repo)[:10]:
        try:
            content = await ctx.github.get_file_content(repo.owner, repo.name, fpath)
            state.relevant_files[fpath] = content
        except Exception:
            pass
    state.context = RepoContext(
        repo=repo,
        file_tree=state.file_tree,
        relevant_files=state.relevant_files,
    )

    # ── Solve each issue — produces a list of findings per issue ──────
    all_findings: list = []
    closes_per_finding: list[int | None] = []
    for issue in issues:
        if state.result.prs_created >= max_prs:
            break
        logger.info("🧠 Solving issue #%d: %s", issue.number, issue.title)
        ctx.set_task("analysis")
        try:
            findings = await solver.solve_issue_deep(issue, repo, state.context)
        except Exception as e:
            logger.warning("solve_issue_deep failed for #%d: %s", issue.number, e)
            continue

        # Pre-filter: skip findings on non-code / low-value / protected files
        findings = [f for f in findings if _is_actionable_finding(f)]
        if not findings:
            logger.info("Could not solve issue #%d (no actionable findings)", issue.number)
            continue

        # Lazy file fetch — extend context with files we haven't read yet
        for f in findings:
            if f.file_path and f.file_path not in state.relevant_files:
                try:
                    content = await ctx.github.get_file_content(repo.owner, repo.name, f.file_path)
                    state.relevant_files[f.file_path] = content
                except Exception:
                    pass
        # Refresh the context's relevant_files mapping for downstream steps
        state.context.relevant_files = state.relevant_files

        all_findings.extend(findings)
        closes_per_finding.extend([issue.number] * len(findings))

    state.findings = all_findings
    state.result.findings_total = len(all_findings)
    if not state.findings:
        state.skip_reason = "no_findings"
        return

    # Track per-finding `closes_issue` values — parallel to ``state.findings``.
    # In issue mode, ``solve_issue_step`` produces one finding per issue (or
    # multiple per issue for multi-file fixes); each finding inherits the
    # issue's number so the eventual PR can link back. ``validate_issue_findings_step``
    # and ``generate_contribution_step`` preserve the parallel list.
    state.closes_issues = closes_per_finding


async def validate_issue_findings_step(ctx: PipelineContext, state: PipelineState) -> None:
    """Step 3 (issue mode) — cap findings by ``max_prs``.

    Skips the GitHub dedup and LLM validation that
    :func:`validate_findings_step` does — issues are user-curated so the
    dedup rarely matters, and the LLM validation already happened inside
    :func:`solve_issue_step` via the solver's planning.

    Preserves the parallel ``state.closes_issues`` list so the eventual
    PR submission can link back to the originating issue.
    """
    findings = state.findings
    if not findings:
        state.skip_reason = "no_findings"
        return

    capped = findings[: state.max_prs]
    state.validated_findings = capped
    # Trim ``closes_issues`` to match the slice (parallel list invariant)
    if state.closes_issues:
        state.closes_issues = state.closes_issues[: state.max_prs]
    if not capped:
        state.skip_reason = "no_validated"


# ── Helper: pre-filter ──────────────────────────────────────────────────────


def _is_actionable_finding(finding) -> bool:
    """True if the finding targets a code file outside protected directories.

    Replicates the legacy in-place filter that lived inside
    ``_process_repo`` and ``_process_repo_issues``. Pure function — used
    by both :func:`run_analysis_step` and :func:`solve_issue_step`.
    """
    fp = finding.file_path or ""

    # Drop non-code file extensions (would be blocked at commit anyway)
    ext = "." + fp.rsplit(".", 1)[-1].lower() if "." in fp else ""
    if ext in SKIP_EXTENSIONS:
        return False

    # Drop findings in low-value directories
    path_parts = fp.lower().replace("\\", "/").split("/")
    if any(part in SKIP_DIRECTORIES for part in path_parts):
        return False

    # Drop invalid or protected paths using the complete normalized path.
    return not is_invalid_repository_path(fp) and not is_protected_meta_path(fp)


# ── Helper: identify key files (used by issue mode) ─────────────────────────


def identify_key_files(file_tree: list, repo) -> list[str]:
    """Pick up to 15 high-signal files for issue-mode context.

    Priority: README/CONTRIBUTING/pyproject/package.json/Cargo.toml/go.mod →
    language entry points (e.g. ``__init__.py`` for Python) → ``src/lib/app``
    directories. Same logic as the legacy ``ContribPipeline._identify_key_files``;
    extracted so :func:`solve_issue_step` can call it directly.
    """
    priority_patterns = [
        "README.md",
        "CONTRIBUTING.md",
        "setup.py",
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
    ]
    all_files = [f.path for f in file_tree if f.type == "blob"]
    key_files: list[str] = []

    for pattern in priority_patterns:
        for fpath in all_files:
            if fpath.endswith(pattern) and fpath not in key_files:
                key_files.append(fpath)
                break

    lang = (repo.language or "").lower()
    entry_patterns = {
        "python": ["__init__.py", "main.py", "app.py", "cli.py"],
        "javascript": ["index.js", "app.js", "server.js"],
        "typescript": ["index.ts", "app.ts", "main.ts"],
        "go": ["main.go", "cmd/main.go"],
        "rust": ["main.rs", "lib.rs"],
    }
    for pat in entry_patterns.get(lang, []):
        for fpath in all_files:
            if fpath.endswith(pat) and fpath not in key_files:
                key_files.append(fpath)

    src_dirs = ["src/", "lib/", "app/", "pkg/", "internal/"]
    for fpath in all_files:
        if len(key_files) >= 15:
            break
        if any(fpath.startswith(d) for d in src_dirs) and fpath not in key_files:
            key_files.append(fpath)

    return key_files[:15]


# ── Helper: fetch relevant files (used by validate_findings_step) ───────────


async def _fetch_relevant_files(github, repo, file_paths: list[str]) -> dict[str, str]:
    """Fetch a deduplicated set of file contents.

    Best-effort — files that fail to fetch are silently skipped (they were
    either deleted, made private, or hit a transient error). The legacy
    code logged at debug level and continued.
    """
    relevant: dict[str, str] = {}
    for fpath in file_paths:
        try:
            content = await github.get_file_content(repo.owner, repo.name, fpath)
            relevant[fpath] = content
        except Exception:
            logger.debug("Could not fetch %s", fpath)
    return relevant


# ── Helper: dedup against past PRs ──────────────────────────────────────────


async def _dedup_against_past_prs(ctx: PipelineContext, repo, findings: list) -> list:
    """Drop findings whose title (similar) or file path matches a past PR.

    Combines two sources:
      1. Local memory (``memory.get_repo_prs``)
      2. GitHub API (recent PRs — catches external contributors too)

    Used by :func:`validate_findings_step`.
    """
    past_titles_lower: set[str] = set()
    past_file_paths: set[str] = set()

    # Local memory
    for pr in await ctx.memory.get_repo_prs(repo.full_name):
        past_titles_lower.add(pr.get("title", "").lower())

    # GitHub API (best-effort — failures fall back to memory-only)
    try:
        github_prs = await ctx.github.list_pull_requests(
            repo.owner, repo.name, state="all", per_page=50
        )
        bot_patterns = ["fix/", "docs/", "feat/", "perf/", "refactor/", "improve/"]
        for gpr in github_prs:
            past_titles_lower.add(gpr.get("title", "").lower())
            head = gpr.get("head", {})
            branch_label = head.get("label", "")
            if any(p in branch_label for p in bot_patterns):
                past_titles_lower.add(gpr.get("title", "").lower())
            body = gpr.get("body", "") or ""
            for match in re.findall(r"`(src/[^\s`]+\.\w+)`", body):
                past_file_paths.add(match)
    except Exception:
        logger.debug("Could not fetch GitHub PRs for dedup, using memory only")

    # Filter findings
    deduped: list = []
    for finding in findings:
        title_lower = finding.title.lower()
        is_title_dup = any(_titles_similar(title_lower, pt) for pt in past_titles_lower)
        is_file_dup = finding.file_path in past_file_paths if finding.file_path else False
        if is_title_dup or is_file_dup:
            logger.info("⏭️ Skipping duplicate finding: %s", finding.title)
            continue
        deduped.append(finding)
    return deduped


# ── Helper: validate findings via LLM (was _validate_findings method) ────────


async def _validate_findings(
    llm: LLMProvider,
    findings: list,
    relevant_files: dict[str, str],
    *,
    set_task=lambda t: None,
) -> list:
    """LLM-validate each finding against the full file content.

    The LLM is asked to return ``VALID:`` or ``INVALID:`` per finding.
    Returns only the validated findings. A finding with no fetched content
    is kept by default (we cannot prove it false). An LLM exception during
    validation also keeps the finding (fail-open) so a transient LLM outage
    doesn't drop legitimate findings.
    """
    if not findings:
        return []

    validated: list = []
    for finding in findings:
        file_content = relevant_files.get(finding.file_path, "")
        if not file_content:
            validated.append(finding)
            continue

        prompt = (
            f"## Finding Validation\n\n"
            f"A code analyzer found this issue. Your job is to determine "
            f"if it is a GENUINE problem or a FALSE POSITIVE.\n\n"
            f"### Finding\n"
            f"- **Title**: {finding.title}\n"
            f"- **Severity**: {finding.severity.value}\n"
            f"- **File**: {finding.file_path}\n"
            f"- **Description**: {finding.description}\n"
            f"- **Suggestion**: {finding.suggestion}\n\n"
            f"### Full File Content\n"
            f"```\n{file_content[:12000]}\n```\n\n"
            f"### Validation Checklist\n"
            f"Check ALL of these before deciding:\n"
            f"1. Is the affected code already protected by try/catch, "
            f"circuit breakers, error boundaries, or fallback patterns?\n"
            f"2. If the finding is about unbounded growth — is the data source "
            f"actually bounded (static array, enum, hardcoded list, config)?\n"
            f"3. Is the function only called from contexts where the issue "
            f"cannot occur?\n"
            f"4. Would the suggested fix add unnecessary complexity without "
            f"real benefit?\n"
            f"5. Does the existing code already handle this edge case through "
            f"a different mechanism?\n\n"
            f"### Response\n"
            f"Respond with EXACTLY one line:\n"
            f"VALID: [brief reason why this is a real issue]\n"
            f"or\n"
            f"INVALID: [brief reason why this is a false positive]\n"
        )

        try:
            set_task("validation")
            response = await llm.complete(
                prompt,
                system=(
                    "You are a senior code reviewer validating automated findings. "
                    "Be skeptical — reject findings that are false positives. "
                    "A finding is INVALID if the code is already protected or "
                    "the issue doesn't exist in practice."
                ),
                temperature=0.1,
            )
            if response.strip().upper().startswith("INVALID"):
                logger.info("❌ Finding rejected: %s — %s", finding.title, response.strip())
                continue
            logger.info("✅ Finding validated: %s", finding.title)
            validated.append(finding)
        except Exception as e:
            logger.warning("Validation failed for %s: %s, keeping", finding.title, e)
            validated.append(finding)

    return validated


# ── Helper: AI policy check (was _check_ai_policy method) ───────────────────


async def _check_ai_policy(github, repo) -> bool:
    """Check if a repo has an AI policy that bans AI-generated PRs.

    Returns True when the repo bans AI PRs. Checks AI_POLICY.md (and a few
    variants) plus CONTRIBUTING.md for known anti-AI phrases. Best-effort:
    every file read is wrapped in try/except so a missing file is treated
    as "no policy found".
    """
    ai_policy_paths = [
        "AI_POLICY.md",
        ".github/AI_POLICY.md",
        ".github/ai_policy.md",
    ]
    ban_keywords = [
        "do not accept ai",
        "no ai-generated",
        "ai contributions are not accepted",
        "ban ai",
        "prohibit ai",
        "ai-generated pull requests will be closed",
        "reject ai",
    ]
    for path in ai_policy_paths:
        try:
            content = await github.get_file_content(repo.owner, repo.name, path)
            if content and any(kw in content.lower() for kw in ban_keywords):
                return True
        except Exception:
            pass

    ban_phrases = [
        "ai-generated contributions",
        "no ai pull requests",
        "ban on ai-generated",
        "do not submit ai",
        "see ai_policy",
    ]
    for contrib_path in ("CONTRIBUTING.md", ".github/CONTRIBUTING.md"):
        try:
            content = await github.get_file_content(repo.owner, repo.name, contrib_path)
            if content and any(p in content.lower() for p in ban_phrases):
                return True
        except Exception:
            pass
    return False


# ── Helper: PR permissions check (was _check_pr_permissions method) ─────────


async def _check_pr_permissions(github, repo) -> bool:
    """True if PR creation is blocked (repo restricts PRs to collaborators).

    Two-tier check: (1) collaborator endpoint, (2) ``allow_forking`` flag on
    the repo metadata. Best-effort — failures return False so a transient
    error doesn't accidentally block the pipeline.
    """
    try:
        user = await github.get_authenticated_user()
        username = user["login"]
        try:
            await github._get(
                f"/repos/{repo.owner}/{repo.name}/collaborators/{username}/permission"
            )
            return False  # we're a collaborator — no restriction
        except Exception as perm_err:
            err_str = str(perm_err).lower()
            if "403" not in err_str:
                # 404 → private/not found → skip; other errors → don't block
                return "404" in err_str

        # Not a collaborator — check if forking is disabled
        try:
            repo_data = await github._get(f"/repos/{repo.owner}/{repo.name}")
            if not repo_data.get("allow_forking", True):
                logger.warning(
                    "%s has forking disabled — PRs restricted to collaborators",
                    repo.full_name,
                )
                return True
        except Exception:
            pass
        return False
    except Exception as e:
        logger.debug("PR permission check failed for %s: %s", repo.full_name, e)
        return False


# ── Helper: CI wait + auto-close (was _check_ci_and_close_if_failed) ────────


async def _check_ci_and_close_if_failed(
    github, memory, repo, pr_result, *, max_wait_sec: int = 90, poll_interval: int = 15
) -> None:
    """Wait for CI checks and auto-close the PR if they fail.

    Polls the PR's head commit for combined status. Returns silently on
    success or timeout. A failure is recorded, but mutation fails closed until
    this legacy path receives a permit-bearing publisher command.
    """
    branch = pr_result.branch_name
    fork_parts = pr_result.fork_full_name.split("/")
    fork_owner = fork_parts[0]
    fork_name = fork_parts[1] if len(fork_parts) > 1 else repo.name

    try:
        branch_data = await github._get(f"/repos/{fork_owner}/{fork_name}/git/ref/heads/{branch}")
        head_sha = branch_data["object"]["sha"]
    except Exception:
        logger.debug("Could not get head SHA for CI check, skipping")
        return

    logger.info("⏳ Waiting for CI checks on PR #%d...", pr_result.pr_number)
    elapsed = 0
    while elapsed < max_wait_sec:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        status = await github.get_combined_status(repo.owner, repo.name, head_sha)
        if status["state"] == "pending":
            continue
        if status["state"] == "success":
            logger.info("✅ CI passed for PR #%d (%d checks)", pr_result.pr_number, status["total"])
            return
        if status["state"] == "failure":
            failed_names = ", ".join(status["failed"])
            logger.warning("❌ CI failed for PR #%d: %s", pr_result.pr_number, failed_names)
            logger.warning(
                "PR #%d was not auto-closed after CI failure: a publisher permit is required",
                pr_result.pr_number,
            )
            await memory.update_pr_status(repo.full_name, pr_result.pr_number, "ci_failed")
            return

    logger.info(
        "⏰ CI check timed out after %ds for PR #%d, leaving open",
        max_wait_sec,
        pr_result.pr_number,
    )


# ── Helper: close linked issues (was _close_linked_issues) ──────────────────


async def _close_linked_issues(
    github, repo, pr_number: int, *, reason: str = "PR was closed"
) -> None:
    """Fail closed until issue closure uses a permit-bearing publisher command."""
    del github
    logger.warning(
        "Linked issues on %s were not closed after PR #%d (%s): a publisher permit is required",
        repo.full_name,
        pr_number,
        reason,
    )


__all__ = [
    "generate_contribution_step",
    "identify_key_files",
    "load_repo_context_step",
    "run_analysis_step",
    "solve_issue_step",
    "submit_pr_step",
    "validate_findings_step",
    "validate_issue_findings_step",
]
