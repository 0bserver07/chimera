# tests/test_strategy_tree_search.py
"""Tests for TreeSearch strategy."""
from __future__ import annotations

import pytest

from chimera.training.strategies.tree_search import SearchNode


class TestSearchNode:
    def test_create_root_node(self):
        node = SearchNode(
            id="root",
            parent_id=None,
            depth=0,
            checkpoint_id="cp0",
            pass_rate=0.0,
            passed=0,
            total=2,
            cost=0.0,
            agent_output="",
            children=[],
        )
        assert node.id == "root"
        assert node.parent_id is None
        assert node.depth == 0
        assert node.is_root
        assert node.is_leaf

    def test_create_child_node(self):
        node = SearchNode(
            id="n1",
            parent_id="root",
            depth=1,
            checkpoint_id="cp1",
            pass_rate=0.5,
            passed=1,
            total=2,
            cost=0.1,
            agent_output="wrote code",
            children=[],
        )
        assert not node.is_root
        assert node.is_leaf

    def test_node_with_children_is_not_leaf(self):
        node = SearchNode(
            id="root",
            parent_id=None,
            depth=0,
            checkpoint_id="cp0",
            pass_rate=0.0,
            passed=0,
            total=2,
            cost=0.0,
            agent_output="",
            children=["n1", "n2"],
        )
        assert not node.is_leaf


from chimera.training.strategies.tree_search import TreeSearch


class TestTreeSearchInit:
    def test_default_params(self):
        ts = TreeSearch()
        assert ts.branch_factor == 3
        assert ts.max_depth == 5
        assert ts.max_nodes == 20
        assert ts.max_cost is None
        assert ts.min_pass_rate == 0.0
        assert ts.branch_fn is None

    def test_custom_params(self):
        ts = TreeSearch(
            branch_factor=5,
            max_depth=10,
            max_nodes=50,
            max_cost=5.0,
            min_pass_rate=0.2,
        )
        assert ts.branch_factor == 5
        assert ts.max_depth == 10
        assert ts.max_nodes == 50
        assert ts.max_cost == 5.0
        assert ts.min_pass_rate == 0.2

    def test_is_strategy_subclass(self):
        from chimera.training.strategies.base import Strategy
        assert issubclass(TreeSearch, Strategy)


import tempfile
from pathlib import Path

from chimera.env.local import LocalEnvironment
from chimera.training.strategies.tree_search import _clone_environment


class TestCloneEnvironment:
    def test_clone_copies_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir)
            env.setup()
            env.write_file("hello.py", "print('hello')")
            env.write_file("sub/deep.txt", "deep")

            cloned = _clone_environment(env, suffix="branch-0")
            try:
                assert cloned.read_file("hello.py") == "print('hello')"
                assert cloned.read_file("sub/deep.txt") == "deep"
                assert cloned.workdir != env.workdir
            finally:
                import shutil
                shutil.rmtree(cloned.workdir)

    def test_clone_is_independent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir)
            env.setup()
            env.write_file("data.txt", "original")

            cloned = _clone_environment(env, suffix="branch-1")
            try:
                cloned.write_file("data.txt", "modified")
                assert env.read_file("data.txt") == "original"
                assert cloned.read_file("data.txt") == "modified"
            finally:
                import shutil
                shutil.rmtree(cloned.workdir)

    def test_clone_workdir_contains_suffix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = LocalEnvironment(workdir=tmpdir)
            env.setup()

            cloned = _clone_environment(env, suffix="branch-42")
            try:
                assert "branch-42" in str(cloned.workdir)
            finally:
                import shutil
                shutil.rmtree(cloned.workdir)
