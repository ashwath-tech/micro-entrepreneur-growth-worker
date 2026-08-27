import pytest
from fastapi.testclient import TestClient
from src.app import app
from src.tools import init_db, seed_historical_data

client = TestClient(app)


class TestAPIEndpoints:
    """Test suite for FastAPI REST API endpoints."""

    @classmethod
    def setup_class(cls):
        init_db("data/memory.db")
        seed_historical_data("data/memory.db")

    def test_root_index_html(self):
        """Verify that the home page returns HTML status 200."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Micro-Entrepreneur Growth Worker" in response.text

    def test_api_status_endpoint(self):
        """Verify /api/status returns JSON status and workflow logs."""
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "logs" in data
        assert "current_step" in data

    def test_api_add_data_success(self):
        """Verify adding a new sales transaction via /api/add_data."""
        payload = {
            "date": "2023-10-30",
            "customer_name": "Deepak Test",
            "phone": "9811122233",
            "item": "Chai, Biscuit",
            "amount_inr": 50.0,
            "is_return": False
        }
        response = client.post("/api/add_data", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "txn_id" in data

    def test_api_add_data_validation_error(self):
        """Verify that missing fields trigger HTTP 422 Unprocessable Entity."""
        bad_payload = {
            "date": "2023-10-30",
            # missing customer_name, phone, item, amount_inr
        }
        response = client.post("/api/add_data", json=bad_payload)
        assert response.status_code == 422

    def test_api_customers_list(self):
        """Verify /api/customers returns customer profiles."""
        response = client.get("/api/customers")
        assert response.status_code == 200
        data = response.json()
        assert "customers" in data
        assert "count" in data
        assert data["count"] >= 1

    def test_api_customer_detail(self):
        """Verify /api/customer/{customer_id} returns detailed profile."""
        response = client.get("/api/customer/C001")
        assert response.status_code == 200
        data = response.json()
        assert data["customer_id"] == "C001"
        assert "customer_name" in data
        assert "preferred_items" in data

    def test_api_customer_analyze_deep(self):
        """Verify /api/customer/{customer_id}/analyze returns strategic insights."""
        response = client.post("/api/customer/C001/analyze")
        assert response.status_code == 200
        data = response.json()
        assert "segment" in data
        assert "churn_risk" in data
        assert "recommended_action" in data

    def test_api_customer_generate_message(self):
        """Verify /api/customer/{customer_id}/generate_message generates a WhatsApp draft."""
        response = client.post("/api/customer/C001/generate_message")
        assert response.status_code == 200
        data = response.json()
        assert data["customer_id"] == "C001"
        assert data["message_text"].startswith("Namaste")
        assert "₹" in data["message_text"]

    def test_api_customer_send_message(self):
        """Verify /api/customer/{customer_id}/send_message records message in SQLite."""
        payload = {
            "message_text": "Namaste C001! Enjoy ₹20 discount on Chai today.",
            "offer_inr": 20.0,
            "rationale": "Direct loyalty test message"
        }
        response = client.post("/api/customer/C001/send_message", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "message_id" in data
        assert "whatsapp_url" in data

    def test_api_day_details(self):
        """Verify /api/day_details/{date} returns transactions for a date."""
        response = client.get("/api/day_details/2023-10-23")
        assert response.status_code == 200
        data = response.json()
        assert data["date"] == "2023-10-23"
        assert "transactions" in data
        assert "total_revenue_inr" in data

    def test_api_shop_impact(self):
        """Verify /api/shop_impact returns shop growth evaluation metrics."""
        response = client.get("/api/shop_impact")
        assert response.status_code == 200
        data = response.json()
        assert "growth_index_score" in data
        assert "estimated_net_value_inr" in data
        assert "promotion_roi_ratio" in data
        assert data["growth_index_score"] >= 50

    def test_api_run_system_tests(self):
        """Verify /api/tests/run executes the feature diagnostics and returns results."""
        response = client.post("/api/tests/run")
        assert response.status_code == 200
        data = response.json()
        assert "summary" in data
        assert "shop_impact" in data
        assert "test_results" in data
        assert data["summary"]["passed"] >= 10
