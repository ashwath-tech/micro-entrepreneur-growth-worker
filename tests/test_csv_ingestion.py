import os
import tempfile
import pytest
from src.tools import read_csv, human_escalation_csv, mask_pii, convert_to_sql


class TestCSVIngestion:
    """Test suite for CSV format validation, corruption handling, and ingestion logic."""

    def test_read_valid_csv(self, sample_valid_csv):
        """Verify that a well-formatted CSV is parsed into structured records."""
        records = read_csv(sample_valid_csv)
        assert isinstance(records, list)
        assert len(records) == 5
        assert records[0]["txn_id"] == "1"
        assert records[0]["customer_name"] == "Ramesh Kumar"
        assert records[0]["amount_inr"] == 20.0
        assert records[0]["is_return"] is False
        assert records[3]["is_return"] is True

    def test_missing_required_columns(self, sample_corrupted_csv):
        """Verify that missing required columns (e.g. amount_inr, item) raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            read_csv(sample_corrupted_csv)
        assert "Required column" in str(exc_info.value)
        assert "amount_inr" in str(exc_info.value)

    def test_invalid_amount_inr_format(self, sample_invalid_amounts_csv):
        """Verify that non-numeric amounts (e.g. 'FREE') raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            read_csv(sample_invalid_amounts_csv)
        assert "Invalid amount_inr" in str(exc_info.value)

    def test_nonexistent_csv_file(self):
        """Verify that reading a non-existent file path raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError) as exc_info:
            read_csv("data/non_existent_file_xyz.csv")
        assert "File not found" in str(exc_info.value)

    def test_empty_csv_file(self):
        """Verify that an empty file raises ValueError due to missing headers."""
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8") as f:
            empty_path = f.name
        
        try:
            with pytest.raises(ValueError):
                read_csv(empty_path)
        finally:
            if os.path.exists(empty_path):
                os.remove(empty_path)

    def test_malformed_csv_extra_commas(self):
        """Verify that malformed CSV rows with unexpected extra comma-separated columns raise ValueError."""
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8", newline="") as f:
            f.write("txn_id,date,customer_name,phone,item,amount_inr,is_return\n")
            f.write("1,2023-10-23,Ramesh Kumar,9876543210,Chai,20,False,EXTRA_COLUMN_VAL\n")
            bad_path = f.name

        try:
            with pytest.raises(ValueError) as exc_info:
                read_csv(bad_path)
            assert "Malformed CSV row" in str(exc_info.value)
        finally:
            if os.path.exists(bad_path):
                os.remove(bad_path)

    def test_is_return_variations(self):
        """Verify flexible boolean parsing for returns (1/0, yes/no, true/false)."""
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8", newline="") as f:
            f.write("txn_id,date,customer_name,phone,item,amount_inr,is_return\n")
            f.write("1,2023-10-23,Customer A,9800000001,Chai,20,1\n")
            f.write("2,2023-10-23,Customer B,9800000002,Chai,20,0\n")
            f.write("3,2023-10-23,Customer C,9800000003,Chai,20,yes\n")
            f.write("4,2023-10-23,Customer D,9800000004,Chai,20,no\n")
            var_path = f.name

        try:
            records = read_csv(var_path)
            assert records[0]["is_return"] is True
            assert records[1]["is_return"] is False
            assert records[2]["is_return"] is True
            assert records[3]["is_return"] is False
        finally:
            if os.path.exists(var_path):
                os.remove(var_path)

    def test_phone_column_name_alternatives(self):
        """Verify support for 'ph_no', 'phone_number', and 'phone' column names."""
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8", newline="") as f:
            f.write("txn_id,date,customer_name,ph_no,item,amount_inr,is_return\n")
            f.write("1,2023-10-23,Ramesh Kumar,9876543210,Chai,20,False\n")
            ph_path = f.name

        try:
            records = read_csv(ph_path)
            assert records[0]["phone"] == "9876543210"
        finally:
            if os.path.exists(ph_path):
                os.remove(ph_path)

    def test_human_escalation_csv_tool(self):
        """Verify human escalation tool returns expected structure and logs escalation."""
        res = human_escalation_csv("Invalid CSV header detected.")
        assert isinstance(res, dict)
        assert res["status"] == "escalated"
        assert res["resolved"] is False
        assert "Invalid CSV" in res["issue"]
