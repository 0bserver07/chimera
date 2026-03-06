"""Workflows umbrella — re-exports from ci, review, research, migration, docs, testgen."""
from chimera.workflows.git_workflow import CommitStrategy, GitWorkflow

# CI
from chimera.ci import CIFixWorkflow, FailureInfo, parse_ci_log

# Review
from chimera.review import ReviewComment, ReviewFeedback, ReviewOrchestrator
from chimera.review import Severity as ReviewSeverity

# Research
from chimera.research import Finding, ResearchPlan, Researcher, Source

# Migration
from chimera.migration import MigrationPlan, MigrationPlanner, MigrationRule

# Docs
from chimera.docs import DocGenerator, DocSection

# TestGen
from chimera.testgen import CoverageReport, TestCase, TestGenerator, parse_coverage

__all__ = [
    # Git
    "CommitStrategy",
    "GitWorkflow",
    # CI
    "CIFixWorkflow",
    "FailureInfo",
    "parse_ci_log",
    # Review
    "ReviewComment",
    "ReviewFeedback",
    "ReviewOrchestrator",
    "ReviewSeverity",
    # Research
    "Finding",
    "ResearchPlan",
    "Researcher",
    "Source",
    # Migration
    "MigrationPlan",
    "MigrationPlanner",
    "MigrationRule",
    # Docs
    "DocGenerator",
    "DocSection",
    # TestGen
    "CoverageReport",
    "TestCase",
    "TestGenerator",
    "parse_coverage",
]
