import json
import os
import re
import sys
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Union
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass


from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from src.state import SharedAgentState, AnalysisSummary, DraftMessage, QAJudgment
from src.tools import (
    log_audit,
    read_csv,
    mask_pii,
    read_sql,
    query_mom_revenue,
    seed_historical_data,
    analyze_revenue,
    check_trends,
    identify_weakareas,
    analyze_customer,
    compute_rfm_segmentation,
    compute_market_basket_affinity,
    get_top_affinity_items,
    calculate_margin_safe_discount,
    scrub_pii_from_text,
    generate_graphs,
    save_analysis,
    save_draft,
    llm_as_a_judge,
    human_verify,
    human_escalation_csv
)

load_dotenv()


def get_gemini_llm(temperature: float = 0.2) -> Optional[ChatGoogleGenerativeAI]:
    """
    Returns a configured Gemini Flash LLM instance using available free-tier models.
    Supports GEMINI_API_KEY and GOOGLE_API_KEY environment variables.
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key or api_key == "your_free_key":
        return None

    candidate_models = ["gemini-2.5-flash","gemini-3-flash"]
    for model_name in candidate_models:
        try:
            return ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=api_key,
                temperature=temperature,
                max_retries=1,
                timeout=10.0
            )
        except Exception:
            continue
    return None



def extract_json_from_response(text: str) -> Union[Dict[str, Any], List[Any]]:
    """
    Extracts and parses JSON from LLM output, handling markdown code fences.
    """
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        clean_text = match.group(1).strip()
    else:
        clean_text = text

    return json.loads(clean_text)


def analyst_node(state: SharedAgentState) -> Dict[str, Any]:
    """
    AnalystAgent Node:
    1. Reviews masked data from SQLite transactions table via read_sql.
    2. Queries baseline historical sales (previous 30 days) from SQLite DB.
    3. Analyzes revenue changes using analyze_revenue and check_trends tools.
    4. Identifies weak areas (lapsed customers and slow-moving items) using identify_weakareas tool.
    5. Analyzes each customer's preferences and activity history using analyze_customer tool.
    6. Generates visual graph datasets using generate_graphs tool.
    7. Uses Gemini Flash to synthesize findings into a structured analysis summary.
    8. Calls save_analysis tool and updates state['analysis_summary'].
    """
    csv_path = state.get("current_csv_path", "data/sales.csv")
    qa_feedback = state.get("qa_feedback", "")
    log_audit("ANALYST_AGENT:START", f"Starting business analysis for CSV: {csv_path} (Has loop feedback: {bool(qa_feedback)})")
    print(f"\n[AnalystAgent] Analyzing business data from SQLite for {csv_path}...")

    seed_historical_data()

    # 1. Read masked transaction data from SQL
    all_txns = read_sql("SELECT txn_id, date, customer_id, item, amount_inr, is_return FROM transactions ORDER BY date ASC")
    
    # Baseline comparison (previous 30 days)
    baseline = query_mom_revenue(days=30)
    baseline_revenue_inr = baseline.get("total_baseline_revenue_inr", 0.0)
    baseline_customer_ids = baseline.get("active_customer_ids", [])
    baseline_items = baseline.get("active_items", [])

    # Filter transactions for the current period (most recent batch)
    current_txns = all_txns[-3:] if len(all_txns) >= 3 else all_txns
    current_customer_ids = {str(t.get("customer_id")) for t in current_txns if t.get("customer_id")}

    # 2. Tool Calls: analyze_revenue, check_trends, identify_weakareas
    rev_analysis = analyze_revenue(current_txns, baseline)
    calculated_trend = check_trends(rev_analysis)
    weak_areas = identify_weakareas(current_txns, baseline)
    lapsed_customers = weak_areas.get("lapsed_customers", [])
    slow_moving_items = weak_areas.get("slow_moving_items", [])

    # 3. Customer-Wise Analysis (Preferences & Activity)
    all_known_customers = sorted(list(set(baseline_customer_ids).union(current_customer_ids)))
    customer_analysis_dict: Dict[str, Any] = {}
    for cid in all_known_customers:
        customer_analysis_dict[cid] = analyze_customer(cid, current_period_customer_ids=list(current_customer_ids))

    # 4. Generate Visual Graph Datasets
    graphs_data = generate_graphs(current_txns, baseline, customer_analysis_dict)

    analysis_result: Dict[str, Any] = {
        "revenue_trend": calculated_trend,
        "lapsed_customers": lapsed_customers,
        "slow_moving_items": slow_moving_items,
        "customer_analysis": customer_analysis_dict,
        "graphs": graphs_data
    }

    system_prompt = (
        "You are an expert business analyst for a small Indian micro-entrepreneur.\n"
        "Persona and Guardrails:\n"
        "- Tone: Professional, respectful, tailored to local Indian Kirana/micro-business context.\n"
        "- Currency: ALWAYS use Rupees (₹). Never use Dollars.\n"
        "- Privacy: NEVER use real customer names or phones; only use customer_id (e.g., C001).\n"
        "- Task: Analyze the business performance comparing current period to the previous 30-day baseline and summarize customer preferences.\n"
        "- Output format: You MUST reply with ONLY a valid JSON object matching this schema:\n"
        "{\n"
        '  "revenue_trend": "<Clear concise description of revenue growth/drop in ₹ with percentages>",\n'
        '  "lapsed_customers": ["<customer_id_1>", "<customer_id_2>"],\n'
        '  "slow_moving_items": ["<item_name_1>", "<item_name_2>"]\n'
        "}"
    )

    feedback_instruction = ""
    if qa_feedback:
        feedback_instruction = f"\n\nCRITICAL AUDIT FEEDBACK FROM QA CRITIC: {qa_feedback}\nPlease correct your analysis accordingly."

    # Format customer preferences summary for LLM prompt
    cust_pref_lines = []
    for cid, cdata in customer_analysis_dict.items():
        cust_pref_lines.append(f"- {cid}: Status={cdata.get('activity_status')}, Preferred Items={cdata.get('preferred_items')}, Total Spent=₹{cdata.get('total_spend_inr')}")
    cust_pref_summary_str = "\n".join(cust_pref_lines)

    user_context = (
        f"Historical Baseline Data (Past 30 Days):\n"
        f"- Total Baseline Revenue: ₹{baseline_revenue_inr:.2f}\n"
        f"- Active Customers in Baseline: {sorted(list(baseline_customer_ids))}\n"
        f"- Active Items in Baseline: {sorted(list(baseline_items))}\n\n"
        f"Current Period Transactions (Retrieved from SQLite):\n"
        f"- Current Transactions: {json.dumps(current_txns)}\n\n"
        f"Customer Preference & Activity Breakdown:\n{cust_pref_summary_str}\n\n"
        f"Computed Tool Metrics:\n"
        f"- Revenue Analysis: {json.dumps(rev_analysis)}\n"
        f"- Revenue Trend: {calculated_trend}\n"
        f"- Lapsed Customers: {lapsed_customers}\n"
        f"- Slow-Moving Items: {slow_moving_items}\n"
        f"{feedback_instruction}\n\n"
        "Generate the formal analysis summary JSON."
    )

    llm = get_gemini_llm(temperature=0.2)
    log_audit("ANALYST_AGENT:LLM_PROMPT", f"User context prepared for Gemini. LLM configured: {llm is not None}")

    if llm:
        try:
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_context)
            ])
            llm_text = response.content
            log_audit("ANALYST_AGENT:LLM_RESPONSE", f"Raw response: {llm_text[:200]}...")

            parsed = extract_json_from_response(llm_text)
            if isinstance(parsed, dict):
                validated = AnalysisSummary(
                    revenue_trend=str(parsed.get("revenue_trend", calculated_trend)),
                    lapsed_customers=list(parsed.get("lapsed_customers", lapsed_customers)),
                    slow_moving_items=list(parsed.get("slow_moving_items", slow_moving_items)),
                    customer_analysis=customer_analysis_dict,
                    graphs=graphs_data
                )
                analysis_result = validated.model_dump()
                log_audit("ANALYST_AGENT:SUCCESS", f"LLM analysis summary created: {json.dumps(analysis_result)}")

        except Exception as e:
            log_audit("ANALYST_AGENT:LLM_ERROR", f"LLM call failed ({str(e)}). Using deterministic baseline calculation.")
            print(f"[AnalystAgent] Warning: LLM call failed or unavailable ({e}). Using deterministic analytical summary.")
    else:
        log_audit("ANALYST_AGENT:NO_API_KEY", "No valid GEMINI_API_KEY found. Using deterministic calculation.")
        print("[AnalystAgent] Note: GEMINI_API_KEY not configured. Generated deterministic analytical summary.")

    # 5. Save analysis tool call
    save_analysis(analysis_result)
    log_audit("ANALYST_AGENT:COMPLETE", f"Final analysis summary: {json.dumps(analysis_result)}")
    print(f"  ✓ Analysis complete: Trend='{analysis_result['revenue_trend'][:60]}...', Lapsed={analysis_result['lapsed_customers']}, Customers Analyzed={len(customer_analysis_dict)}")

    return {"analysis_summary": analysis_result}


def marketing_node(state: SharedAgentState) -> Dict[str, Any]:
    """
    MarketingAgent Node:
    1. Receives analysis_summary (including customer preferences) from AnalystAgent and any qa_feedback from CritiqueAgent.
    2. Drafts 2 personalized WhatsApp follow-up messages for customer C001 based on their preferred items and 1 local offer concept.
    3. Strictly enforces SOUL.md guardrails:
       - Starts WhatsApp messages with 'Namaste'
       - Strictly under 50 words per message
       - Always uses Rupees (₹) symbol
       - Maximum discount allowed on any offer is ≤ 20%
       - Strictly uses customer_id (C001), never real names/phones
    4. Calls save_draft tool and saves drafts to SharedAgentState.
    """
    log_audit("MARKETING_AGENT:START", "MarketingAgent drafting WhatsApp follow-ups and local offers")

    analysis = state.get("analysis_summary", {})
    revenue_trend = analysis.get("revenue_trend", "Revenue dropped compared to baseline.")
    slow_moving = analysis.get("slow_moving_items", ["Biscuits", "Samosa"])
    customer_analysis = analysis.get("customer_analysis", {})
    qa_feedback = state.get("qa_feedback", "")

    slow_moving_str = ", ".join(slow_moving) if slow_moving else "Biscuits, Samosa"
    target_slow = slow_moving[0] if slow_moving else "Biscuits"
    affinity_complements = get_top_affinity_items(target_slow)
    complement_item = affinity_complements[0]["complementary_item"] if affinity_complements else "Chai"

    # Identify C001 customer preferences and RFM cohort
    c001_info = customer_analysis.get("C001", {})
    c001_prefs = c001_info.get("preferred_items", ["Chai", "Sweets"])
    c001_top_item = c001_info.get("top_preferred_item", "Sweets")
    c001_pref_str = ", ".join(c001_prefs) if c001_prefs else "Chai, Sweets"
    c001_cohort = c001_info.get("rfm_cohort", "At-Risk High-Value")

    default_drafts = [
        {
            "customer_id": "C001",
            "message_text": f"Namaste C001! We value your visits for {c001_pref_str}. Enjoy a 15% discount (up to ₹30) on your favorite fresh {c001_top_item} this week. Visit our shop soon!",
            "offer_inr": 30.0,
            "rationale": f"Retention incentive: Reactivates {c001_cohort.lower()} customer C001 with a 15% discount on their top favorite item ({c001_top_item})."
        },
        {
            "customer_id": "C001",
            "message_text": f"Namaste C001! Hope you are doing well. We have hot fresh {c001_top_item} and Chai ready for you. Get a flat ₹20 off on your purchase above ₹100 today!",
            "offer_inr": 20.0,
            "rationale": f"Order value booster: Encourages a higher order basket (₹100+) for regular buyer C001 with a flat ₹20 discount."
        },
        {
            "customer_id": "STORE_OFFER",
            "message_text": f"Namaste neighbours! Special Kirana Deal: Buy 2 packets of fresh {target_slow} and get 20% off on hot {complement_item} (save ₹10)! Valid till Sunday in ₹.",
            "offer_inr": 10.0,
            "rationale": f"Dead stock liquidation: Clears slow-moving {target_slow} by bundling with high-affinity complementary {complement_item}."
        }
    ]

    system_prompt = (
        "You are a local marketing expert for a small Indian micro-entrepreneur (e.g. Kirana store owner).\n"
        "Persona & Strict Business Guardrails (from SOUL.md):\n"
        "1. Start EVERY WhatsApp message with 'Namaste'.\n"
        "2. Keep EVERY message strictly under 50 words.\n"
        "3. ALWAYS use the Rupee symbol (₹) explicitly in EVERY message text for financial figures/offers.\n"
        "4. Maximum discount allowed on ANY offer is 20% (strictly ≤ 20%).\n"
        "5. NEVER use real customer names (e.g. Ramesh). ONLY use customer_id (e.g. C001).\n"
        "6. Tone: Respectful, polite, warm, and tailored to local Indian neighbourhood context.\n\n"
        "Required Output: A valid JSON list of EXACTLY 3 draft objects:\n"
        "- 2 personalized WhatsApp follow-up messages for customer 'C001' tailored to their preferred items\n"
        "- 1 local promotional offer concept for 'STORE_OFFER' targeting slow-moving items\n"
        "JSON Schema:\n"
        "[\n"
        "  {\n"
        '    "customer_id": "C001",\n'
        '    "message_text": "<Namaste C001! ... message under 50 words with ₹ symbol and ≤20% discount>",\n'
        '    "offer_inr": <estimated discount amount in ₹>,\n'
        '    "rationale": "<Very short 1-sentence summary on why this review/message was suggested based on data findings>"\n'
        "  },\n"
        "  {\n"
        '    "customer_id": "C001",\n'
        '    "message_text": "<Namaste C001! ... second follow-up message under 50 words with ₹ symbol and ≤20% discount>",\n'
        '    "offer_inr": <estimated discount amount in ₹>,\n'
        '    "rationale": "<Very short 1-sentence summary on why this review/message was suggested based on data findings>"\n'
        "  },\n"
        "  {\n"
        '    "customer_id": "STORE_OFFER",\n'
        '    "message_text": "<Namaste neighbours! ... promotional offer bundling slow-moving items with ≤20% discount using ₹ symbol>",\n'
        '    "offer_inr": <estimated discount amount in ₹>,\n'
        '    "rationale": "<Very short 1-sentence summary on why this review/message was suggested based on data findings>"\n'
        "  }\n"
        "]"
    )

    feedback_instruction = ""
    if qa_feedback:
        feedback_instruction = (
            f"\n\nCRITICAL FIX REQUIRED (QA CRITIC REJECTION):\n"
            f"The QA Auditor rejected previous drafts with feedback: '{qa_feedback}'.\n"
            f"You MUST strictly fix this issue! Ensure discounts are ≤20%, no real customer names are used, 'Namaste' starts each message, and the literal ₹ symbol is included in every single message text."
        )

    user_context = (
        f"Analyst Findings:\n"
        f"- Revenue Trend: {revenue_trend}\n"
        f"- Slow-Moving Items (0 sales this week): {slow_moving_str}\n"
        f"- Target Customer: C001 (Preferred items: {c001_pref_str}, Top preference: {c001_top_item})\n"
        f"{feedback_instruction}\n\n"
        "Please draft the 2 WhatsApp follow-ups for C001 and 1 local store offer in JSON."
    )

    drafts = default_drafts
    llm = get_gemini_llm(temperature=0.3)

    if llm:
        try:
            log_audit("MARKETING_AGENT:LLM_PROMPT", f"Sending prompt to Gemini Flash. Has feedback: {bool(qa_feedback)}")
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_context)
            ])
            llm_text = response.content
            log_audit("MARKETING_AGENT:LLM_RESPONSE", f"Raw response: {llm_text[:200]}...")

            parsed = extract_json_from_response(llm_text)
            if isinstance(parsed, list) and len(parsed) >= 3:
                validated_drafts = []
                for item in parsed[:3]:
                    msg = str(item.get("message_text", "")).strip()
                    if not msg.startswith("Namaste"):
                        msg = "Namaste! " + msg
                    cid = str(item.get("customer_id", "C001"))
                    amt = float(item.get("offer_inr", 20.0))
                    rat = str(item.get("rationale", "")).strip()
                    if not rat:
                        if cid == "STORE_OFFER":
                            rat = f"Dead stock liquidation: Bundling slow-moving items with high-demand goods."
                        else:
                            rat = f"Retention incentive: Targeted offer for {cid} based on preference for {c001_top_item}."
                    
                    if "₹" not in msg and "rupee" not in msg.lower():
                        msg += f" (Save ₹{int(amt)} on your order)"
                        
                    validated_drafts.append({
                        "customer_id": cid,
                        "message_text": scrub_pii_from_text(msg),
                        "offer_inr": amt,
                        "rationale": rat
                    })
                drafts = validated_drafts
                log_audit("MARKETING_AGENT:SUCCESS", f"Generated {len(drafts)} drafts from LLM")

        except Exception as e:
            log_audit("MARKETING_AGENT:LLM_ERROR", f"LLM generation failed ({str(e)}). Using guardrailed fallback drafts.")
            print(f"[MarketingAgent] Warning: LLM call failed or unavailable ({e}). Using guardrailed fallback drafts.")
    else:
        log_audit("MARKETING_AGENT:NO_API_KEY", "No valid GEMINI_API_KEY. Using guardrailed fallback drafts.")
        print("[MarketingAgent] Note: GEMINI_API_KEY not configured. Generated guardrailed fallback drafts.")

    save_draft(drafts)
    log_audit("MARKETING_AGENT:COMPLETE", f"Saved drafts to shared state: {json.dumps(drafts)}")

    return {
        "generated_drafts": drafts,
        "qa_feedback": ""
    }


def qa_critic_node(state: SharedAgentState) -> Dict[str, Any]:
    """
    QACriticAgent Node (The Agentic Loop Gatekeeper):
    1. Inspects the drafts in state['generated_drafts'].
    2. Runs rule-based checks + LLM-as-a-judge via llm_as_a_judge tool.
    3. If approved -> updates qa_status to 'APPROVED'.
    4. If rejected -> increments retry_count, sets qa_status to 'REJECTED', and populates qa_feedback.
    """
    drafts = state.get("generated_drafts", [])
    retry_count = state.get("retry_count", 0)
    
    log_audit("QA_CRITIC:START", f"QA Critic evaluating {len(drafts)} drafts (Current retry: {retry_count})")
    print(f"\n[QACriticAgent] Auditing {len(drafts)} generated marketing drafts (Retry #{retry_count})...")

    judgment = llm_as_a_judge(drafts)
    is_approved = judgment.get("Approved", True)
    feedback = judgment.get("Feedback", "")
    target = judgment.get("Target", "Marketing")

    if is_approved:
        log_audit("QA_CRITIC:DECISION_APPROVED", "All drafts approved by QA Critic.")
        print("  ✓ QA Critic Decision: APPROVED! All business guardrails satisfied.")
        return {
            "qa_status": "APPROVED",
            "qa_feedback": "",
            "qa_target": "Approved"
        }
    else:
        new_retry = retry_count + 1
        log_audit("QA_CRITIC:DECISION_REJECTED", f"Drafts rejected (Retry #{new_retry}). Target: {target}. Feedback: {feedback}")
        print(f"  ✗ QA Critic Decision: REJECTED! Feedback: {feedback}")
        print(f"  -> Triggering Agentic Loop: routing back to {target}Agent (Attempt {new_retry}/2)...")
        return {
            "qa_status": "REJECTED",
            "qa_feedback": feedback,
            "qa_target": target,
            "retry_count": new_retry,
            "generated_drafts": []
        }



def human_approval_node(state: SharedAgentState) -> Dict[str, Any]:
    """
    HumanApproval Node:
    Presents QA-approved drafts to human for review and logs approved state to SQLite.
    Prompts the user: 'Approve? (y/n)'
    """
    drafts = state.get("generated_drafts", [])
    log_audit("HUMAN_APPROVAL:START", f"Presenting {len(drafts)} approved drafts to human")

    decision = human_verify(drafts)
    is_approved = (decision in ("y", "yes"))

    if is_approved:
        conn = sqlite3.connect("data/memory.db")
        cursor = conn.cursor()
        date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for d in drafts:
            cursor.execute(
                """
                INSERT INTO approved_drafts (customer_id, message_text, offer_inr, date_approved, rationale)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    d.get("customer_id"),
                    d.get("message_text"),
                    float(d.get("offer_inr", 0.0)),
                    date_now,
                    d.get("rationale", "")
                )
            )
        conn.commit()
        conn.close()
        log_audit("HUMAN_APPROVAL:APPROVED", f"Human approved drafts. Saved {len(drafts)} records to approved_drafts table.")
        print(f"\n[HumanApproval] Success: {len(drafts)} drafts approved and saved to SQLite long-term memory (approved_drafts).")
    else:
        log_audit("HUMAN_APPROVAL:REJECTED", "Human rejected drafts.")
        print("\n[HumanApproval] Human declined approval (n). No drafts were sent or recorded.")

    return {"human_approved": is_approved}


def escalation_node(state: SharedAgentState) -> Dict[str, Any]:
    """
    Escalation Node:
    Called when max QA retries (2) are exceeded or an Ingestion failure occurs.
    """
    qa_feedback = state.get("qa_feedback", "Workflow halted due to unresolved issue.")
    issue = f"Workflow escalated to human: {qa_feedback}"
    log_audit("WORKFLOW_ESCALATION", issue)
    return {"qa_status": "ESCALATED", "human_approved": False}
