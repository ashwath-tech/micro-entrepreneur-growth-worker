import os
import sqlite3
import pytest
from src.learning import (
    init_learning_tables,
    distill_learning_from_feedback,
    record_qa_critic_reflection,
    get_relevant_learnings,
    format_learnings_for_prompt,
    evaluate_campaign_outcomes,
    get_learning_analytics,
    add_custom_rule,
    toggle_rule_status,
    delete_rule,
    reset_learning_memory
)


def test_init_learning_tables(temp_db_path):
    """Verifies that all learning subsystem tables and indices are properly created."""
    init_learning_tables(temp_db_path)
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    conn.close()

    assert "agent_learned_rules" in tables
    assert "human_feedback_history" in tables
    assert "campaign_outcomes" in tables


def test_distill_learning_from_feedback(temp_db_path):
    """Verifies that human edits extract actionable preference rules and save history."""
    orig_msg = "Namaste C001! We miss you. Get 20% discount on sweets."
    edited_msg = "Namaste C001 ji! Fresh sweets ready for you. Get 10% discount (save ₹20) on your order!"

    res = distill_learning_from_feedback(
        agent_name="MarketingAgent",
        original_text=orig_msg,
        edited_text=edited_msg,
        customer_id="C001",
        db_path=temp_db_path
    )

    assert res is not None
    assert res["customer_id"] == "C001"
    assert res["domain"] == "customer_preference"
    assert len(res["rule_description"]) > 5

    # Check database storage
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM human_feedback_history WHERE customer_id='C001'")
    fb_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM agent_learned_rules WHERE customer_id='C001'")
    rule_count = cursor.fetchone()[0]
    conn.close()

    assert fb_count == 1
    assert rule_count == 1


def test_qa_critic_reflection(temp_db_path):
    """Verifies that QA Critic rejection feedback is stored as a self-correction heuristic."""
    feedback = "Draft #1 rejected: Discount of 35% exceeds maximum allowed limit of 20%."
    res = record_qa_critic_reflection("Marketing", feedback, db_path=temp_db_path)

    assert res is not None
    assert res["domain"] == "discount_preference"
    assert res["source"] == "qa_reflection"
    assert "35%" in res["rule_description"]


def test_format_learnings_for_prompt(temp_db_path):
    """Verifies that high-confidence learned rules are formatted into prompt injection strings."""
    add_custom_rule("marketing_tone", "Keep messages under 30 words with polite Hindi tone.", db_path=temp_db_path)
    add_custom_rule("customer_preference", "Customer C001 prefers chai bundles over sweets.", customer_id="C001", db_path=temp_db_path)

    prompt_context = format_learnings_for_prompt("marketing_tone", customer_id="C001", db_path=temp_db_path)
    assert "Learned Preferences" in prompt_context
    assert "under 30 words" in prompt_context or "chai bundles" in prompt_context


def test_evaluate_campaign_outcomes_reinforcement(temp_db_path):
    """Verifies that sales outcomes reinforce confidence scores for active rules."""
    # 1. Add a rule for customer C001
    rule_id = add_custom_rule("customer_preference", "Offer 10% discount on chai for C001", customer_id="C001", db_path=temp_db_path)

    # 2. Add an approved draft
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO approved_drafts (customer_id, message_text, offer_inr, date_approved, rationale) VALUES (?, ?, ?, ?, ?)",
        ("C001", "Namaste C001! Enjoy ₹10 off on Chai", 10.0, "2023-10-20 10:00:00", "Retention")
    )
    conn.commit()
    conn.close()

    # 3. Simulate new incoming sales transactions showing C001 returned and purchased ₹250
    new_sales = [
        {"txn_id": "T999", "date": "2023-10-23", "customer_id": "C001", "item": "Chai", "amount_inr": 250.0, "is_return": False}
    ]

    outcomes = evaluate_campaign_outcomes(new_sales, db_path=temp_db_path)
    assert outcomes["conversions"] == 1
    assert outcomes["revenue_gained"] == 250.0

    # 4. Verify confidence score was boosted (+0.1 from base 1.5 -> 1.6)
    conn = sqlite3.connect(temp_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT confidence_score FROM agent_learned_rules WHERE id=?", (rule_id,))
    new_conf = cursor.fetchone()[0]
    conn.close()

    assert new_conf >= 1.6


def test_toggle_and_delete_rule(temp_db_path):
    """Verifies rule lifecycle management (toggle active/disabled and delete)."""
    rule_id = add_custom_rule("marketing_tone", "Use informal Kirana greetings.", db_path=temp_db_path)

    # Toggle to DISABLED
    status = toggle_rule_status(rule_id, db_path=temp_db_path)
    assert status == "DISABLED"

    # Toggle back to ACTIVE
    status = toggle_rule_status(rule_id, db_path=temp_db_path)
    assert status == "ACTIVE"

    # Delete rule
    deleted = delete_rule(rule_id, db_path=temp_db_path)
    assert deleted is True

    analytics = get_learning_analytics(db_path=temp_db_path)
    assert not any(r["id"] == rule_id for r in analytics["rules"])


def test_reset_learning_memory(temp_db_path):
    """Verifies complete reset of learning memory."""
    add_custom_rule("marketing_tone", "Test rule 1", db_path=temp_db_path)
    reset_learning_memory(temp_db_path)

    analytics = get_learning_analytics(db_path=temp_db_path)
    assert analytics["total_active_rules"] == 0
    assert len(analytics["rules"]) == 0
    assert len(analytics["feedback_history"]) == 0
