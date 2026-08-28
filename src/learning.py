import os
import json
import sqlite3
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv

load_dotenv()


def get_learning_db_connection(db_path: str = "data/memory.db") -> sqlite3.Connection:
    """
    Returns an optimized SQLite connection for the learning subsystem.
    """
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
    except Exception:
        pass
    return conn


def init_learning_tables(db_path: str = "data/memory.db") -> None:
    """
    Initializes the SQLite tables required for the agent self-learning subsystem.
    Tables:
      - agent_learned_rules: Distilled persistent behavioral rules and preferences
      - human_feedback_history: Historical log of human edits, overrides, and reasons
      - campaign_outcomes: Tracks customer responses to approved messages/offers
    """
    conn = get_learning_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_learned_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,                -- 'marketing_tone', 'discount_preference', 'customer_preference', 'analyst_strategy'
            rule_description TEXT NOT NULL,
            customer_id TEXT,                    -- Optional: specific customer ID (e.g. C001) or NULL for global
            confidence_score REAL DEFAULT 1.0,   -- Scaled 0.1 to 2.0 (boosted on success, penalized on decay)
            status TEXT DEFAULT 'ACTIVE',        -- 'ACTIVE', 'DISABLED', 'PRUNED'
            source TEXT DEFAULT 'human_edit',    -- 'human_edit', 'qa_reflection', 'outcome_reinforcement', 'manual'
            created_at TEXT NOT NULL,
            last_reinforced TEXT
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_learned_domain ON agent_learned_rules(domain, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_learned_cust ON agent_learned_rules(customer_id)")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS human_feedback_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            customer_id TEXT,
            original_draft TEXT NOT NULL,
            human_edited_draft TEXT NOT NULL,
            edit_delta_summary TEXT,
            timestamp TEXT NOT NULL
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_feedback_cust ON human_feedback_history(customer_id)")

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            offer_text TEXT NOT NULL,
            offer_inr REAL,
            date_sent TEXT NOT NULL,
            returned_within_7d INTEGER DEFAULT 0,
            revenue_gained_inr REAL DEFAULT 0.0,
            evaluated_at TEXT
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_cust ON campaign_outcomes(customer_id, date_sent)")

    conn.commit()
    conn.close()


def _get_llm_instance():
    """Helper to lazily import and instantiate the LLM without circular dependencies."""
    try:
        from src.agents import get_gemini_llm
        return get_gemini_llm(temperature=0.1)
    except Exception:
        return None


def distill_learning_from_feedback(
    agent_name: str,
    original_text: str,
    edited_text: str,
    customer_id: Optional[str] = None,
    db_path: str = "data/memory.db"
) -> Optional[Dict[str, Any]]:
    """
    Analyzes the semantic delta between the agent's drafted message and the human's edited version.
    Extracts an actionable, persistent rule and stores it in SQLite.
    """
    if not original_text or not edited_text or original_text.strip() == edited_text.strip():
        return None

    init_learning_tables(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Distill rule using LLM or heuristic fallback
    rule_desc = ""
    llm = _get_llm_instance()

    if llm:
        try:
            prompt = (
                "You are an AI Agent Meta-Learner analyzing a human shop owner's edit to an automated draft.\n"
                f"Agent: {agent_name}\n"
                f"Customer Context: {customer_id or 'General Store'}\n"
                f"Original Agent Output: \"{original_text}\"\n"
                f"Human Edited Version: \"{edited_text}\"\n\n"
                "Task: Identify what the shop owner changed (e.g. tone, wording length, discount amount, product highlight) "
                "and distill ONE clear, actionable rule (maximum 20 words) for future draft generation so the agent aligns with their preference.\n"
                "Example rules:\n"
                "- 'Keep Kirana WhatsApp greetings under 30 words with friendly warm tone.'\n"
                "- 'Offer max 10% discount on Chai and Sweets for regular customers.'\n"
                "- 'Always mention fresh stock availability when drafting tea bundle offers.'\n\n"
                "Return ONLY the concise rule text."
            )
            resp = llm.invoke([
                SystemMessage(content="You extract concise, actionable AI agent behavioral guidelines."),
                HumanMessage(content=prompt)
            ])
            rule_desc = resp.content.strip().replace('"', '').replace("'", "")
            if len(rule_desc) > 200:
                rule_desc = rule_desc[:200]
        except Exception:
            rule_desc = ""

    # Deterministic heuristic fallback if LLM is unavailable
    if not rule_desc:
        orig_len = len(original_text.split())
        edit_len = len(edited_text.split())
        if edit_len < orig_len - 5:
            rule_desc = f"Prefer concise messaging (~{edit_len} words) for WhatsApp follow-ups."
        elif "₹" in edited_text and "₹" not in original_text:
            rule_desc = "Always explicitly display price and savings with the ₹ symbol."
        else:
            rule_desc = f"Align wording with human style: '{edited_text[:60]}...'"

    domain = "customer_preference" if customer_id and customer_id != "STORE_OFFER" else "marketing_tone"

    conn = get_learning_db_connection(db_path)
    cursor = conn.cursor()

    # Log human feedback history
    cursor.execute(
        """
        INSERT INTO human_feedback_history 
        (agent_name, customer_id, original_draft, human_edited_draft, edit_delta_summary, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (agent_name, customer_id, original_text, edited_text, rule_desc, now_str)
    )

    # Insert or update learned rule
    cursor.execute(
        """
        INSERT INTO agent_learned_rules 
        (domain, rule_description, customer_id, confidence_score, status, source, created_at, last_reinforced)
        VALUES (?, ?, ?, 1.2, 'ACTIVE', 'human_edit', ?, ?)
        """,
        (domain, rule_desc, customer_id, now_str, now_str)
    )

    rule_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        "rule_id": rule_id,
        "domain": domain,
        "rule_description": rule_desc,
        "customer_id": customer_id,
        "confidence_score": 1.2,
        "source": "human_edit"
    }


def record_qa_critic_reflection(
    agent_target: str,
    feedback: str,
    db_path: str = "data/memory.db"
) -> Optional[Dict[str, Any]]:
    """
    Captures QA Critic rejection feedback as a self-reflection rule to avoid repeating audit failures.
    """
    if not feedback:
        return None

    init_learning_tables(db_path)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rule_desc = f"Critic Audit Rule: {feedback.strip()}"
    if len(rule_desc) > 200:
        rule_desc = rule_desc[:200]

    domain = "discount_preference" if "discount" in feedback.lower() or "%" in feedback else "marketing_tone"
    if "data" in feedback.lower() or "sql" in feedback.lower() or "trend" in feedback.lower():
        domain = "analyst_strategy"

    conn = get_learning_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO agent_learned_rules 
        (domain, rule_description, customer_id, confidence_score, status, source, created_at, last_reinforced)
        VALUES (?, ?, NULL, 1.0, 'ACTIVE', 'qa_reflection', ?, ?)
        """,
        (domain, rule_desc, now_str, now_str)
    )
    rule_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        "rule_id": rule_id,
        "domain": domain,
        "rule_description": rule_desc,
        "source": "qa_reflection"
    }


def get_relevant_learnings(
    domain: str,
    customer_id: Optional[str] = None,
    limit: int = 5,
    db_path: str = "data/memory.db"
) -> List[Dict[str, Any]]:
    """
    Retrieves high-confidence active learned rules for a specific domain and/or customer.
    """
    init_learning_tables(db_path)
    conn = get_learning_db_connection(db_path)
    cursor = conn.cursor()

    if customer_id:
        cursor.execute(
            """
            SELECT id, domain, rule_description, customer_id, confidence_score, source, created_at
            FROM agent_learned_rules
            WHERE status = 'ACTIVE' AND (domain = ? OR customer_id = ?)
            ORDER BY confidence_score DESC, id DESC
            LIMIT ?
            """,
            (domain, customer_id, limit)
        )
    else:
        cursor.execute(
            """
            SELECT id, domain, rule_description, customer_id, confidence_score, source, created_at
            FROM agent_learned_rules
            WHERE status = 'ACTIVE' AND domain = ?
            ORDER BY confidence_score DESC, id DESC
            LIMIT ?
            """,
            (domain, limit)
        )

    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        results.append({
            "id": r[0],
            "domain": r[1],
            "rule_description": r[2],
            "customer_id": r[3],
            "confidence_score": round(float(r[4]), 2),
            "source": r[5],
            "created_at": r[6]
        })
    return results


def format_learnings_for_prompt(domain: str, customer_id: Optional[str] = None, db_path: str = "data/memory.db") -> str:
    """
    Formats relevant active rules into a concise prompt injection string.
    """
    learnings = get_relevant_learnings(domain, customer_id=customer_id, limit=4, db_path=db_path)
    if not learnings:
        return ""

    lines = ["\n### Learned Preferences & Autonomous Insights (from User History & Experience):"]
    for item in learnings:
        prefix = f"[{item['source'].upper()}]"
        cust_tag = f" (for {item['customer_id']})" if item.get("customer_id") else ""
        lines.append(f"- {prefix}{cust_tag}: {item['rule_description']}")

    lines.append("Apply these learned preferences while adhering strictly to safety guardrails.\n")
    return "\n".join(lines)


def evaluate_campaign_outcomes(new_transactions: List[Dict[str, Any]], db_path: str = "data/memory.db") -> Dict[str, Any]:
    """
    Evaluates real-world sales outcomes against previously sent offers to close the reinforcement loop.
    When customers return and purchase after receiving an offer:
    - Increments revenue_gained and sets returned_within_7d = 1
    - Boosts confidence_score (+0.1) of related learned rules
    - Returns summary metrics
    """
    if not new_transactions:
        return {"evaluated_count": 0, "conversions": 0, "revenue_gained": 0.0}

    init_learning_tables(db_path)
    conn = get_learning_db_connection(db_path)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Fetch pending or recent approved drafts / campaign records within last 14 days
    cursor.execute(
        """
        SELECT id, customer_id, message_text, offer_inr, date_approved
        FROM approved_drafts
        ORDER BY id DESC LIMIT 50
        """
    )
    draft_rows = cursor.fetchall()
    if not draft_rows:
        conn.close()
        return {"evaluated_count": 0, "conversions": 0, "revenue_gained": 0.0}

    conversions = 0
    total_rev = 0.0

    # Build mapping of new transactions per customer
    customer_spend: Dict[str, float] = {}
    for txn in new_transactions:
        cid = str(txn.get("customer_id", "")).strip()
        amt = float(txn.get("amount_inr", 0.0))
        if cid and not txn.get("is_return", False):
            customer_spend[cid] = customer_spend.get(cid, 0.0) + amt

    for d_id, d_cid, d_msg, d_offer, d_date in draft_rows:
        if d_cid in customer_spend:
            spent = customer_spend[d_cid]
            conversions += 1
            total_rev += spent

            # Record outcome
            cursor.execute(
                """
                INSERT INTO campaign_outcomes 
                (customer_id, offer_text, offer_inr, date_sent, returned_within_7d, revenue_gained_inr, evaluated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (d_cid, d_msg, float(d_offer or 0.0), d_date or now_str, spent, now_str)
            )

            # Reinforce confidence score of related customer rules (+0.1)
            cursor.execute(
                """
                UPDATE agent_learned_rules 
                SET confidence_score = MIN(2.0, confidence_score + 0.1), last_reinforced = ?
                WHERE customer_id = ? AND status = 'ACTIVE'
                """,
                (now_str, d_cid)
            )

    conn.commit()
    conn.close()

    return {
        "evaluated_count": len(draft_rows),
        "conversions": conversions,
        "revenue_gained": round(total_rev, 2)
    }


def get_learning_analytics(db_path: str = "data/memory.db") -> Dict[str, Any]:
    """
    Returns high-level statistics and summaries of all agent self-learning activities for the dashboard.
    """
    init_learning_tables(db_path)
    conn = get_learning_db_connection(db_path)
    cursor = conn.cursor()

    # 1. Rules Count
    cursor.execute("SELECT COUNT(*), status FROM agent_learned_rules GROUP BY status")
    status_counts = dict(cursor.fetchall())
    total_active_rules = status_counts.get("ACTIVE", 0)

    # 2. Top Active Rules
    cursor.execute(
        """
        SELECT id, domain, rule_description, customer_id, confidence_score, source, created_at, last_reinforced
        FROM agent_learned_rules
        WHERE status = 'ACTIVE'
        ORDER BY confidence_score DESC, id DESC
        LIMIT 10
        """
    )
    rules_list = []
    for r in cursor.fetchall():
        rules_list.append({
            "id": r[0],
            "domain": r[1],
            "rule_description": r[2],
            "customer_id": r[3] or "Global",
            "confidence_score": round(float(r[4]), 2),
            "source": r[5],
            "created_at": r[6],
            "last_reinforced": r[7] or r[6]
        })

    # 3. Recent Human Feedback Edits
    cursor.execute(
        """
        SELECT id, agent_name, customer_id, original_draft, human_edited_draft, edit_delta_summary, timestamp
        FROM human_feedback_history
        ORDER BY id DESC LIMIT 5
        """
    )
    feedback_list = []
    for r in cursor.fetchall():
        feedback_list.append({
            "id": r[0],
            "agent_name": r[1],
            "customer_id": r[2] or "Store Broadcast",
            "original_draft": r[3],
            "human_edited_draft": r[4],
            "edit_delta_summary": r[5],
            "timestamp": r[6]
        })

    # 4. Campaign Outcomes / Conversion Reinforcement
    cursor.execute(
        """
        SELECT COUNT(*), SUM(returned_within_7d), SUM(revenue_gained_inr)
        FROM campaign_outcomes
        """
    )
    c_total, c_returns, c_rev = cursor.fetchone()
    total_tracked = c_total or 0
    total_returns = c_returns or 0
    total_revenue_recovered = float(c_rev or 0.0)
    conversion_rate = round((total_returns / total_tracked * 100), 1) if total_tracked > 0 else 0.0

    conn.close()

    return {
        "total_active_rules": total_active_rules,
        "rules": rules_list,
        "feedback_history": feedback_list,
        "campaign_metrics": {
            "campaigns_tracked": total_tracked,
            "customers_reactivated": total_returns,
            "conversion_rate_pct": conversion_rate,
            "revenue_recovered_inr": total_revenue_recovered
        }
    }


def add_custom_rule(domain: str, rule_description: str, customer_id: Optional[str] = None, db_path: str = "data/memory.db") -> int:
    """Manually adds a user-specified preference rule."""
    init_learning_tables(db_path)
    conn = get_learning_db_connection(db_path)
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute(
        """
        INSERT INTO agent_learned_rules 
        (domain, rule_description, customer_id, confidence_score, status, source, created_at, last_reinforced)
        VALUES (?, ?, ?, 1.5, 'ACTIVE', 'manual', ?, ?)
        """,
        (domain, rule_description.strip(), customer_id, now_str, now_str)
    )
    rule_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return rule_id


def toggle_rule_status(rule_id: int, db_path: str = "data/memory.db") -> str:
    """Toggles a rule status between 'ACTIVE' and 'DISABLED'."""
    init_learning_tables(db_path)
    conn = get_learning_db_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT status FROM agent_learned_rules WHERE id = ?", (rule_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return "NOT_FOUND"

    new_status = "DISABLED" if row[0] == "ACTIVE" else "ACTIVE"
    cursor.execute("UPDATE agent_learned_rules SET status = ? WHERE id = ?", (new_status, rule_id))
    conn.commit()
    conn.close()
    return new_status


def delete_rule(rule_id: int, db_path: str = "data/memory.db") -> bool:
    """Deletes a rule from the learning memory."""
    init_learning_tables(db_path)
    conn = get_learning_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM agent_learned_rules WHERE id = ?", (rule_id,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def reset_learning_memory(db_path: str = "data/memory.db") -> None:
    """Resets all learned rules and feedback memory."""
    init_learning_tables(db_path)
    conn = get_learning_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM agent_learned_rules")
    cursor.execute("DELETE FROM human_feedback_history")
    cursor.execute("DELETE FROM campaign_outcomes")
    conn.commit()
    conn.close()
