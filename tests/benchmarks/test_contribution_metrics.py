"""Contribution Value metric benchmark fixtures."""

from __future__ import annotations

from contribai.storage.outcomes import ContributionBenchmark


def test_contribution_benchmark_covers_quality_cost_and_maintainer_value() -> None:
    samples = [
        {
            "localization_recall_at_1": 1.0,
            "localization_recall_at_3": 1.0,
            "localization_recall_at_5": 1.0,
            "patch_applied": True,
            "baseline_preserved": True,
            "syntax_passed": True,
            "targeted_tests_passed": True,
            "regression_tests_passed": True,
            "lint_passed": True,
            "typecheck_passed": True,
            "security_passed": True,
            "cost_usd": 0.2,
            "wall_time_sec": 20,
            "accepted": True,
            "merged": True,
            "review_hours": 4,
            "tool_calls": 3,
            "policy_denials": 0,
        },
        {
            "localization_recall_at_1": 0.0,
            "localization_recall_at_3": 1.0,
            "localization_recall_at_5": 1.0,
            "patch_applied": True,
            "baseline_preserved": False,
            "syntax_passed": True,
            "targeted_tests_passed": False,
            "regression_tests_passed": False,
            "lint_passed": True,
            "typecheck_passed": False,
            "security_passed": True,
            "cost_usd": 0.4,
            "wall_time_sec": 40,
            "accepted": False,
            "merged": False,
            "review_hours": 8,
            "tool_calls": 5,
            "policy_denials": 1,
        },
    ]

    metrics = ContributionBenchmark.from_samples(samples)

    assert metrics.sample_count == 2
    assert metrics.localization_recall_at_1 == 0.5
    assert metrics.localization_recall_at_3 == 1.0
    assert metrics.patch_apply_rate == 1.0
    assert metrics.baseline_preservation_rate == 0.5
    assert metrics.targeted_test_pass_rate == 0.5
    assert metrics.avg_cost_usd == 0.3
    assert metrics.median_review_hours == 6.0
    assert metrics.acceptance_rate == 0.5
    assert metrics.merge_rate == 0.5
    assert metrics.policy_denials == 1


def test_empty_benchmark_is_safe_and_zeroed() -> None:
    metrics = ContributionBenchmark.from_samples([])

    assert metrics.sample_count == 0
    assert metrics.merge_rate == 0
    assert metrics.policy_denials == 0
