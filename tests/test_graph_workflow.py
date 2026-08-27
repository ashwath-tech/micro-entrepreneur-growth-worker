import os
import pytest
from src.state import create_initial_state, SharedAgentState
from src.graph import (
    create_multi_agent_graph,
    ingestion_node,
    route_ingestion_decision,
    route_qa_decision
)


class TestGraphWorkflow:
    """Test suite for LangGraph multi-agent orchestration and routing."""

    def test_ingestion_node_success(self, sample_valid_csv):
        """Verify that ingestion_node successfully processes valid CSV files."""
        state = create_initial_state(sample_valid_csv)
        result = ingestion_node(state)
        assert result.get("ingestion_status") == "SUCCESS"

    def test_ingestion_node_failure_on_corrupt_csv(self, sample_corrupted_csv):
        """Verify that ingestion_node catches corruption and flags FAILED status."""
        state = create_initial_state(sample_corrupted_csv)
        result = ingestion_node(state)
        assert result.get("ingestion_status") == "FAILED"
        assert result.get("qa_status") == "ESCALATED"
        assert result.get("human_approved") is False

    def test_route_ingestion_decision(self):
        """Verify conditional edge routing based on ingestion status."""
        assert route_ingestion_decision({"ingestion_status": "SUCCESS"}) == "analyst"
        assert route_ingestion_decision({"ingestion_status": "FAILED"}) == "escalation"

    def test_route_qa_decision_approved(self):
        """Verify routing to human_approval when QA is approved."""
        state = {"qa_status": "APPROVED", "retry_count": 0}
        assert route_qa_decision(state) == "human_approval"

    def test_route_qa_decision_rejected_loops(self):
        """Verify retry looping to Marketing or Analyst upon QA rejection."""
        state_mkt = {"qa_status": "REJECTED", "qa_target": "Marketing", "retry_count": 1}
        assert route_qa_decision(state_mkt) == "marketing"

        state_ana = {"qa_status": "REJECTED", "qa_target": "Analyst", "retry_count": 1}
        assert route_qa_decision(state_ana) == "analyst"

    def test_route_qa_decision_max_retries_escalation(self):
        """Verify escalation when retry count exceeds threshold."""
        state = {"qa_status": "REJECTED", "qa_target": "Marketing", "retry_count": 3}
        assert route_qa_decision(state) == "escalation"

    def test_full_graph_compilation_and_structure(self):
        """Verify that the StateGraph compiles without errors and contains expected nodes."""
        graph = create_multi_agent_graph()
        assert graph is not None
        # Verify node names
        node_keys = graph.nodes.keys()
        for expected_node in ["ingestion", "analyst", "marketing", "qa_critic", "human_approval", "escalation"]:
            assert expected_node in node_keys
