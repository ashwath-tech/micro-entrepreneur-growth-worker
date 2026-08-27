import pytest
from src.tools import (
    query_mom_revenue,
    analyze_revenue,
    check_trends,
    identify_weakareas,
    analyze_customer,
    generate_graphs
)


class TestAnalystBusinessImpact:
    """Test suite for business intelligence calculations, revenue analysis, and customer insights."""

    def test_query_mom_revenue_baseline(self, temp_db_path):
        """Verify calculation of 30-day baseline revenue and active customer/item sets."""
        summary = query_mom_revenue(days=30, db_path=temp_db_path)
        assert summary["total_baseline_revenue_inr"] > 0
        assert "C001" in summary["active_customer_ids"]
        assert "Chai" in summary["active_items"]
        assert summary["baseline_period_days"] == 30

    def test_analyze_revenue_growth_and_drop(self, temp_db_path):
        """Verify accurate calculation of revenue changes and drop detection."""
        baseline = {"total_baseline_revenue_inr": 1000.0}
        
        # Test Revenue Drop
        current_drop_data = [
            {"amount_inr": 200.0, "is_return": False},
            {"amount_inr": 300.0, "is_return": False}
        ]
        res_drop = analyze_revenue(current_drop_data, baseline_data=baseline, db_path=temp_db_path)
        assert res_drop["current_revenue_inr"] == 500.0
        assert res_drop["revenue_change_pct"] == -50.0
        assert res_drop["is_drop"] is True

        # Test Revenue Growth
        current_growth_data = [
            {"amount_inr": 800.0, "is_return": False},
            {"amount_inr": 700.0, "is_return": False}
        ]
        res_growth = analyze_revenue(current_growth_data, baseline_data=baseline, db_path=temp_db_path)
        assert res_growth["current_revenue_inr"] == 1500.0
        assert res_growth["revenue_change_pct"] == 50.0
        assert res_growth["is_drop"] is False

    def test_check_trends_rupee_formatting(self):
        """Verify that check_trends always outputs formatted descriptions containing ₹ symbol."""
        drop_analysis = {
            "current_revenue_inr": 500.0,
            "baseline_revenue_inr": 1000.0,
            "revenue_change_pct": -50.0,
            "is_drop": True
        }
        trend = check_trends(drop_analysis)
        assert "₹" in trend
        assert "dropped" in trend.lower()
        assert "50.0%" in trend

    def test_identify_weakareas_lapsed_and_slow_items(self, temp_db_path):
        """Verify detection of lapsed customers and slow-moving items."""
        baseline = {
            "active_customer_ids": ["C001", "C002", "C003"],
            "active_items": ["Chai", "Samosa", "Sweets", "Biscuits"]
        }
        # Current data only includes C002 buying Chai
        current_data = [
            {"customer_id": "C002", "item": "Chai", "amount_inr": 20.0}
        ]
        
        weak_areas = identify_weakareas(current_data, baseline_data=baseline, db_path=temp_db_path)
        assert "C001" in weak_areas["lapsed_customers"]
        assert "C003" in weak_areas["lapsed_customers"]
        assert "C002" not in weak_areas["lapsed_customers"]
        
        assert "Samosa" in weak_areas["slow_moving_items"]
        assert "Sweets" in weak_areas["slow_moving_items"]
        assert "Biscuits" in weak_areas["slow_moving_items"]
        assert "Chai" not in weak_areas["slow_moving_items"]

    def test_analyze_customer_preferences_and_status(self, temp_db_path):
        """Verify customer purchase history extraction, top favorite item, and activity status."""
        cust_profile = analyze_customer("C001", db_path=temp_db_path, current_period_customer_ids=["C002"])
        
        assert cust_profile["customer_id"] == "C001"
        assert cust_profile["total_spend_inr"] > 0
        assert cust_profile["visit_count"] >= 1
        assert cust_profile["activity_status"] == "Lapsed"  # Since C001 is not in current_period_customer_ids
        assert len(cust_profile["preferred_items"]) > 0
        assert cust_profile["top_preferred_item"] != "None"

    def test_generate_graphs_datasets(self, temp_db_path):
        """Verify generation of weekly revenue and customer item preference graph structures."""
        current_txns = [{"txn_id": "1", "date": "2023-10-23", "customer_id": "C001", "item": "Chai", "amount_inr": 20.0, "is_return": 0}]
        graphs = generate_graphs(current_txns, db_path=temp_db_path)
        
        assert "weekly_revenue_graph" in graphs
        assert "item_distribution_graph" in graphs
        assert "labels" in graphs["weekly_revenue_graph"]
        assert "datasets" in graphs["weekly_revenue_graph"]

    def test_rfm_customer_segmentation(self, temp_db_path):
        """Verify quantitative RFM scoring and strategic cohort classification."""
        from src.tools import compute_rfm_segmentation
        rfm = compute_rfm_segmentation("C001", db_path=temp_db_path, reference_date="2023-10-23")
        assert "r_score" in rfm
        assert "f_score" in rfm
        assert "m_score" in rfm
        assert "rfm_cohort" in rfm
        assert 1 <= rfm["r_score"] <= 5
        assert 1 <= rfm["f_score"] <= 5
        assert 1 <= rfm["m_score"] <= 5
        assert rfm["monetary_total_inr"] > 0

    def test_market_basket_affinity_rules(self, temp_db_path):
        """Verify Apriori transactional co-occurrence mining (support, confidence, lift)."""
        from src.tools import compute_market_basket_affinity, get_top_affinity_items
        basket_analysis = compute_market_basket_affinity(db_path=temp_db_path)
        assert "total_baskets" in basket_analysis
        assert "top_affinity_rules" in basket_analysis
        assert basket_analysis["total_baskets"] > 0
        
        complements = get_top_affinity_items("Chai", db_path=temp_db_path)
        assert isinstance(complements, list)

    def test_statistical_z_score_revenue_analysis(self, temp_db_path):
        """Verify standard deviation and Z-score calculation during revenue baseline comparison."""
        current_data = [{"amount_inr": 150.0, "date": "2023-10-23", "is_return": False}]
        rev_res = analyze_revenue(current_data, db_path=temp_db_path)
        assert "z_score" in rev_res
        assert "daily_mean_inr" in rev_res
        assert "is_statistically_significant" in rev_res

    def test_inventory_aging_and_velocity(self, temp_db_path):
        """Verify inventory turnover velocity and category classification."""
        baseline = query_mom_revenue(days=30, db_path=temp_db_path)
        current_data = [{"customer_id": "C001", "item": "Chai", "amount_inr": 20.0, "date": "2023-10-23"}]
        weak = identify_weakareas(current_data, baseline_data=baseline, db_path=temp_db_path)
        assert "inventory_classification" in weak
        assert "Chai" in weak["inventory_classification"]
        assert "velocity_per_week" in weak["inventory_classification"]["Chai"]
        assert "category" in weak["inventory_classification"]["Chai"]

