# All Tools

### This file contains all the tool details and why it is used for each agent

## IngestionAgent tools:

### 1. read_csv(file_path: str) -> list
#### Description: Parses a local CSV file containing daily sales data, validates required columns, and canonicalizes phones.
#### Input: file_path (String) - Path to the CSV file.
#### Output: (JSON) List of validated transaction dictionaries.

### 2. mask_pii(raw_data: list) -> list
#### Description: Strips sensitive customer names and phone numbers, replaces them with customer_id (e.g. C001), and stores the reversible mapping locally in SQLite.
#### Input: Raw unmasked transaction list.
#### Output: List of masked transaction dictionaries.

### 3. normalize_phone_number(raw_phone: str) -> str
#### Description: Canonicalizes Indian mobile numbers into clean 10-digit format (+91, leading 0, and symbols removed).
#### Input: Raw phone string.
#### Output: 10-digit clean phone string.

### 4. generate_txn_hash(record: dict) -> str
#### Description: Computes a deterministic SHA-256 fingerprint hash to prevent duplicate sales entries.
#### Input: Transaction dictionary.
#### Output: Hexadecimal hash string.

### 5. convert_to_sql(records: list) -> bool
#### Description: Inserts masked sales transactions into the SQLite database in WAL mode.
#### Input: List of masked transaction dictionaries.
#### Output: Boolean True on success.

### 6. human_escalation_csv(issue: str) -> dict
#### Description: Escalates corrupted or invalid CSV data to human review.
#### Input: Issue description string.
#### Output: Escalation status JSON.


## AnalystAgent Tools:

### 1. read_sql(query: str) -> list
#### Description: Queries the local SQLite database to retrieve masked transaction data (blocks queries to pii_mapping).
#### Input: SQL query string.
#### Output: JSON list of database rows.

### 2. query_mom_revenue(days: int) -> dict
#### Description: Computes baseline metrics (total revenue, active customers, item stats) over the previous 30 days.
#### Input: Baseline days window (default 30).
#### Output: Baseline summary JSON.

### 3. analyze_revenue(current_data: list, baseline_data: dict) -> dict
#### Description: Compares current revenue in ₹ against 30-day baseline with statistical Z-scores.
#### Input: Current transactions and baseline summary.
#### Output: Revenue change %, drop flag, and Z-score significance JSON.

### 4. check_trends(revenue_analysis: dict) -> str
#### Description: Formats a human-readable trend description in ₹ with growth/drop percentages and Z-scores.
#### Input: Revenue analysis dict.
#### Output: Formatted trend string.

### 5. identify_weakareas(current_data: list, baseline_data: dict) -> dict
#### Description: Identifies lapsed customers, slow-moving items, and inventory turnover velocity classifications.
#### Input: Current transactions and baseline summary.
#### Output: JSON with lapsed_customers, slow_moving_items, and inventory_classification.

### 6. analyze_customer(customer_id: str) -> dict
#### Description: Extracts a customer's purchase preferences, visit frequency, spend history, and RFM cohort metrics.
#### Input: customer_id (e.g. 'C001').
#### Output: JSON summary of customer preferences, timeline, and RFM segment.

### 7. compute_rfm_segmentation(customer_id: str) -> dict
#### Description: Computes Recency, Frequency, and Monetary scores (1-5) and assigns strategic cohort categories.
#### Input: customer_id string.
#### Output: RFM score string and cohort category JSON.

### 8. compute_market_basket_affinity() -> dict
#### Description: Mines transactional co-occurrence rules (Support, Confidence, Lift) across shopping baskets.
#### Input: Optional db_path.
#### Output: JSON of pairwise product association rules.

### 9. get_top_affinity_items(target_item: str) -> list
#### Description: Retrieves the highest-affinity complementary items to pair with a given target item for bundle offers.
#### Input: target_item string (e.g. 'Biscuits').
#### Output: List of complementary item recommendations with lift scores.

### 10. generate_graphs(current_data: list, baseline_data: dict) -> dict
#### Description: Generates visualization datasets for weekly revenue trends and customer item preferences.
#### Input: Current transactions, baseline data, customer analyses.
#### Output: JSON containing chart plotting datasets.

### 11. save_analysis(analysis_result: dict) -> bool
#### Description: Saves the analysis summary, customer insights, and graphs to the shared agent state.
#### Input: Analysis summary JSON.
#### Output: Boolean True.


## MarketingAgent Tools:

### 1. save_draft(drafts: list) -> bool
#### Description: Saves drafted WhatsApp messages and promotional offers to shared state for critique review.
#### Input: List of draft dictionaries.
#### Output: Boolean True.

### 2. generate_single_customer_message(customer_id: str) -> dict
#### Description: Generates a personalized WhatsApp message draft for an individual customer using RFM cohort, margin-safe discount, and top affinity recommendations.
#### Input: customer_id string.
#### Output: Draft dictionary with message_text, offer_inr, and rationale.

### 3. calculate_margin_safe_discount(item_name: str, avg_spend: float) -> dict
#### Description: Calculates unit-economic margin-safe discount amounts and breakeven volume multipliers.
#### Input: item_name and avg_spend in ₹.
#### Output: JSON with safe discount %, offer ₹, and breakeven volume multiplier.


## CritiqueAgent & Evaluation Tools:

### 1. llm_as_a_judge(drafts: list) -> dict
#### Description: Audits drafts on rule compliance (Namaste, <50 words, ₹ symbol, ≤20% discount, disguised discount traps, no real names) and multi-criteria 1-5 rubric scoring.
#### Input: List of draft dictionaries.
#### Output: JSON with "Approved": bool, "Feedback": str, "Target": str, "Rubric_Scores": dict.

### 2. scrub_pii_from_text(text: str) -> str
#### Description: Post-processes generated texts to scrub any accidental customer names or 10-digit mobile numbers.
#### Input: Text string.
#### Output: Sanitized text string.

### 3. human_verify(approved_drafts: list) -> str
#### Description: Pauses the workflow and presents approved drafts for human confirmation (y/n).
#### Input: Approved drafts list.
#### Output: Human input decision string ("y" or "n").

### 4. ShopImpactEvaluator
#### Description: Calculates recoverable revenue from lapsed buyers using time-decay return probabilities and unlocked dead-stock working capital.
#### Input: Lapsed customers list and slow-moving items list.
#### Output: Shop Growth Index score (0-100), net revenue value in ₹, and promotion ROI ratio.


## Self-Learning & Memory Tools:

### 1. distill_learning_from_feedback(agent_name, original_text, edited_text, customer_id)
#### Description: Compares your edited message with what the agent wrote, extracting a simple preference rule for future drafts.
#### Input: Original text, edited text, customer_id.
#### Output: Dict with the new rule and confidence score.

### 2. record_qa_critic_reflection(agent_target, feedback)
#### Description: Saves audit rejection feedback as a lesson learned so the agents avoid repeating the same mistake.
#### Input: Agent name and feedback string.
#### Output: Dict with the saved critique rule.

### 3. format_learnings_for_prompt(domain, customer_id)
#### Description: Fetches the most relevant saved rules from SQLite to guide the agent during message drafting.
#### Input: Domain name and optional customer_id.
#### Output: Formatted text of learned preferences.

### 4. evaluate_campaign_outcomes(new_transactions)
#### Description: Checks if customers returned to shop after getting an offer, boosting confidence for strategies that worked.
#### Input: List of new sales transactions.
#### Output: Conversion count and revenue gained.

