import pytest
from src.tools import llm_as_a_judge, generate_single_customer_message


class TestMarketingCriticLoop:
    """Test suite for Marketing Agent drafting rules, SOUL guardrails, and QA Critic verification."""

    def test_critic_approves_valid_draft(self, temp_db_path):
        """Verify that a compliant draft meets all SOUL.md rules and is approved."""
        valid_drafts = [
            {
                "customer_id": "C001",
                "message_text": "Namaste C001! We have fresh Sweets ready for you today. Enjoy a special 15% discount (Save ₹30) on your order. Visit us soon!",
                "offer_inr": 30.0,
                "rationale": "Retention incentive for lapsed regular buyer."
            },
            {
                "customer_id": "STORE_OFFER",
                "message_text": "Namaste neighbours! Special deal: Buy 2 Samosas and save ₹10 (15% off) on hot Chai! Valid till Sunday in ₹.",
                "offer_inr": 10.0,
                "rationale": "Dead stock bundle offer."
            }
        ]

        result = llm_as_a_judge(valid_drafts, db_path=temp_db_path)
        assert result["Approved"] is True
        assert result["Feedback"] == ""

    def test_critic_rejects_missing_namaste(self, temp_db_path):
        """Verify rejection when message does not start with 'Namaste'."""
        bad_draft = [
            {
                "customer_id": "C001",
                "message_text": "Hello C001! Enjoy a 10% discount of ₹20 on fresh Chai today.",
                "offer_inr": 20.0
            }
        ]
        result = llm_as_a_judge(bad_draft, db_path=temp_db_path)
        assert result["Approved"] is False
        assert "Namaste" in result["Feedback"]
        assert result["Target"] == "Marketing"

    def test_critic_rejects_excessive_discount(self, temp_db_path):
        """Verify rejection when discount percentage exceeds the 20% limit."""
        bad_draft = [
            {
                "customer_id": "C001",
                "message_text": "Namaste C001! Huge sale: Get a massive 40% discount (Save ₹50) on all items today!",
                "offer_inr": 50.0
            }
        ]
        result = llm_as_a_judge(bad_draft, db_path=temp_db_path)
        assert result["Approved"] is False
        assert "20%" in result["Feedback"]
        assert result["Target"] == "Marketing"

    def test_critic_rejects_dollar_symbol(self, temp_db_path):
        """Verify rejection when dollar symbol ($) is used instead of Rupee (₹)."""
        bad_draft = [
            {
                "customer_id": "C001",
                "message_text": "Namaste C001! Enjoy $5 discount on your purchase today.",
                "offer_inr": 5.0
            }
        ]
        result = llm_as_a_judge(bad_draft, db_path=temp_db_path)
        assert result["Approved"] is False
        assert "Dollar" in result["Feedback"] or "Rupee" in result["Feedback"]

    def test_critic_rejects_missing_rupee_currency(self, temp_db_path):
        """Verify rejection when currency symbol is entirely omitted."""
        bad_draft = [
            {
                "customer_id": "C001",
                "message_text": "Namaste C001! Enjoy 15% discount on fresh Chai today. Visit soon!",
                "offer_inr": 20.0
            }
        ]
        result = llm_as_a_judge(bad_draft, db_path=temp_db_path)
        assert result["Approved"] is False
        assert "Rupee symbol (₹)" in result["Feedback"]

    def test_critic_rejects_excessive_word_count(self, temp_db_path):
        """Verify rejection when message exceeds 50 words."""
        long_message = "Namaste C001! " + "We have great discounts in ₹ for you today. " * 15
        bad_draft = [
            {
                "customer_id": "C001",
                "message_text": long_message,
                "offer_inr": 20.0
            }
        ]
        result = llm_as_a_judge(bad_draft, db_path=temp_db_path)
        assert result["Approved"] is False
        assert "50-word" in result["Feedback"] or "words" in result["Feedback"].lower()

    def test_critic_rejects_pii_leakage(self, temp_db_path):
        """Verify rejection when real customer name (Ramesh) is leaked in draft instead of customer_id."""
        # Seed Ramesh Kumar in temp db pii_mapping
        bad_draft = [
            {
                "customer_id": "C001",
                "message_text": "Namaste Ramesh! Enjoy a 15% discount (Save ₹20) on your favorite Chai today.",
                "offer_inr": 20.0
            }
        ]
        result = llm_as_a_judge(bad_draft, db_path=temp_db_path)
        assert result["Approved"] is False
        assert "Privacy violation" in result["Feedback"] or "real customer name" in result["Feedback"].lower()

    def test_generate_single_customer_message_guardrails(self, temp_db_path):
        """Verify that generate_single_customer_message generates compliant drafts."""
        msg_draft = generate_single_customer_message("C001", db_path=temp_db_path)
        assert msg_draft["customer_id"] == "C001"
        assert msg_draft["message_text"].startswith("Namaste")
        assert "₹" in msg_draft["message_text"]
        assert len(msg_draft["message_text"].split()) <= 50
        assert msg_draft["offer_inr"] > 0
