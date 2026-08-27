import os
import sys
import time
import argparse
import unittest
import tempfile
import sqlite3
from typing import Dict, Any, List

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.tools import (
    init_db,
    seed_historical_data,
    read_csv,
    mask_pii,
    convert_to_sql,
    read_sql,
    query_mom_revenue,
    analyze_revenue,
    check_trends,
    identify_weakareas,
    analyze_customer,
    generate_single_customer_message,
    llm_as_a_judge,
    human_escalation_csv,
    ShopImpactEvaluator
)
from src.state import create_initial_state
from src.graph import create_multi_agent_graph, route_ingestion_decision, route_qa_decision


# Terminal Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


class TestReport:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.total = 0
        self.results: List[Dict[str, Any]] = []

    def record(self, category: str, name: str, success: bool, message: str = "", duration_ms: float = 0.0):
        self.total += 1
        if success:
            self.passed += 1
        else:
            self.failed += 1
        
        self.results.append({
            "category": category,
            "name": name,
            "success": success,
            "message": message,
            "duration_ms": round(duration_ms, 2)
        })


def run_all_diagnostics() -> Dict[str, Any]:
    """
    Executes all internal feature diagnostic tests and evaluates shop help metrics.
    Returns structured results for CLI and Web API consumers.
    """
    report = TestReport()
    start_time = time.time()

    # Create temporary isolated database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        test_db = f.name

    init_db(test_db)
    seed_historical_data(test_db)

    # -------------------------------------------------------------
    # Suite 1: CSV Ingestion & Format Robustness
    # -------------------------------------------------------------
    # Test 1.1: Valid CSV reading
    t0 = time.time()
    try:
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8", newline="") as f:
            f.write("txn_id,date,customer_name,phone,item,amount_inr,is_return\n")
            f.write("1,2023-10-23,Ramesh Kumar,9876543210,Chai,20.0,False\n")
            f.write("2,2023-10-23,Sita Devi,9876512345,Samosa,40.0,False\n")
            valid_path = f.name
        
        records = read_csv(valid_path)
        assert len(records) == 2 and records[0]["amount_inr"] == 20.0
        report.record("CSV Ingestion", "Read Valid Sales CSV", True, "Successfully parsed 2 transactions", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("CSV Ingestion", "Read Valid Sales CSV", False, str(e), (time.time() - t0) * 1000)
    finally:
        if os.path.exists(valid_path):
            os.remove(valid_path)

    # Test 1.2: Missing required column rejection (Wrong format)
    t0 = time.time()
    try:
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8", newline="") as f:
            f.write("txn_id,date,customer_name\n")  # Missing amount_inr and item
            f.write("1,2023-10-23,Ramesh Kumar\n")
            bad_path = f.name
        
        try:
            read_csv(bad_path)
            report.record("CSV Ingestion", "Reject Missing Columns (Wrong Format)", False, "Failed to raise ValueError on missing column", (time.time() - t0) * 1000)
        except ValueError as ve:
            assert "Required column" in str(ve)
            report.record("CSV Ingestion", "Reject Missing Columns (Wrong Format)", True, f"Correctly caught: {ve}", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("CSV Ingestion", "Reject Missing Columns (Wrong Format)", False, str(e), (time.time() - t0) * 1000)
    finally:
        if os.path.exists(bad_path):
            os.remove(bad_path)

    # Test 1.3: Non-numeric amount validation
    t0 = time.time()
    try:
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8", newline="") as f:
            f.write("txn_id,date,customer_name,phone,item,amount_inr,is_return\n")
            f.write("1,2023-10-23,Ramesh Kumar,9876543210,Chai,FREE_ITEM,False\n")
            inv_amt_path = f.name
        
        try:
            read_csv(inv_amt_path)
            report.record("CSV Ingestion", "Reject Non-Numeric Amount", False, "Failed to catch invalid amount string", (time.time() - t0) * 1000)
        except ValueError as ve:
            assert "Invalid amount_inr" in str(ve)
            report.record("CSV Ingestion", "Reject Non-Numeric Amount", True, f"Correctly caught: {ve}", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("CSV Ingestion", "Reject Non-Numeric Amount", False, str(e), (time.time() - t0) * 1000)
    finally:
        if os.path.exists(inv_amt_path):
            os.remove(inv_amt_path)

    # Test 1.4: Non-existent file error
    t0 = time.time()
    try:
        try:
            read_csv("data/does_not_exist_9999.csv")
            report.record("CSV Ingestion", "Handle Missing File Gracefully", False, "Failed to raise FileNotFoundError", (time.time() - t0) * 1000)
        except FileNotFoundError:
            report.record("CSV Ingestion", "Handle Missing File Gracefully", True, "Correctly caught FileNotFoundError", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("CSV Ingestion", "Handle Missing File Gracefully", False, str(e), (time.time() - t0) * 1000)

    # -------------------------------------------------------------
    # Suite 2: Privacy & PII Protection
    # -------------------------------------------------------------
    t0 = time.time()
    try:
        raw_rec = [{"txn_id": "1", "date": "2023-10-23", "customer_name": "Ramesh Kumar", "phone": "9876543210", "item": "Chai", "amount_inr": 20.0}]
        masked = mask_pii(raw_rec, db_path=test_db)
        assert "customer_name" not in masked[0]
        assert "phone" not in masked[0]
        assert masked[0]["customer_id"].startswith("C")
        report.record("Privacy & PII", "Mask Real Names & Phone Numbers", True, f"Masked to {masked[0]['customer_id']}", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("Privacy & PII", "Mask Real Names & Phone Numbers", False, str(e), (time.time() - t0) * 1000)

    t0 = time.time()
    try:
        try:
            read_sql("SELECT * FROM pii_mapping", db_path=test_db)
            report.record("Privacy & PII", "Block Direct SQL Access to PII Mapping", False, "Failed to block PII table query", (time.time() - t0) * 1000)
        except PermissionError:
            report.record("Privacy & PII", "Block Direct SQL Access to PII Mapping", True, "Successfully raised PermissionError security block", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("Privacy & PII", "Block Direct SQL Access to PII Mapping", False, str(e), (time.time() - t0) * 1000)

    # -------------------------------------------------------------
    # Suite 3: Business Analysis & Revenue Trend
    # -------------------------------------------------------------
    t0 = time.time()
    try:
        base = query_mom_revenue(days=30, db_path=test_db)
        assert base["total_baseline_revenue_inr"] > 0
        report.record("Business Analysis", "Compute 30-Day Historical Baseline", True, f"Baseline total: ₹{base['total_baseline_revenue_inr']}", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("Business Analysis", "Compute 30-Day Historical Baseline", False, str(e), (time.time() - t0) * 1000)

    t0 = time.time()
    try:
        curr_drop = [{"amount_inr": 100.0, "is_return": False}]
        res = analyze_revenue(curr_drop, baseline_data=base, db_path=test_db)
        trend_msg = check_trends(res)
        assert "₹" in trend_msg and "dropped" in trend_msg.lower()
        report.record("Business Analysis", "Spot Revenue Drop in Rupees (₹)", True, trend_msg, (time.time() - t0) * 1000)
    except Exception as e:
        report.record("Business Analysis", "Spot Revenue Drop in Rupees (₹)", False, str(e), (time.time() - t0) * 1000)

    t0 = time.time()
    try:
        weak = identify_weakareas(curr_drop, baseline_data=base, db_path=test_db)
        assert len(weak["lapsed_customers"]) > 0
        report.record("Business Analysis", "Identify Lapsed Customers & Slow Stock", True, f"Lapsed: {weak['lapsed_customers']}, Slow items: {weak['slow_moving_items']}", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("Business Analysis", "Identify Lapsed Customers & Slow Stock", False, str(e), (time.time() - t0) * 1000)

    # -------------------------------------------------------------
    # Suite 4: Marketing Guardrails & QA Critic Self-Correction
    # -------------------------------------------------------------
    t0 = time.time()
    try:
        good_draft = [{"customer_id": "C001", "message_text": "Namaste C001! Enjoy a 15% discount (Save ₹25) on your favorite Chai. Visit today!", "offer_inr": 25.0}]
        critique = llm_as_a_judge(good_draft, db_path=test_db)
        assert critique["Approved"] is True
        report.record("Marketing & QA Critic", "QA Critic Approves Compliant Drafts", True, "All rules (Namaste, <50 words, ₹ symbol, <=20% discount) passed", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("Marketing & QA Critic", "QA Critic Approves Compliant Drafts", False, str(e), (time.time() - t0) * 1000)

    t0 = time.time()
    try:
        excess_discount_draft = [{"customer_id": "C001", "message_text": "Namaste C001! Get 50% off on your purchase in ₹ today!", "offer_inr": 100.0}]
        critique_bad = llm_as_a_judge(excess_discount_draft, db_path=test_db)
        assert critique_bad["Approved"] is False and "20%" in critique_bad["Feedback"]
        report.record("Marketing & QA Critic", "Reject Offers Exceeding 20% Discount Ceiling", True, f"Correctly rejected with feedback: '{critique_bad['Feedback']}'", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("Marketing & QA Critic", "Reject Offers Exceeding 20% Discount Ceiling", False, str(e), (time.time() - t0) * 1000)

    t0 = time.time()
    try:
        dollar_draft = [{"customer_id": "C001", "message_text": "Namaste C001! Enjoy $10 off today on your order.", "offer_inr": 10.0}]
        critique_dollar = llm_as_a_judge(dollar_draft, db_path=test_db)
        assert critique_dollar["Approved"] is False
        report.record("Marketing & QA Critic", "Reject Non-Rupee Currency ($ Dollar)", True, f"Correctly rejected: '{critique_dollar['Feedback']}'", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("Marketing & QA Critic", "Reject Non-Rupee Currency ($ Dollar)", False, str(e), (time.time() - t0) * 1000)

    # -------------------------------------------------------------
    # Suite 5: Multi-Agent Graph Routing
    # -------------------------------------------------------------
    t0 = time.time()
    try:
        g = create_multi_agent_graph()
        assert route_ingestion_decision({"ingestion_status": "SUCCESS"}) == "analyst"
        assert route_ingestion_decision({"ingestion_status": "FAILED"}) == "escalation"
        assert route_qa_decision({"qa_status": "APPROVED"}) == "human_approval"
        assert route_qa_decision({"qa_status": "REJECTED", "qa_target": "Marketing", "retry_count": 1}) == "marketing"
        report.record("Graph Orchestration", "Multi-Agent Graph State & Routing", True, "All state edges and conditional branches verified", (time.time() - t0) * 1000)
    except Exception as e:
        report.record("Graph Orchestration", "Multi-Agent Graph State & Routing", False, str(e), (time.time() - t0) * 1000)

    # -------------------------------------------------------------
    # Suite 6: Shop Help & Growth Impact Quantification
    # -------------------------------------------------------------
    t0 = time.time()
    try:
        lapsed_sample = ["C001", "C003"]
        slow_sample = ["Sweets", "Biscuits"]
        
        rec = ShopImpactEvaluator.calculate_recoverable_revenue(lapsed_sample, db_path=test_db)
        ds = ShopImpactEvaluator.calculate_dead_stock_value(slow_sample, db_path=test_db)
        growth_eval = ShopImpactEvaluator.calculate_shop_growth_index(rec, ds, base["total_baseline_revenue_inr"])

        assert growth_eval["shop_growth_index_score"] >= 50
        assert growth_eval["total_estimated_net_value_inr"] > 0
        assert growth_eval["promotion_roi_ratio"] >= 1.0

        shop_help_msg = (
            f"Shop Growth Score: {growth_eval['shop_growth_index_score']}/100 | "
            f"Estimated Net Value: ₹{growth_eval['total_estimated_net_value_inr']} | "
            f"Promotion ROI: {growth_eval['promotion_roi_ratio']}x"
        )
        report.record("Shop Help Evaluation", "Quantify Shop Growth & Revenue Protection", True, shop_help_msg, (time.time() - t0) * 1000)
    except Exception as e:
        report.record("Shop Help Evaluation", "Quantify Shop Growth & Revenue Protection", False, str(e), (time.time() - t0) * 1000)

    # Clean up test db
    if os.path.exists(test_db):
        try:
            os.remove(test_db)
        except Exception:
            pass

    total_time_ms = round((time.time() - start_time) * 1000, 2)

    return {
        "summary": {
            "total_tests": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "success_rate_pct": round((report.passed / max(report.total, 1)) * 100, 1),
            "total_duration_ms": total_time_ms,
            "status": "ALL_PASSED" if report.failed == 0 else "TESTS_FAILED"
        },
        "shop_impact": {
            "growth_index_score": growth_eval.get("shop_growth_index_score", 85),
            "estimated_net_value_inr": growth_eval.get("total_estimated_net_value_inr", 0.0),
            "potential_revenue_uplift_pct": growth_eval.get("potential_revenue_uplift_pct", 0.0),
            "promotion_roi_ratio": growth_eval.get("promotion_roi_ratio", 0.0),
            "status": growth_eval.get("status", "High Growth Potential"),
            "recoverable_breakdown": rec,
            "dead_stock_breakdown": ds
        },
        "test_results": report.results
    }


def print_cli_report(data: Dict[str, Any], filter_suite: str = None) -> int:
    """Prints a beautiful formatted summary to the console."""
    summary = data["summary"]
    impact = data["shop_impact"]
    results = data["test_results"]

    if filter_suite:
        results = [r for r in results if filter_suite.lower() in r["category"].lower()]

    print("\n" + "=" * 80)
    print(f"  {BOLD}MICRO-ENTREPRENEUR GROWTH WORKER - SYSTEM DIAGNOSTICS & TEST RUNNER 🇮🇳{RESET}")
    print("=" * 80)

    current_cat = None
    for r in results:
        if r["category"] != current_cat:
            current_cat = r["category"]
            print(f"\n{BOLD}{CYAN}▶ [{current_cat}]{RESET}")
        
        status_tag = f"{GREEN}[ PASS ]{RESET}" if r["success"] else f"{RED}[ FAIL ]{RESET}"
        dur = f"({r['duration_ms']}ms)"
        print(f"  {status_tag} {r['name']} {dur}")
        if r["message"]:
            msg_color = GREEN if r["success"] else RED
            print(f"         └─ {msg_color}{r['message']}{RESET}")

    print("\n" + "-" * 80)
    print(f"{BOLD}📊 SHOP HELP & BUSINESS IMPACT EVALUATION:{RESET}")
    print(f"  • Shop Growth Index Score:       {BOLD}{GREEN}{impact['growth_index_score']}/100{RESET} ({impact['status']})")
    print(f"  • Estimated Net Revenue Value:   {BOLD}₹{impact['estimated_net_value_inr']}{RESET}")
    print(f"  • Potential Revenue Uplift:      {BOLD}+{impact['potential_revenue_uplift_pct']}%{RESET}")
    print(f"  • Promotion ROI Multiplier:      {BOLD}{impact['promotion_roi_ratio']}x{RESET} return on discount spend")
    print(f"  • Lapsed Customers Addressable:  {impact['recoverable_breakdown']['lapsed_customers_count']} buyers (₹{impact['recoverable_breakdown']['total_lapsed_historical_value_inr']} historical spend)")
    print(f"  • Dead Stock Unlocked Liquidity: ₹{impact['dead_stock_breakdown']['estimated_unlocked_liquidity_inr']} across {impact['dead_stock_breakdown']['slow_moving_items_count']} items")

    print("-" * 80)
    rate_color = GREEN if summary["failed"] == 0 else RED
    print(f"{BOLD}SUMMARY: {summary['passed']}/{summary['total_tests']} Passed ({rate_color}{summary['success_rate_pct']}% Success{RESET}) in {summary['total_duration_ms']}ms{RESET}")
    print("=" * 80 + "\n")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Micro-Entrepreneur Growth Worker Test Runner")
    parser.add_argument("--suite", type=str, default=None, help="Filter by test suite name (e.g., csv, privacy, business, marketing, impact)")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    args = parser.parse_args()

    results_data = run_all_diagnostics()

    if args.json:
        import json
        print(json.dumps(results_data, indent=2))
        sys.exit(0 if results_data["summary"]["failed"] == 0 else 1)
    else:
        exit_code = print_cli_report(results_data, filter_suite=args.suite)
        sys.exit(exit_code)
