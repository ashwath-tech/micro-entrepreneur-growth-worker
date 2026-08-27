import pytest
import sqlite3
from typing import Dict, Any, List
from src.tools import (
    query_mom_revenue,
    analyze_revenue,
    identify_weakareas,
    analyze_customer,
    generate_single_customer_message,
    read_sql
)


class ShopImpactEvaluator:
    """
    Evaluates and quantifies how much the Micro-Entrepreneur Growth Worker
    protects, recovers, and grows shop revenue.
    """

    @staticmethod
    def calculate_recoverable_revenue(lapsed_customers: List[str], db_path: str = "data/memory.db") -> Dict[str, Any]:
        """
        Calculates the historical value of lapsed customers and estimated recoverable revenue
        through targeted WhatsApp reactivation offers.
        """
        total_lapsed_historical_spend = 0.0
        total_offer_cost = 0.0
        customer_breakdown = []

        for cid in lapsed_customers:
            cdata = analyze_customer(cid, db_path=db_path)
            hist_spend = cdata.get("total_spend_inr", 0.0)
            avg_basket = cdata.get("avg_spend_per_visit_inr", 0.0)
            
            # Generate planned reactivation offer
            draft = generate_single_customer_message(cid, db_path=db_path)
            offer_amt = draft.get("offer_inr", 0.0)

            total_lapsed_historical_spend += hist_spend
            total_offer_cost += offer_amt

            customer_breakdown.append({
                "customer_id": cid,
                "historical_spend_inr": hist_spend,
                "avg_basket_inr": avg_basket,
                "reactivation_offer_inr": offer_amt,
                "estimated_return_spend_inr": max(avg_basket, 100.0)
            })

        # Estimated recovery assumes at least 1 return visit basket minus offer cost
        estimated_recovered_gross = sum(cb["estimated_return_spend_inr"] for cb in customer_breakdown)
        estimated_net_benefit = estimated_recovered_gross - total_offer_cost

        return {
            "lapsed_customers_count": len(lapsed_customers),
            "total_lapsed_historical_value_inr": round(total_lapsed_historical_spend, 2),
            "total_reactivation_incentive_inr": round(total_offer_cost, 2),
            "estimated_recovered_gross_inr": round(estimated_recovered_gross, 2),
            "estimated_net_growth_benefit_inr": round(estimated_net_benefit, 2),
            "customer_breakdown": customer_breakdown
        }

    @staticmethod
    def calculate_dead_stock_value(slow_moving_items: List[str], db_path: str = "data/memory.db") -> Dict[str, Any]:
        """
        Calculates trapped working capital in slow-moving inventory and the potential unlocked
        liquidity from bundle offers.
        """
        baseline = query_mom_revenue(days=30, db_path=db_path)
        item_stats = baseline.get("item_stats", {})

        total_trapped_revenue_potential = 0.0
        item_breakdown = []

        for item in slow_moving_items:
            stats = item_stats.get(item, {})
            hist_rev = stats.get("revenue_inr", 0.0)
            units = stats.get("units_sold", 0)

            total_trapped_revenue_potential += hist_rev
            item_breakdown.append({
                "item": item,
                "historical_units_sold": units,
                "historical_revenue_inr": hist_rev,
                "bundle_liquidation_potential_inr": round(hist_rev * 0.80, 2)  # with 20% bundle discount
            })

        unlocked_liquidity = sum(ib["bundle_liquidation_potential_inr"] for ib in item_breakdown)

        return {
            "slow_moving_items_count": len(slow_moving_items),
            "historical_baseline_value_inr": round(total_trapped_revenue_potential, 2),
            "estimated_unlocked_liquidity_inr": round(unlocked_liquidity, 2),
            "item_breakdown": item_breakdown
        }

    @staticmethod
    def calculate_shop_growth_index(
        recoverable_analysis: Dict[str, Any],
        dead_stock_analysis: Dict[str, Any],
        baseline_revenue_inr: float
    ) -> Dict[str, Any]:
        """
        Computes the overall Shop Growth Impact Index (0-100%) and business ROI metrics.
        """
        net_benefit = (
            recoverable_analysis.get("estimated_net_growth_benefit_inr", 0.0) +
            dead_stock_analysis.get("estimated_unlocked_liquidity_inr", 0.0)
        )
        total_costs = recoverable_analysis.get("total_reactivation_incentive_inr", 1.0)
        
        roi_ratio = round(net_benefit / max(total_costs, 1.0), 2)
        
        # Calculate impact as % of monthly baseline revenue
        potential_revenue_uplift_pct = round(
            (net_benefit / max(baseline_revenue_inr, 100.0)) * 100, 2
        ) if baseline_revenue_inr > 0 else 0.0

        # Composite score
        growth_index = min(100, int(50 + (potential_revenue_uplift_pct * 2)))

        return {
            "shop_growth_index_score": growth_index,
            "potential_revenue_uplift_pct": potential_revenue_uplift_pct,
            "total_estimated_net_value_inr": round(net_benefit, 2),
            "promotion_roi_ratio": roi_ratio,
            "status": "High Growth Potential" if growth_index >= 70 else "Moderate Growth Potential"
        }


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
