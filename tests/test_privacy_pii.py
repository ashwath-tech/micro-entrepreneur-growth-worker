import pytest
import sqlite3
from src.tools import mask_pii, read_sql, convert_to_sql, init_db


class TestPrivacyAndPII:
    """Test suite for customer data privacy, PII masking, and security guardrails."""

    def test_mask_pii_transforms_name_and_phone(self, temp_db_path):
        """Verify that customer names and phones are stripped and replaced with customer_id."""
        raw_records = [
            {"txn_id": "1", "date": "2023-10-23", "customer_name": "Ramesh Kumar", "phone": "9876543210", "item": "Chai", "amount_inr": 20.0, "is_return": False},
            {"txn_id": "2", "date": "2023-10-23", "customer_name": "Sita Devi", "phone": "9876512345", "item": "Samosa", "amount_inr": 40.0, "is_return": False}
        ]
        
        masked = mask_pii(raw_records, db_path=temp_db_path)
        
        assert len(masked) == 2
        for rec in masked:
            assert "customer_name" not in rec
            assert "phone" not in rec
            assert "customer_id" in rec
            assert rec["customer_id"].startswith("C")

    def test_mask_pii_consistent_id_for_same_customer(self, temp_db_path):
        """Verify that multiple transactions from the same customer receive the identical customer_id."""
        raw_records = [
            {"txn_id": "1", "date": "2023-10-23", "customer_name": "Ramesh Kumar", "phone": "9876543210", "item": "Chai", "amount_inr": 20.0, "is_return": False},
            {"txn_id": "2", "date": "2023-10-24", "customer_name": "Ramesh Kumar", "phone": "9876543210", "item": "Samosa", "amount_inr": 40.0, "is_return": False}
        ]
        
        masked = mask_pii(raw_records, db_path=temp_db_path)
        assert masked[0]["customer_id"] == masked[1]["customer_id"]

    def test_pii_mapping_persisted_in_db(self, temp_db_path):
        """Verify that mapping is saved in pii_mapping table for reversible human unmasking."""
        raw_record = {"txn_id": "10", "date": "2023-10-23", "customer_name": "Anil Sharma", "phone": "9876500001", "item": "Sweets", "amount_inr": 250.0}
        masked = mask_pii(raw_record, db_path=temp_db_path)
        assigned_id = masked[0]["customer_id"]

        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT customer_name, phone_number FROM pii_mapping WHERE customer_id = ?", (assigned_id,))
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "Anil Sharma"
        assert row[1] == "9876500001"

    def test_security_violation_on_direct_pii_query(self, temp_db_path):
        """Verify that read_sql strictly prevents direct queries to pii_mapping table."""
        with pytest.raises(PermissionError) as exc_info:
            read_sql("SELECT * FROM pii_mapping", db_path=temp_db_path)
        assert "Security Exception" in str(exc_info.value)
        assert "strictly prohibited" in str(exc_info.value)

    def test_scrub_pii_from_text(self, temp_db_path):
        """Verify post-generation scrubbing of real names and 10-digit mobile numbers."""
        from src.tools import scrub_pii_from_text
        raw_msg = "Namaste Ramesh! Call us at 9876543210 for ₹20 off today."
        sanitized = scrub_pii_from_text(raw_msg, db_path=temp_db_path)
        assert "9876543210" not in sanitized
        assert "[PHONE_PROTECTED]" in sanitized
        assert "₹20" in sanitized

    def test_hash_pii_identifier(self):
        """Verify cryptographic salted hashing of customer identifiers."""
        from src.tools import hash_pii_identifier
        h1 = hash_pii_identifier("9876543210")
        h2 = hash_pii_identifier("9876543210")
        h3 = hash_pii_identifier("9876500000")
        assert len(h1) == 64
        assert h1 == h2
        assert h1 != h3

