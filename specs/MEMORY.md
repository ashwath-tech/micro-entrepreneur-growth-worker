# Memory and State Strategy

### This file defines how the agents share data with each other (Short-term Memory) and how they store permanent data (Long-term Memory).

## 1. Short-Term Memory (Shared Agent State)

During the workflow, agents communicate via a shared JSON state object. Each agent reads from and writes to this state. The state object MUST contain these exact keys:

- current_csv_path (String): The file path of the current week's sales data (set by Human/Ingestion).
- analysis_summary (JSON): The findings from the AnalystAgent. Contains:
  - revenue_trend (String): e.g., "Revenue dropped by 15.0% compared to previous 30-day baseline (₹100.00 vs ₹2900.00)."
  - lapsed_customers (List): e.g., ["C001", "C004"]
  - slow_moving_items (List): e.g., ["Biscuits", "Sweets"]
  - customer_analysis (JSON): e.g., `{"C001": {"preferred_items": ["Chai", "Sweets"], "top_preferred_item": "Sweets", "total_spend_inr": 720.0, "rfm_cohort": "At-Risk High-Value", "rfm_scores": "144"}}`
  - graphs (JSON): Paths or visual data for weekly revenue trend graph and customer-wise activity/preference charts.
- generated_drafts (List of JSON): The drafts created by the MarketingAgent. Contains customer_id, message_text, offer_inr, and rationale.
- qa_feedback (String): Feedback from the CritiqueAgent if a draft is rejected. Empty if approved.
- qa_status (String): "APPROVED" or "REJECTED".
- qa_target (String): Routing destination on loopback ("Marketing", "Analyst", or "Approved").
- retry_count (Integer): Count of loop retries (max 2).
- human_approved (Boolean): Set to True when the human approves the final drafts.
- Rule: Clear generated_drafts and update qa_feedback every time the Marketing/Critique loop retries to prevent context bloat.

## 2. Long-Term Memory (SQLite Database)
Permanent data is stored locally in data/memory.db (using Write-Ahead Logging WAL mode for safe concurrency). This data persists across sessions to allow Month-on-Month (MoM) tracking.

- Table: transactions
  - Stores all masked daily sales with SHA-256 deduplication hashes.
  - Columns: txn_id, date, customer_id, item, amount_inr, is_return.
  - Used by AnalystAgent to calculate baseline revenue, RFM scores, and product affinities.
- Table: pii_mapping (Strictly Local)
  - Stores the reversible mapping for PII.
  - Columns: customer_id (e.g., C001), customer_name, phone_number.
  - Rule: The LLM must NEVER read from this table. Only the Python tool mask_pii writes to it, and only a local Python function reads it when preparing the final human approval output.
- Table: approved_drafts
  - Stores the final messages the human approved ("Yes").
  - Columns: id, customer_id, message_text, offer_inr, date_approved, rationale.
  - Used to track what actions and offers were executed.
- Table: agent_learned_rules
  - Saves preferences and rules the agents pick up over time (like preferred greetings, discount limits, or customer habits).
  - Columns: id, domain, rule_description, customer_id, confidence_score, status, source, created_at, last_reinforced.
- Table: human_feedback_history
  - Keeps a history of how you edited messages so the agents learn your personal writing and offer style.
  - Columns: id, agent_name, customer_id, original_draft, human_edited_draft, edit_delta_summary, timestamp.
- Table: campaign_outcomes
  - Tracks whether customers visited again after getting an offer, so the agents see which deals worked best.
  - Columns: id, customer_id, offer_text, offer_inr, date_sent, returned_within_7d, revenue_gained_inr, evaluated_at.

## 3. Retrieval Strategy
- AnalystAgent Retrieval: Before looking at this week's CSV, it queries the transactions table for the past 30 days to set a baseline, checks customer buying patterns, and pulls saved analysis rules.
- MarketingAgent Retrieval: Reads the analysis summary from the shared state and pulls top learned rules from SQLite to match your preferred tone and discount style.

