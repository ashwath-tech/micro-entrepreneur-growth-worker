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

## 3. Retrieval Strategy
- AnalystAgent Retrieval: Before analyzing this week's CSV, the AnalystAgent MUST query the transactions table for the previous 30 days to establish a baseline for comparison, query customer transaction history for RFM cohorts, and compute Apriori co-occurrence rules.
- MarketingAgent Retrieval: Does not query SQLite directly. It relies entirely on the analysis_summary passed through the Shared Agent State.

