# About the Agents

#### This file has all the agents and what each agent does and how they are connected with each other

## Nodes(Agents)

### 1. Ingestion agent

#### Role : This agent is the first agent that reads the daily/weekly sales CSV, validates schema and values, normalies phone numbers (+91), hashes records for checking duplicates, masks PII (names/phones to customer_id), and writes to SQLite.

#### Tools: read_csv, mask_pii, human_escalation_csv, convert_to_sql, normalize_phone_number, generate_txn_hash

#### Next Step: If the CSV is valid, it masks PII data and calls AnalystAgent. If the CSV has errors or missing required columns, it escalates to human review.

### 2. Analyst Agent:

#### Role : This agent is the second agent that reviews data from SQL, calculates statistical Z-score revenue trends, identifies weak areas & inventory turnover velocity, computes RFM customer segmentation, mines Apriori market basket item affinities, generates visual graphs, and saves findings to shared state.

#### Tools: read_sql, query_mom_revenue, analyze_revenue, check_trends, identify_weakareas, analyze_customer, compute_rfm_segmentation, compute_market_basket_affinity, get_top_affinity_items, generate_graphs, save_analysis

#### Next Step: After analyzing, it creates a structured analysis summary (including graphs, RFM cohorts, and affinity bundles) and passes it to MarketingAgent via shared state.

### 3. Marketing Agent:

#### Role : This agent drafts personalized WhatsApp messages and store bundle offers based on customer preferences, safe discount limits, and rules learned from past sessions.

#### Tools: save_draft, generate_single_customer_message, calculate_margin_safe_discount, format_learnings_for_prompt

#### Next Step: Saves the drafted plan and passes it to CritiqueAgent.

### 4. Critique Agent:

#### Role : This agent audits drafts against basic business rules (starts with Namaste, under 50 words, uses ₹ symbol, maximum 20% discount, no real customer names) and scores message quality.

#### Tools: llm_as_a_judge, scrub_pii_from_text, human_verify, record_qa_critic_reflection

#### Next Step: 
- If REJECTED: Saves the issue as a lesson learned so it isn't repeated, and loops back to AnalystAgent (if data issue) or MarketingAgent (if wording issue). Max 2 retries.
- If APPROVED: Passes approved drafts to human review.


## Workflow States (Edges)
- START -> IngestionAgent
- IngestionAgent -> AnalystAgent (On Success)
- IngestionAgent -> HumanEscalation (On Tool Failure / Missing Data)
- AnalystAgent -> MarketingAgent (On Success)
- MarketingAgent -> CriticAgent (Always)
- CriticAgent -> AnalystAgent (On Rejection - TRIGGERS LOOP)
- CriticAgent -> MarketingAgent (On Rejection - TRIGGERS LOOP)
- CriticAgent -> HumanApproval (On Approval - EXITS LOOP)
- HumanApproval -> Saves final plan & learns from any message edits

