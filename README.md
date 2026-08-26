Micro Entrepreneur Growth Worker 🇮🇳
An agentic, multi-agent system designed to help small Indian micro-entrepreneurs (e.g., local Kirana store owners, freelance service providers) review daily/weekly business activity, identify weak areas, create customer follow-ups, suggest local offers, and track month-on-month improvement.

This project uses a Looping Multi-Agent Architecture where agents autonomously critique and correct each other's work before presenting it to a human for approval.

Demo Video: [Link to be added]GitHub Repository: [Link to be added]

1. Business & System Definition
Goal: Increase the micro-entrepreneur's monthly revenue (in ₹) and customer retention by identifying weak areas, drafting customer follow-ups, suggesting local offers, and tracking month-on-month improvement.
User: Small Indian micro-entrepreneurs who use basic digital ledgers or CSV exports for daily sales.
System: A standalone local CLI tool that acts as a virtual business analyst, operating inside the entrepreneur's local environment.
Inputs: Daily/Weekly CSV of business activity (dummy data: date, customer_id, item, amount_inr), historical data from local SQLite DB.
Decisions: The system can independently identify business weak areas (e.g., "Footfall dropped by 15%"), draft personalized WhatsApp follow-up messages, and conceptualize local promotional offers.
Outputs: A Weekly Action Report (Markdown), drafted customer follow-up messages (JSON/Text), and proposed local offers with estimated discount costs in ₹.
Constraints: The agent must NEVER send WhatsApp/SMS messages directly. It must NEVER make financial commitments or alter the actual POS database. It must only use dummy or synthetic data.
Definition of Done: The workflow successfully ingests a week of dummy CSV data, analyzes trends against last month's SQLite data, generates 3 drafted follow-ups, proposes 1 offer, awaits human approval, and correctly logs the approved state to memory.db.
Success Metric: The workflow reduces the time spent on weekly business review from 2 hours to 15 minutes, and the human approves 80% of the generated follow-ups without edits.
Escalation: The worker stops and asks a human if: the CSV is malformed, a tool fails to execute, the LLM returns a confidence score < 70% on a suggested offer, or before any message is approved for sending.
2. Agentic Architecture: The Multi-Agent Loop
This system is not a linear script; it is a graph of connected, looping agents.

IngestionAgent: Reads the CSV, masks PII, and saves to SQLite. (If data is bad, escalates to Human).
AnalystAgent: Reads the DB, compares month-on-month ₹ revenue, and identifies weak areas. Passes findings to the Marketing Agent.
MarketingAgent: Drafts WhatsApp follow-ups and local offers (in ₹) based on the Analyst's findings.
QACriticAgent (The Loop): Reviews the Marketing Agent's drafts.
If drafts violate rules (e.g., promises >20% discount, missing ₹ symbol, bad tone) -> Loops back to the Marketing Agent with feedback to retry.
If drafts are good -> Passes to Human Approval.
3. Tech Stack (100% Free)
Language: Python 3.10+
Agent Framework: LangGraph (for stateful, looping multi-agent graphs)
LLM: Google Gemini 1.5 Flash (Generous Free Tier API)
Database: SQLite3 (Local, zero-config)
Env: python-dotenv
4. AI Coding Assistant Instructions
This section is for the Antigravity AI tool reading this repository.

ALWAYS read the specs/ directory (AGENTS.md, SOUL.md, TOOLS.md, MEMORY.md) before writing or modifying code.
NEVER use real PII in dummy data or LLM prompts.
ALWAYS use Rupees (₹) for financial data. Never use Dollars.
Build the system iteratively. Do not write the whole system at once; follow the user's step-by-step prompts.
Log all agent actions, tool calls, and QA loops to logs/audit.log.
5. Specification Files (specs/ directory)
The agentic framework reads these markdown files to understand its constraints and tools:

