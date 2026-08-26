# All Tools

### This file contains all the tool details and why it is used for each agent

## IngestionAgent tools:

### 1. read_csv(file_path :str) -> dict

#### Description: Parses a local CSV file containing daily sales data.
#### Input: file_path (String) - Path to the CSV file.
#### Output: (JSON) List of raw transaction dictionaries.
#### Example Output: [{"txn_id": "1", "date": "2023-10-23", "customer_name": "Ramesh", "item": "Samosa", "amount_inr": 40}, "ph_no" : 1234567890]

### 2. mask_pii(raw_data: dict) -> dict

#### Description: Masks sensitive data like phone numbers. Has to be able to be converted back.
#### Input: raw unmasked data dictionary.
#### Output: dictionary of masked data.
#### Example Output: [{"txn_id": "1", "date": "2023-10-23", "customer_name": "Ramesh", "item": "Samosa", "amount_inr": 40}, "ph_no" : abcdefghi]

### 3. human_escalation_csv(str)

#### Description: Raises human interference to fin data which has errors or is of bad quality
#### Input: the issue.
#### Output: after human fixes, human clicks button and Ingestion agent runs again.

### 4. convert_to_sql(dict)

#### Description: Converts dict to SQL and stores it in SQLLite
#### Input: dictionary of what is to be stored.
#### Output: Stored in SQLLite.

## AnalystAgent Tools:

### 1. read_sql(query: str) -> dict

#### Description: Queries the local SQLite database to retrieve masked transaction data.
#### Input: SQL query string.
#### Output: JSON list of database rows.

### 2. analyze_revenue(data: dict) -> dict

#### Description: Compares this week's revenue in ₹ against last month's data.
#### Input: Data from read_sql.
#### Output: JSON summary of revenue drops/growth.

### 3. identify_weakareas(analysis: dict) -> dict

#### Description: Identifies specific weak areas (e.g., drop in repeat visits).
#### Input: Revenue analysis data.
#### Output: JSON list of weak areas.

### 4. analyze_customer(customer_id: str) -> dict

#### Description: Analyzes a specific customer's transaction history to determine preferences (most bought items) and activity patterns (visit count, total spend, recency).
#### Input: customer_id (String).
#### Output: JSON summary of customer preferences and activity.

### 5. generate_graphs(analysis: dict) -> dict

#### Description: Generates visualization graphs for weekly revenue trends and customer-wise activity/preferences.
#### Input: Analysis data JSON.
#### Output: JSON containing graph paths/chart data.

### 6. save_analysis(weak_areas: dict) -> bool

#### Description: Saves the analysis summary, customer insights, and graphs to the shared agent state (MEMORY).
#### Input: Analysis JSON.
#### Output: Boolean True if saved to state.

## MarketingAgent tools:

### 1. save_draft(drafts: dict) -> bool

#### Description: Saves drafted follow-up messages and offers to shared state for the Critique Agent.
#### Input: JSON containing drafted messages and ₹ offers.
#### Output: Boolean True.

## CritiqueAgent tools:

### 1. llm_as_a_judge(drafts: dict) -> dict

#### Description: Uses an LLM to score drafts on tone, ₹ usage, and discount limits.
#### Input: Drafts from Marketing Agent.
#### Output: JSON with "Approved": bool, "Feedback": str, "Target": "Analyst" or "Marketing".

### 2. human_verify(approved_drafts: dict) -> str

#### Description: Pauses the workflow and presents approved drafts to the human in the CLI.
#### Input: Approved drafts JSON.
#### Output: Human input string ("y" or "n").
