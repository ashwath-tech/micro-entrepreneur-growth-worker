from typing import Any, Dict
from langgraph.graph import StateGraph, START, END

from src.state import SharedAgentState
from src.tools import (
    log_audit,
    read_csv,
    mask_pii,
    convert_to_sql,
    seed_historical_data,
    human_escalation_csv
)
from src.agents import (
    analyst_node,
    marketing_node,
    qa_critic_node,
    human_approval_node,
    escalation_node
)


def ingestion_node(state: SharedAgentState) -> Dict[str, Any]:
    """
    IngestionAgent Node:
    Reads current CSV, masks PII, and stores records in SQLite.
    If required columns (such as amount_inr) are missing or corrupted:
    - Catches the error
    - Writes to logs/audit.log
    - Prints escalation message to CLI via human_escalation_csv tool
    - Routes the workflow to escalation/halt without executing downstream agents.
    """
    csv_path = state.get("current_csv_path", "data/sales.csv")
    log_audit("INGESTION_AGENT:START", f"Ingesting data from {csv_path}")
    print(f"\n[IngestionAgent] Reading and validating {csv_path}...")

    seed_historical_data()
    try:
        raw_data = read_csv(csv_path)
        masked_data = mask_pii(raw_data)
        convert_to_sql(masked_data)
        print(f"  ✓ Successfully ingested {len(masked_data)} masked transactions into SQLite.")
        return {"ingestion_status": "SUCCESS"}
    except Exception as e:
        issue = f"Ingestion Failure for '{csv_path}': {str(e)}"
        log_audit("INGESTION_AGENT:ERROR", issue)
        human_escalation_csv(issue)
        return {
            "ingestion_status": "FAILED",
            "qa_feedback": issue,
            "qa_status": "ESCALATED",
            "human_approved": False
        }


def route_ingestion_decision(state: SharedAgentState) -> str:
    """
    Conditional edge router for IngestionAgent.
    - If SUCCESS -> route to 'analyst'
    - If FAILED -> route to 'escalation'
    """
    status = state.get("ingestion_status", "SUCCESS")
    if status == "SUCCESS":
        return "analyst"
    return "escalation"


def route_qa_decision(state: SharedAgentState) -> str:
    """
    Conditional edge router for QACriticAgent.
    - If APPROVED -> route to 'human_approval' (Exits Loop)
    - If REJECTED and retry_count <= 2:
      - If qa_target is 'Analyst' -> loop back to 'analyst' (CriticAgent -> AnalystAgent)
      - If qa_target is 'Marketing' -> loop back to 'marketing' (CriticAgent -> MarketingAgent)
    - If REJECTED and retry_count > 2 -> route to 'escalation'
    """
    status = state.get("qa_status", "PENDING")
    target = state.get("qa_target", "Marketing")
    retries = state.get("retry_count", 0)

    if status == "APPROVED":
        return "human_approval"
    elif status == "REJECTED":
        if retries <= 2:
            if target and target.strip().lower() == "analyst":
                return "analyst"
            return "marketing"
        else:
            print(f"\n[QACriticAgent] Max retry limit exceeded ({retries} retries). Escalating...")
            return "escalation"
    else:
        return "escalation"


def create_multi_agent_graph():
    """
    Builds and compiles the looping multi-agent StateGraph with Ingestion error handling and HITL.
    Workflow (defined in specs/AGENTS.md):
      - START -> IngestionAgent
      - IngestionAgent -> AnalystAgent (On Success)
      - IngestionAgent -> HumanEscalation (On Tool Failure / Missing Data)
      - AnalystAgent -> MarketingAgent (On Success)
      - MarketingAgent -> CriticAgent (Always)
      - CriticAgent -> AnalystAgent (On Rejection - TRIGGERS LOOP)
      - CriticAgent -> MarketingAgent (On Rejection - TRIGGERS LOOP)
      - CriticAgent -> HumanApproval (On Approval - EXITS LOOP)
    """
    builder = StateGraph(SharedAgentState)

    # Register Nodes
    builder.add_node("ingestion", ingestion_node)
    builder.add_node("analyst", analyst_node)
    builder.add_node("marketing", marketing_node)
    builder.add_node("qa_critic", qa_critic_node)
    builder.add_node("human_approval", human_approval_node)
    builder.add_node("escalation", escalation_node)

    # Ingestion Edge & Routing
    builder.add_edge(START, "ingestion")
    builder.add_conditional_edges(
        "ingestion",
        route_ingestion_decision,
        {
            "analyst": "analyst",
            "escalation": "escalation"
        }
    )

    # Downstream Edges
    builder.add_edge("analyst", "marketing")
    builder.add_edge("marketing", "qa_critic")

    # Conditional looping edge from QA Critic (Dual-loop to Analyst or Marketing, or exit to Approval)
    builder.add_conditional_edges(
        "qa_critic",
        route_qa_decision,
        {
            "analyst": "analyst",
            "marketing": "marketing",
            "human_approval": "human_approval",
            "escalation": "escalation"
        }
    )

    builder.add_edge("human_approval", END)
    builder.add_edge("escalation", END)

    return builder.compile()

