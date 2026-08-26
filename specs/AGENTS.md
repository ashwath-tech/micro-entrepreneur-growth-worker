# About the Agents

#### This file has all the agents and waht each agent does and how they are connected with each other

## Nodes(Agents)

### 1. Ingestion agent

#### Role : This agent is the first agent that reads the daily/weekly sales CSV, masks PII (names/phones to customer_id), and writes to SQLite.

#### Tools: read_csv, mask_pii, Human_escalation_csv, convert_to_sql

#### Next Step: If the csv is valid then it pii masks the data and call Analyst agent, if the data in csv has errors or is of bad quality, then it is escalated to human

### 2. Analyst Agent:

#### Role : This agent is the second agent that reviews data from sql, identifies weak areas, checks trends, compares revenue, analyzes customer preferences & activity history, generates weekly and customer-wise graphs, and gives all the points to the marketing agent

#### Tools: read_sql, analyze_revenue, check_trends, identify_weakareas, analyze_customer, generate_graphs, save_analysis

#### Next Step: After analyzing, it makes a summary of its findings (including graphs) and gives the findings to the marketting agent by saving it in the state

### 3. Marketing Agent:

#### Role : This agent is the third agent that understands what the Analyst agent has given, and drafts a plan of action which will contain follow up questions to the customer and feedback, suggest offers that will interest the customers

#### Tools: save_draft

#### Next Step: Saves the drafted plan and gives it to the critique agent

### 3. Critique Agent:

#### Role : This agent is the fourth agent that uses judging criterias (LLM-as-a-judge) to critique the draft made by the marketting agent. 

#### Tools: llm_as_a_judge, human_verify

#### Next Step: 
- If REJECTED: check whether it is a data quality problem or marketting strategy problem. loop to analyst agent or marketting agent and create a critique draft that the agents will use depending on the critique. 
- If APPROVED: then  move to human approval


## Workflow States (Edges)
- START -> IngestionAgent
- ingestionAgent -> AnalystAgent (On Success)
- IngestionAgent -> HumanEscalation (On Tool Failure / Missing Data)
- AnalystAgent -> MarketingAgent (On Success)
- MarketingAgent -> CriticAgent (Always)
- CriticAgent -> AnalystAgent (On Rejection - TRIGGERS LOOP)
- CriticAgent -> MarketingAgent (On Rejection - TRIGGERS LOOP)
- CriticAgent -> HumanApproval (On Approval - EXITS LOOP)
