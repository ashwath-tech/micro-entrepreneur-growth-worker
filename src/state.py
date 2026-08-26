from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict
from pydantic import BaseModel, Field


class AnalysisSummary(BaseModel):
    """Structured summary produced by the AnalystAgent."""
    revenue_trend: str = Field(..., description="Description of revenue change in Rupees (₹) comparing current data to baseline")
    lapsed_customers: List[str] = Field(default_factory=list, description="List of masked customer_ids who have reduced buying or lapsed")
    slow_moving_items: List[str] = Field(default_factory=list, description="List of items with sales last month but 0 or low sales this week")
    customer_analysis: Dict[str, Any] = Field(default_factory=dict, description="Customer preferences and activity history breakdown")
    graphs: Dict[str, Any] = Field(default_factory=dict, description="Graph and visualization data for weekly trend and customer insights")


class DraftMessage(BaseModel):
    """Draft follow-up message created by the MarketingAgent."""
    customer_id: str = Field(..., description="Masked customer identifier, e.g., C001")
    message_text: str = Field(..., description="Drafted message starting with Namaste, under 50 words, using ₹ symbol")
    offer_inr: Optional[float] = Field(None, description="Discount or offer amount in ₹ (max 20%)")
    rationale: Optional[str] = Field(default="", description="Short summary explaining why this review/message was suggested")


class QAJudgment(BaseModel):
    """Structured critique judgment from the QACriticAgent."""
    approved: bool = Field(..., description="True if drafts meet all rules and quality standards, False otherwise")
    feedback: str = Field(default="", description="Specific reason and feedback if rejected")
    target: str = Field(default="Marketing", description="Target agent to loop back to ('Marketing' or 'Analyst')")


class SharedAgentState(TypedDict, total=False):
    """
    Shared Agent State dictionary passed between LangGraph nodes.
    Defined in specs/MEMORY.md.
    """
    current_csv_path: str
    analysis_summary: Dict[str, Any]
    generated_drafts: List[Dict[str, Any]]
    qa_feedback: str
    qa_target: str
    human_approved: bool
    retry_count: int
    qa_status: str
    ingestion_status: str


def create_initial_state(csv_path: str = "data/sales.csv") -> SharedAgentState:
    """Helper to initialize the shared state with required default keys."""
    return {
        "current_csv_path": csv_path,
        "analysis_summary": {
            "revenue_trend": "",
            "lapsed_customers": [],
            "slow_moving_items": [],
            "customer_analysis": {},
            "graphs": {}
        },
        "generated_drafts": [],
        "qa_feedback": "",
        "qa_target": "Marketing",
        "human_approved": False,
        "retry_count": 0,
        "qa_status": "PENDING",
        "ingestion_status": "PENDING"
    }

