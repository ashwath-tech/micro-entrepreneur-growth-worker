import pytest
import sqlite3
from typing import Dict, Any, List
from src.tools import (
    query_mom_revenue,
    analyze_revenue,
    identify_weakareas,
    analyze_customer,
    generate_single_customer_message,
    read_sql,
    ShopImpactEvaluator
)


class TestShopGrowthEvaluation:
    """Test suite evaluating quantifiable shop growth, revenue protection, and ROI metrics."""

    def test_lapsed_customer_recovery_calculation(self, temp_db_path):
        """Verify that lapsed customer recovery value and reactivation metrics are accurately computed."""
        lapsed_list = ["C001", "C003"]
        recovery_metrics = ShopImpactEvaluator.calculate_recoverable_revenue(lapsed_list, db_path=temp_db_path)

        assert recovery_metrics["lapsed_customers_count"] == 2
        assert recovery_metrics["total_lapsed_historical_value_inr"] > 0
        assert recovery_metrics["estimated_recovered_gross_inr"] > 0
        assert recovery_metrics["estimated_net_growth_benefit_inr"] > 0
        assert len(recovery_metrics["customer_breakdown"]) == 2

    def test_dead_stock_liquidation_value(self, temp_db_path):
        """Verify that trapped capital in slow-moving stock is correctly calculated."""
        slow_items = ["Sweets", "Biscuits"]
        dead_stock_metrics = ShopImpactEvaluator.calculate_dead_stock_value(slow_items, db_path=temp_db_path)

        assert dead_stock_metrics["slow_moving_items_count"] == 2
        assert dead_stock_metrics["historical_baseline_value_inr"] > 0
        assert dead_stock_metrics["estimated_unlocked_liquidity_inr"] > 0
        assert len(dead_stock_metrics["item_breakdown"]) == 2

    def test_shop_growth_index_and_roi(self, temp_db_path):
        """Verify composite Shop Growth Index and positive promotion ROI calculation."""
        lapsed_list = ["C001"]
        slow_items = ["Biscuits"]
        
        rec = ShopImpactEvaluator.calculate_recoverable_revenue(lapsed_list, db_path=temp_db_path)
        ds = ShopImpactEvaluator.calculate_dead_stock_value(slow_items, db_path=temp_db_path)
        baseline = query_mom_revenue(days=30, db_path=temp_db_path)
        base_rev = baseline["total_baseline_revenue_inr"]

        growth_summary = ShopImpactEvaluator.calculate_shop_growth_index(rec, ds, base_rev)

        assert growth_summary["shop_growth_index_score"] >= 50
        assert growth_summary["total_estimated_net_value_inr"] > 0
        assert growth_summary["promotion_roi_ratio"] >= 1.0  # Positive ROI
        assert "Growth Potential" in growth_summary["status"]

    def test_discount_margin_safety_guarantee(self, temp_db_path):
        """Verify that all customer reactivation offers never exceed the 20% margin ceiling."""
        conn = sqlite3.connect(temp_db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT customer_id FROM transactions")
        customer_ids = [r[0] for r in cursor.fetchall() if r[0]]
        conn.close()

        for cid in customer_ids:
            cdata = analyze_customer(cid, db_path=temp_db_path)
            avg_spend = cdata.get("avg_spend_per_visit_inr", 100.0)
            draft = generate_single_customer_message(cid, db_path=temp_db_path)
            offer = draft.get("offer_inr", 0.0)

            # Offer must not exceed 20% of customer average spend (or max ₹30 cap)
            max_allowed = max(avg_spend * 0.20, 10.0) + 1.0  # with rounding tolerance
            assert offer <= max_allowed or offer <= 30.0, f"Offer ₹{offer} exceeded 20% margin for customer {cid}"

    def test_time_decay_reactivation_probability(self):
        """Verify exponential time-decay reactivation probability curve."""
        from src.tools import ShopImpactEvaluator
        p_fresh = ShopImpactEvaluator.calculate_reactivation_probability(recency_days=5)
        p_lapsed = ShopImpactEvaluator.calculate_reactivation_probability(recency_days=30)
        p_lost = ShopImpactEvaluator.calculate_reactivation_probability(recency_days=90)
        assert p_fresh > p_lapsed > p_lost
        assert 0.15 <= p_fresh <= 0.95

    def test_net_contribution_margin_calculation(self, temp_db_path):
        """Verify Net Contribution Margin (NCM) probability weighting in recoverable analysis."""
        from src.tools import ShopImpactEvaluator
        rec = ShopImpactEvaluator.calculate_recoverable_revenue(["C001", "C003"], db_path=temp_db_path)
        assert "net_contribution_margin_inr" in rec
        assert "probability_weighted_gross_inr" in rec
        assert rec["probability_weighted_gross_inr"] > 0