AGENTS.md: Defines the agents (nodes) and their connections/loops (edges).
SOUL.md: Defines the persona, tone, and ethical guardrails for each agent.
TOOLS.md: Defines the exact function contracts (inputs/outputs) the agents are allowed to call.
MEMORY.md: Defines the shared state (scratchpad) and SQLite long-term memory strategy.
6. System Design & Implementation Details
A. Workflow States and Transitions
INIT -> Load shared state and config.
INGEST -> IngestionAgent reads CSV, masks PII. (If invalid -> ESCALATE to Human).
ANALYZE -> AnalystAgent compares this week's ₹ revenue to last month's data.
DRAFT_LOOP (The Agentic Loop):
MarketingAgent generates drafts.
QACriticAgent reviews.
If Rejected -> Loop back to MarketingAgent with qa_feedback.
If Approved -> Exit loop.
HUMAN_REVIEW -> Present to human in CLI for final approval.
EXECUTE -> Save approved drafts to DB.
COMPLETE -> Log monthly comparison and close session.
B. Tool Definitions & Contracts
read_csv(file_path) -> dict: Parses local daily sales data.
mask_pii(raw_data) -> dict: Replaces customer names/phones with customer_id before LLM processing.
query_monthly_summary(month) -> dict: Retrieves past month's total revenue in ₹ from SQLite.
save_drafts(drafts) -> bool: Saves generated WhatsApp/Email messages to local DB for human review.
C. Retrieval / Memory / State Strategy
Shared State: A temporary JSON object passed between agents holding current_csv_path, weak_areas, generated_drafts, and qa_feedback.
Long-Term Memory: SQLite database (data/memory.db) tracks Transactions, CustomerProfiles, and ApprovedDrafts.
Retrieval Rule: Before generating follow-ups, the Analyst Agent queries SQLite for the customer's last 3 visits, passing only aggregated visit counts and total ₹ spent to the LLM.
D. Evaluation Approach
Rule-Based: Verify that the number of generated follow-ups matches the number of lapsed customers identified.
LLM-as-a-Judge: The QACriticAgent scores the generated follow-up messages on a scale of 1-5 for "tone" and "actionability" and rejects scores < 4.
E. Privacy and DPDP (Digital Personal Data Protection) Approach
Local Only: All customer PII (Names, Phone Numbers) stay local in SQLite.
Never Shared: Raw PII is never sent to the external LLM.
Data Minimization: Only aggregated, non-PII data flows to the model (e.g., "Customer #1045 spent ₹500 last month but ₹0 this month. Draft a winback message.").
F. Exception Handling and Retry Logic
Tool Failure: If read_csv fails, retry 2 times. If it fails a 3rd time, transition to ESCALATE and log to audit.log.
LLM Malformed Output: If the LLM returns JSON that doesn't match the expected schema, use a reflection prompt ("Your output was not valid JSON, please fix it") with a max of 2 retries.
G. Human Approval and Escalation Policy
The workflow strictly pauses at the HUMAN_REVIEW state. The CLI prints: "Draft generated. Approve? (y/n)". No execution happens without a y.
H. Audit and Logging Approach
Every tool call, LLM prompt, LLM response, QA retry, and state transition is written to logs/audit.log with a timestamp.
7. Intentional Failure Scenario (Demo Requirement)
During the demo, the system will intentionally be fed a corrupted CSV where the amount_inr column is completely missing.

What happens: The read_csv tool fails, the IngestionAgent catches the exception, retries once, fails again, writes to audit.log, and gracefully transitions to ESCALATE, printing: "Error: Data parsing failed. Missing 'amount_inr' column. Please check the source file." The agent stops without generating any hallucinated financial reports.
Secondary Failure Demo: The MarketingAgent will intentionally hallucinate a 50% discount. The QACriticAgent will catch this, trigger the loop, and force the MarketingAgent to regenerate at <20%.
8. Autonomy vs. Human-Led Tasks
What the current version does autonomously:

Ingesting and validating daily/weekly business activity in ₹.
Masking PII before interacting with the LLM.
Comparing current performance against historical SQLite data.
Drafting personalized follow-up messages and local offers.
Self-correcting via the QA Loop (rejecting and regenerating bad drafts without human intervention).
Logging all retries and state transitions to audit.log.
What remains human-led:

Reviewing and approving the final, QA-approved follow-up messages (HITL).
Actually sending the approved messages (e.g., copy-pasting to WhatsApp).
Executing the promotional offer (e.g., updating shop signage).
Providing a valid CSV if the Ingestion Agent escalates a parsing failure.
9. Next Version Improvements
Direct WhatsApp Business API integration (using free tiers) for autonomous sending upon approval.
Multi-modal analysis: Allow the entrepreneur to take a photo of a paper receipt, using a free vision model to ingest data without needing a CSV.
A/B Testing: Automatically generate two variants of an offer message and track which one yields a higher return in the DB.
10. How to Run
(Instructions to be populated after development)

Clone the repo.
Install requirements: pip install -r requirements.txt
Add .env with GEMINI_API_KEY=your_free_key
Run: `python src/main.py