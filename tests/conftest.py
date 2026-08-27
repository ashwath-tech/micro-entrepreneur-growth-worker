import os
import sys
import tempfile
import sqlite3
import pytest
from typing import Generator, Dict, Any, List

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tools import init_db, seed_historical_data


@pytest.fixture
def temp_db_path() -> Generator[str, None, None]:
    """Provides a temporary, isolated SQLite database file for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    init_db(db_path)
    seed_historical_data(db_path)
    
    yield db_path
    
    # Cleanup
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except Exception:
            pass


@pytest.fixture
def sample_valid_csv() -> Generator[str, None, None]:
    """Creates a temporary valid CSV file containing standard sales data."""
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8", newline="") as f:
        f.write("txn_id,date,customer_name,phone,item,amount_inr,is_return\n")
        f.write("1,2023-10-23,Ramesh Kumar,9876543210,Chai,20,False\n")
        f.write("2,2023-10-23,Sita Devi,9876512345,Samosa,40,False\n")
        f.write("3,2023-10-24,Vikram Singh,9811223344,Milk (1L),65,False\n")
        f.write("4,2023-10-24,Ramesh Kumar,9876543210,Sweets,300,True\n")
        f.write("5,2023-10-25,Pooja Patel,9876500002,Biscuits,50,False\n")
        csv_path = f.name
    
    yield csv_path
    
    if os.path.exists(csv_path):
        try:
            os.remove(csv_path)
        except Exception:
            pass


@pytest.fixture
def sample_corrupted_csv() -> Generator[str, None, None]:
    """Creates a temporary CSV with missing required columns."""
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8", newline="") as f:
        f.write("txn_id,date,customer_name\n")  # missing item and amount_inr
        f.write("1,2023-10-23,Ramesh Kumar\n")
        csv_path = f.name
    
    yield csv_path
    
    if os.path.exists(csv_path):
        try:
            os.remove(csv_path)
        except Exception:
            pass


@pytest.fixture
def sample_invalid_amounts_csv() -> Generator[str, None, None]:
    """Creates a temporary CSV with non-numeric amount_inr values."""
    with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8", newline="") as f:
        f.write("txn_id,date,customer_name,phone,item,amount_inr,is_return\n")
        f.write("1,2023-10-23,Ramesh Kumar,9876543210,Chai,FREE,False\n")
        csv_path = f.name
    
    yield csv_path
    
    if os.path.exists(csv_path):
        try:
            os.remove(csv_path)
        except Exception:
            pass
