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
