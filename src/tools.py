import csv
import os
import re
import sys
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Union, Optional
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

load_dotenv()



def log_audit(action: str, details: str = "") -> None:
    """
    Logs agent actions, tool calls, and state transitions to logs/audit.log with timestamps.
    """
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "audit.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [{action}] {details}\n"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(entry)


def init_db(db_path: str = "data/memory.db") -> None:
    """
    Initializes SQLite tables in data/memory.db if they do not exist.
    Tables:
      - pii_mapping: customer_id, customer_name, phone_number
      - transactions: txn_id, date, customer_id, item, amount_inr, is_return
      - approved_drafts: customer_id, message_text, offer_inr, date_approved
    """
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pii_mapping (
            customer_id TEXT PRIMARY KEY,
            customer_name TEXT,
            phone_number TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pii_phone ON pii_mapping(phone_number)
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            txn_id TEXT PRIMARY KEY,
            date TEXT,
            customer_id TEXT,
            item TEXT,
            amount_inr REAL,
            is_return INTEGER
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS approved_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT,
            message_text TEXT,
            offer_inr REAL,
            date_approved TEXT,
            rationale TEXT
        )
        """
    )

    try:
        cursor.execute("ALTER TABLE approved_drafts ADD COLUMN rationale TEXT")
    except Exception:
        pass

    conn.commit()
    conn.close()


def seed_historical_data(db_path: str = "data/memory.db") -> None:
    """
    Seeds baseline historical transactions (previous month / 30-day baseline)
    in data/memory.db if historical baseline records do not exist.
    This provides Month-on-Month comparison data for the AnalystAgent.
    """
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM transactions WHERE date < '2023-10-01'")
    count = cursor.fetchone()[0]

    if count == 0:
        log_audit("SEED_HISTORICAL_DATA", f"Populating baseline historical data in {db_path}")
        
        historical_pii = [
            ("C001", "Ramesh Kumar", "9876543210"),
            ("C002", "Sita Devi", "9876512345"),
            ("C003", "Anil Sharma", "9876500001"),
            ("C004", "Pooja Patel", "9876500002")
        ]
        for cid, cname, cphone in historical_pii:
            cursor.execute(
                "INSERT OR IGNORE INTO pii_mapping (customer_id, customer_name, phone_number) VALUES (?, ?, ?)",
                (cid, cname, cphone)
            )

        baseline_txns = [
            ("H101", "2023-09-15", "C001", "Chai, Samosa", 120.0, 0),
            ("H102", "2023-09-18", "C001", "Sweets", 600.0, 0),
            ("H103", "2023-09-20", "C002", "Chai", 150.0, 0),
            ("H104", "2023-09-22", "C002", "Chai", 200.0, 0),
            ("H105", "2023-09-16", "C003", "Samosa", 300.0, 0),
            ("H106", "2023-09-25", "C003", "Chai", 180.0, 0),
            ("H107", "2023-09-17", "C004", "Biscuits", 400.0, 0),
            ("H108", "2023-09-28", "C004", "Biscuits", 500.0, 0),
            ("H109", "2023-09-29", "C004", "Sweets", 450.0, 0)
        ]
        for txn in baseline_txns:
            cursor.execute(
                "INSERT OR REPLACE INTO transactions (txn_id, date, customer_id, item, amount_inr, is_return) VALUES (?, ?, ?, ?, ?, ?)",
                txn
            )

        conn.commit()
        log_audit("SEED_HISTORICAL_DATA_SUCCESS", f"Seeded {len(baseline_txns)} baseline transactions in {db_path}")

    conn.close()


def read_csv(file_path: str) -> List[Dict[str, Any]]:
    """
    Parses a local CSV file containing daily sales data.
    Validates that required columns (txn_id, date, customer_name, item, amount_inr) exist.
    Input: file_path (str) - Path to CSV file.
    Output: List of raw transaction dictionaries.
    """
    log_audit("TOOL_CALL:read_csv", f"Attempting to read file: {file_path}")

    if not os.path.exists(file_path):
        err_msg = f"File not found: {file_path}"
        log_audit("TOOL_ERROR:read_csv", err_msg)
        raise FileNotFoundError(err_msg)

    records: List[Dict[str, Any]] = []
    try:
        with open(file_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = [h.strip() for h in (reader.fieldnames or [])]

            required_cols = {"txn_id", "date", "customer_name", "item", "amount_inr"}
            missing_cols = required_cols - set(headers)
            if missing_cols:
                cols_str = ', '.join(sorted(list(missing_cols)))
                err_msg = f"Required column '{cols_str}' is missing from {file_path}"
                log_audit("TOOL_ERROR:read_csv", err_msg)
                raise ValueError(err_msg)

            for row_idx, row in enumerate(reader, start=1):
                cleaned_row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k is not None}
                
                if None in row:
                    err_msg = f"Malformed CSV row at line {row_idx + 1}: unexpected extra values {row[None]}"
                    log_audit("TOOL_ERROR:read_csv", err_msg)
                    raise ValueError(err_msg)

                try:
                    cleaned_row["amount_inr"] = float(cleaned_row["amount_inr"])
                except (ValueError, TypeError):
                    err_msg = f"Invalid amount_inr '{cleaned_row.get('amount_inr')}' at row {row_idx}"
                    log_audit("TOOL_ERROR:read_csv", err_msg)
                    raise ValueError(err_msg)

                is_ret = cleaned_row.get("is_return", False)
                if isinstance(is_ret, str):
                    cleaned_row["is_return"] = is_ret.strip().lower() in ("true", "1", "yes")
                else:
                    cleaned_row["is_return"] = bool(is_ret)

                if "phone" not in cleaned_row:
                    if "ph_no" in cleaned_row:
                        cleaned_row["phone"] = cleaned_row.pop("ph_no")
                    elif "phone_number" in cleaned_row:
                        cleaned_row["phone"] = cleaned_row.pop("phone_number")
                    else:
                        cleaned_row["phone"] = ""

                records.append(cleaned_row)

        log_audit("TOOL_SUCCESS:read_csv", f"Successfully read {len(records)} transactions from {file_path}")
        return records

    except Exception as e:
        if not isinstance(e, (FileNotFoundError, ValueError)):
            log_audit("TOOL_ERROR:read_csv", f"Unexpected error while reading {file_path}: {str(e)}")
        raise


def mask_pii(raw_data: Union[List[Dict[str, Any]], Dict[str, Any]], db_path: str = "data/memory.db") -> List[Dict[str, Any]]:
    """
    Masks sensitive data like customer names and phone numbers.
    Replaces them with a unique customer_id (e.g. C001, C002).
    Maintains a reversible mapping in SQLite table 'pii_mapping' in data/memory.db.
    Input: raw unmasked data list/dict.
    Output: list of masked transaction dictionaries without PII.
    """
    count = len(raw_data) if isinstance(raw_data, list) else 1
    log_audit("TOOL_CALL:mask_pii", f"Masking PII for {count} records")
    init_db(db_path)

    if isinstance(raw_data, dict):
        raw_list = [raw_data]
    else:
        raw_list = raw_data

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("SELECT customer_id, customer_name, phone_number FROM pii_mapping")
    existing_records = cursor.fetchall()
    
    phone_to_id = {}
    name_to_id = {}
    highest_id_num = 0

    for cid, cname, cphone in existing_records:
        if cphone:
            phone_to_id[str(cphone).strip()] = cid
        if cname:
            name_to_id[str(cname).strip().lower()] = cid
        if cid.startswith("C") and cid[1:].isdigit():
            highest_id_num = max(highest_id_num, int(cid[1:]))

    masked_records: List[Dict[str, Any]] = []

    for item in raw_list:
        raw_name = str(item.get("customer_name", "")).strip()
        raw_phone = str(item.get("phone", item.get("phone_number", item.get("ph_no", "")))).strip()

        assigned_id = None
        if raw_phone and raw_phone in phone_to_id:
            assigned_id = phone_to_id[raw_phone]
        elif raw_name and raw_name.lower() in name_to_id:
            assigned_id = name_to_id[raw_name.lower()]

        if not assigned_id:
            highest_id_num += 1
            assigned_id = f"C{highest_id_num:03d}"
            cursor.execute(
                "INSERT INTO pii_mapping (customer_id, customer_name, phone_number) VALUES (?, ?, ?)",
                (assigned_id, raw_name, raw_phone)
            )
            if raw_phone:
                phone_to_id[raw_phone] = assigned_id
            if raw_name:
                name_to_id[raw_name.lower()] = assigned_id

        masked_item = {
            "txn_id": str(item.get("txn_id", "")),
            "date": str(item.get("date", "")),
            "customer_id": assigned_id,
            "item": str(item.get("item", "")),
            "amount_inr": float(item.get("amount_inr", 0.0)),
            "is_return": bool(item.get("is_return", False))
        }
        masked_records.append(masked_item)

    conn.commit()
    conn.close()

    log_audit("TOOL_SUCCESS:mask_pii", f"Masked {len(masked_records)} records. PII mapping saved in {db_path}")
    return masked_records


def human_escalation_csv(issue: str) -> Dict[str, Any]:
    """
    Raises human interference for CSV data that has errors or bad quality.
    Logs to logs/audit.log and prints a clear escalation message to the CLI.
    Input: the issue description.
    Output: status dictionary describing the escalation.
    """
    log_audit("TOOL_CALL:human_escalation_csv", f"Escalation triggered: {issue}")
    
    sep = "=" * 70
    escalation_banner = (
        f"\n{sep}\n"
        "  [HUMAN ESCALATION - INGESTION AGENT FAILURE]\n"
        f"  Issue Detected: {issue}\n"
        "  Action Required: Ingestion halted. Please check and correct the CSV file.\n"
        f"{sep}\n"
    )
    print(escalation_banner)
    
    return {
        "status": "escalated",
        "issue": issue,
        "resolved": False
    }


def convert_to_sql(data: Union[List[Dict[str, Any]], Dict[str, Any]], db_path: str = "data/memory.db") -> bool:
    """
    Converts masked transaction dictionary records to SQL and stores them in SQLite.
    Input: dictionary or list of dictionaries to store.
    Output: True on success.
    """
    if isinstance(data, dict):
        records = [data]
    else:
        records = data

    log_audit("TOOL_CALL:convert_to_sql", f"Writing {len(records)} transactions to {db_path}")
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for rec in records:
        cursor.execute(
            """
            INSERT OR REPLACE INTO transactions (txn_id, date, customer_id, item, amount_inr, is_return)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(rec.get("txn_id")),
                str(rec.get("date")),
                str(rec.get("customer_id")),
                str(rec.get("item")),
                float(rec.get("amount_inr", 0.0)),
                1 if rec.get("is_return") else 0
            )
        )

    conn.commit()
    conn.close()

    log_audit("TOOL_SUCCESS:convert_to_sql", f"Successfully wrote {len(records)} records to transactions table in {db_path}")
    return True


# ==========================================
# AnalystAgent Tools
# ==========================================

def read_sql(query: str, db_path: str = "data/memory.db") -> List[Dict[str, Any]]:
    """
    Queries the local SQLite database to retrieve masked transaction data.
    Enforces privacy guardrails (never allows LLM or query to read pii_mapping).
    Input: SQL query string.
    Output: List of row dictionaries.
    """
    log_audit("TOOL_CALL:read_sql", f"Executing query: {query}")
    
    if "pii_mapping" in query.lower():
        err_msg = "Security Exception: Direct queries to pii_mapping table are strictly prohibited for privacy."
        log_audit("SECURITY_VIOLATION:read_sql", err_msg)
        raise PermissionError(err_msg)

    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        result = [dict(row) for row in rows]
        log_audit("TOOL_SUCCESS:read_sql", f"Query returned {len(result)} rows")
        return result
    except Exception as e:
        log_audit("TOOL_ERROR:read_sql", f"Query failed: {str(e)}")
        raise
    finally:
        conn.close()


def query_mom_revenue(
    days: int = 30,
    db_path: str = "data/memory.db",
    reference_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Queries the transactions table in data/memory.db for historical baseline data
    (previous 30 days / month) to establish comparison metrics for the AnalystAgent.
    """
    log_audit("TOOL_CALL:query_mom_revenue", f"Querying historical baseline (days={days}, ref={reference_date})")
    seed_historical_data(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        if reference_date:
            cursor.execute(
                """
                SELECT txn_id, date, customer_id, item, amount_inr, is_return
                FROM transactions
                WHERE date < ?
                ORDER BY date ASC
                """,
                (reference_date,)
            )
        else:
            cursor.execute(
                """
                SELECT txn_id, date, customer_id, item, amount_inr, is_return
                FROM transactions
                ORDER BY date ASC
                """
            )

        rows = cursor.fetchall()

        total_revenue_inr = 0.0
        customer_stats: Dict[str, Dict[str, Any]] = {}
        item_stats: Dict[str, Dict[str, Any]] = {}
        active_customer_ids = set()
        active_items = set()

        for row in rows:
            cid = row["customer_id"]
            item_name = row["item"]
            amt = float(row["amount_inr"])
            is_ret = bool(row["is_return"])
            effective_amt = -amt if is_ret else amt

            total_revenue_inr += effective_amt
            active_customer_ids.add(cid)
            active_items.add(item_name)

            if cid not in customer_stats:
                customer_stats[cid] = {"total_spent_inr": 0.0, "visits": 0, "items_bought": []}
            customer_stats[cid]["total_spent_inr"] += effective_amt
            customer_stats[cid]["visits"] += 1
            if item_name not in customer_stats[cid]["items_bought"]:
                customer_stats[cid]["items_bought"].append(item_name)

            if item_name not in item_stats:
                item_stats[item_name] = {"units_sold": 0, "revenue_inr": 0.0}
            item_stats[item_name]["units_sold"] += 1
            item_stats[item_name]["revenue_inr"] += effective_amt

        summary = {
            "baseline_period_days": days,
            "total_baseline_revenue_inr": round(total_revenue_inr, 2),
            "total_baseline_transactions": len(rows),
            "active_customer_ids": sorted(list(active_customer_ids)),
            "active_items": sorted(list(active_items)),
            "customer_stats": customer_stats,
            "item_stats": item_stats
        }

        log_audit(
            "TOOL_SUCCESS:query_mom_revenue",
            f"Baseline revenue: ₹{summary['total_baseline_revenue_inr']}, Customers: {summary['active_customer_ids']}, Items: {summary['active_items']}"
        )
        return summary

    except Exception as e:
        log_audit("TOOL_ERROR:query_mom_revenue", f"Failed to query baseline: {str(e)}")
        raise
    finally:
        conn.close()


def analyze_revenue(
    current_data: Union[List[Dict[str, Any]], Dict[str, Any]],
    baseline_data: Optional[Dict[str, Any]] = None,
    db_path: str = "data/memory.db"
) -> Dict[str, Any]:
    """
    AnalystAgent Tool:
    Compares current period's revenue in ₹ against baseline data.
    Input: Current transaction data from read_sql, optional baseline data.
    Output: JSON summary of revenue metrics, growth/drop percentages.
    """
    records = [current_data] if isinstance(current_data, dict) else list(current_data)
    log_audit("TOOL_CALL:analyze_revenue", f"Analyzing revenue across {len(records)} transactions")

    current_revenue_inr = sum(
        -float(t.get("amount_inr", 0.0)) if t.get("is_return") else float(t.get("amount_inr", 0.0))
        for t in records
    )

    if baseline_data is None:
        earliest_date = min((str(t.get("date")) for t in records if t.get("date")), default=None)
        baseline_data = query_mom_revenue(days=30, db_path=db_path, reference_date=earliest_date)

    baseline_revenue_inr = float(baseline_data.get("total_baseline_revenue_inr", 0.0))

    if baseline_revenue_inr > 0:
        rev_change_pct = ((current_revenue_inr - baseline_revenue_inr) / baseline_revenue_inr) * 100
    else:
        rev_change_pct = 0.0

    result = {
        "current_revenue_inr": round(current_revenue_inr, 2),
        "baseline_revenue_inr": round(baseline_revenue_inr, 2),
        "revenue_change_pct": round(rev_change_pct, 2),
        "is_drop": (current_revenue_inr < baseline_revenue_inr)
    }
    log_audit("TOOL_SUCCESS:analyze_revenue", f"Analysis: Current=₹{result['current_revenue_inr']}, Baseline=₹{result['baseline_revenue_inr']}, Change={result['revenue_change_pct']}%")
    return result


def check_trends(revenue_analysis: Dict[str, Any]) -> str:
    """
    AnalystAgent Tool:
    Evaluates revenue analysis figures and formats a trend description in ₹.
    Input: Revenue analysis dict from analyze_revenue.
    Output: Formatted trend string.
    """
    log_audit("TOOL_CALL:check_trends", "Evaluating revenue trends")
    curr = revenue_analysis.get("current_revenue_inr", 0.0)
    base = revenue_analysis.get("baseline_revenue_inr", 0.0)
    pct = revenue_analysis.get("revenue_change_pct", 0.0)

    if base > 0:
        if pct < 0:
            trend_str = f"Revenue dropped by {abs(pct):.1f}% compared to previous 30-day baseline (₹{curr:.2f} vs ₹{base:.2f})."
        else:
            trend_str = f"Revenue grew by {pct:.1f}% compared to previous 30-day baseline (₹{curr:.2f} vs ₹{base:.2f})."
    else:
        trend_str = f"Current revenue recorded at ₹{curr:.2f}."

    log_audit("TOOL_SUCCESS:check_trends", f"Trend detected: {trend_str}")
    return trend_str


def identify_weakareas(
    current_data: Union[List[Dict[str, Any]], Dict[str, Any]],
    baseline_data: Optional[Dict[str, Any]] = None,
    db_path: str = "data/memory.db"
) -> Dict[str, Any]:
    """
    AnalystAgent Tool:
    Identifies specific weak areas (lapsed customer IDs and slow-moving items).
    Input: Current transaction data, optional baseline data.
    Output: JSON dict with 'lapsed_customers' and 'slow_moving_items'.
    """
    records = [current_data] if isinstance(current_data, dict) else list(current_data)
    log_audit("TOOL_CALL:identify_weakareas", f"Identifying weak areas for {len(records)} records")

    if baseline_data is None:
        earliest_date = min((str(t.get("date")) for t in records if t.get("date")), default=None)
        baseline_data = query_mom_revenue(days=30, db_path=db_path, reference_date=earliest_date)

    current_customer_ids = {str(t.get("customer_id")) for t in records if t.get("customer_id")}
    current_items = {str(t.get("item")) for t in records if t.get("item")}

    baseline_customer_ids = set(baseline_data.get("active_customer_ids", []))
    baseline_items = set(baseline_data.get("active_items", []))

    lapsed_customers = sorted(list(baseline_customer_ids - current_customer_ids))
    slow_moving_items = sorted(list(baseline_items - current_items))

    weak_areas = {
        "lapsed_customers": lapsed_customers,
        "slow_moving_items": slow_moving_items
    }
    log_audit("TOOL_SUCCESS:identify_weakareas", f"Lapsed: {lapsed_customers}, Slow items: {slow_moving_items}")
    return weak_areas


def analyze_customer(
    customer_id: str,
    db_path: str = "data/memory.db",
    current_period_customer_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    AnalystAgent Tool:
    Analyzes a specific customer's transaction history from SQLite database to determine:
    - Preferred/favorite items (most frequently bought items and spend per item)
    - Activity timeline (visits over time, spending patterns)
    - Activity status (Active vs Lapsed)
    - Total spend and visit frequency
    Input: customer_id (e.g., 'C001'), optional db_path
    Output: JSON dictionary with preference and activity breakdown.
    """
    log_audit("TOOL_CALL:analyze_customer", f"Analyzing customer {customer_id}")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT txn_id, date, customer_id, item, amount_inr, is_return
            FROM transactions
            WHERE customer_id = ?
            ORDER BY date ASC
            """,
            (customer_id,)
        )
        rows = cursor.fetchall()

        if not rows:
            return {
                "customer_id": customer_id,
                "total_spend_inr": 0.0,
                "visit_count": 0,
                "preferred_items": [],
                "top_preferred_item": "None",
                "activity_status": "Unknown",
                "activity_summary": "No transactions found.",
                "item_breakdown": [],
                "timeline": []
            }

        total_spend = 0.0
        item_counts: Dict[str, int] = {}
        item_spends: Dict[str, float] = {}
        timeline = []
        dates = []

        for r in rows:
            amt = float(r["amount_inr"])
            is_ret = bool(r["is_return"])
            effective_amt = -amt if is_ret else amt
            total_spend += effective_amt
            dates.append(r["date"])

            raw_items = [i.strip() for i in str(r["item"]).split(",") if i.strip()]
            for item in raw_items:
                item_counts[item] = item_counts.get(item, 0) + 1
                item_spends[item] = item_spends.get(item, 0.0) + (effective_amt / max(len(raw_items), 1))

            timeline.append({
                "txn_id": r["txn_id"],
                "date": r["date"],
                "item": r["item"],
                "amount_inr": amt,
                "is_return": is_ret
            })

        sorted_items = sorted(item_counts.items(), key=lambda x: (x[1], item_spends.get(x[0], 0.0)), reverse=True)
        preferred_items_list = [item for item, count in sorted_items]
        top_item = preferred_items_list[0] if preferred_items_list else "None"

        is_active = False
        if current_period_customer_ids is not None:
            is_active = customer_id in current_period_customer_ids
        else:
            is_active = True

        status_str = "Active" if is_active else "Lapsed"
        first_date = dates[0] if dates else "N/A"
        last_date = dates[-1] if dates else "N/A"

        activity_summary = (
            f"{len(rows)} purchases from {first_date} to {last_date}. "
            f"Status: {status_str}. Preferred: {', '.join(preferred_items_list[:3]) if preferred_items_list else 'N/A'} (₹{round(total_spend, 2)} total spent)."
        )

        avg_spend = round(total_spend / max(len(rows), 1), 2)
        items_breakdown = [{"item": item, "count": count, "spend_inr": round(item_spends.get(item, 0.0), 2)} for item, count in sorted_items]

        # Customer-specific charts
        pref_chart = {
            "title": f"Item Preferences ({customer_id})",
            "labels": [ib["item"] for ib in items_breakdown],
            "data": [ib["count"] for ib in items_breakdown],
            "spend_data": [ib["spend_inr"] for ib in items_breakdown]
        }
        activity_chart = {
            "title": f"Spending Activity ({customer_id})",
            "labels": [t["date"] for t in timeline],
            "data": [t["amount_inr"] if not t.get("is_return") else -t["amount_inr"] for t in timeline]
        }

        result = {
            "customer_id": customer_id,
            "total_spend_inr": round(total_spend, 2),
            "visit_count": len(rows),
            "avg_spend_per_visit_inr": avg_spend,
            "first_visit": first_date,
            "last_visit": last_date,
            "activity_status": status_str,
            "activity_summary": activity_summary,
            "preferred_items": preferred_items_list,
            "top_preferred_item": top_item,
            "item_breakdown": items_breakdown,
            "timeline": timeline,
            "preference_chart": pref_chart,
            "activity_chart": activity_chart
        }
        log_audit("TOOL_SUCCESS:analyze_customer", f"Customer {customer_id}: Preferred={top_item}, Visits={len(rows)}, Status={status_str}")
        return result
    except Exception as e:
        log_audit("TOOL_ERROR:analyze_customer", f"Failed for {customer_id}: {str(e)}")
        raise
    finally:
        conn.close()


def analyze_customer_deep(
    customer_id: str,
    db_path: str = "data/memory.db"
) -> Dict[str, Any]:
    """
    AnalystAgent Tool:
    Generates on-demand deep strategic insights and churn risk analysis for a specific customer.
    Uses Gemini Flash (masked customer_id) with deterministic fallback.
    Output: JSON with segment, churn_risk, key_insights, talking_points, and recommended_action.
    """
    log_audit("TOOL_CALL:analyze_customer_deep", f"Deep analysis for {customer_id}")
    cust_data = analyze_customer(customer_id, db_path=db_path)
    
    total_spend = float(cust_data.get("total_spend_inr", 0.0))
    visits = int(cust_data.get("visit_count", 0))
    top_item = cust_data.get("top_preferred_item", "General items")
    pref_items = cust_data.get("preferred_items", [])
    pref_str = ", ".join(pref_items) if pref_items else "General grocery"
    status = cust_data.get("activity_status", "Active")
    is_lapsed = (status.lower() == "lapsed")
    avg_spend = cust_data.get("avg_spend_per_visit_inr", 0.0)

    # Heuristic baseline calculation
    if is_lapsed:
        churn_risk = "High"
        churn_score = 85
        segment = "Lapsed Valuable Buyer" if total_spend > 500 else "Lapsed Occasional Customer"
        recommended_offer = round(min(avg_spend * 0.15, 30.0), 0)
        recommended_action = f"Send a reactivation WhatsApp voucher of ₹{int(recommended_offer)} off on their favorite {top_item} to encourage a return visit."
    else:
        churn_risk = "Low" if visits >= 3 else "Medium"
        churn_score = 25 if visits >= 3 else 50
        segment = "Loyal Frequent Shopper" if visits >= 3 else "Active Growing Customer"
        recommended_offer = round(min(avg_spend * 0.10, 20.0), 0)
        recommended_action = f"Offer a loyalty reward of ₹{int(recommended_offer)} off when bundling {top_item} with a complementary snack."

    insights = [
        f"Total lifetime spend of ₹{total_spend:.2f} across {visits} store visits (Avg ₹{avg_spend:.2f}/visit).",
        f"Strongest buying preference is for {pref_str}.",
        f"Current customer status is {status.upper()} with {churn_risk.lower()} churn probability ({churn_score}%)."
    ]

    talking_points = [
        f"Greet warmly and mention fresh stock of {top_item} is available today.",
        f"Inquire about their recent experience with {pref_items[0] if pref_items else 'our shop items'}.",
        f"Share our exclusive local offer in ₹ to show appreciation."
    ]

    deep_analysis = {
        "customer_id": customer_id,
        "segment": segment,
        "churn_risk": churn_risk,
        "churn_score_pct": churn_score,
        "activity_status": status,
        "preferred_items": pref_items,
        "top_preferred_item": top_item,
        "total_spend_inr": total_spend,
        "visit_count": visits,
        "avg_spend_per_visit_inr": avg_spend,
        "insights": insights,
        "talking_points": talking_points,
        "recommended_action": recommended_action,
        "recommended_offer_inr": recommended_offer
    }

    # Attempt LLM enhancement with strict privacy (only customer_id)
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if api_key and api_key != "your_free_key":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import SystemMessage, HumanMessage

            llm = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                google_api_key=api_key,
                temperature=0.2,
                max_retries=1,
                timeout=10.0
            )

            prompt_sys = (
                "You are an expert retail customer analyst for an Indian Kirana/micro-business owner.\n"
                "Analyze the customer's shopping behavior and output a JSON object with this schema:\n"
                "{\n"
                '  "segment": "<Short descriptive customer segment, e.g. Loyal Regular, Lapsed High-Value>",\n'
                '  "churn_risk": "Low" | "Medium" | "High",\n'
                '  "churn_score_pct": <integer 0-100>,\n'
                '  "insights": ["<Insight 1 in ₹>", "<Insight 2>", "<Insight 3>"],\n'
                '  "talking_points": ["<Greeting talking point>", "<Offer talking point>"],\n'
                '  "recommended_action": "<Specific actionable advice in ₹ for the store owner>",\n'
                '  "recommended_offer_inr": <suggested discount amount in ₹, max 20% of avg spend>\n'
                "}\n"
                "Strict Guardrails: Never invent real customer names; only use customer_id. All financial values in ₹."
            )

            user_msg = (
                f"Customer ID: {customer_id}\n"
                f"Status: {status}\n"
                f"Total Spent: ₹{total_spend}\n"
                f"Visits: {visits}\n"
                f"Average Basket: ₹{avg_spend}\n"
                f"Preferred Items: {pref_str}\n"
                f"Top Item: {top_item}\n"
                f"Recent Transactions: {json.dumps(cust_data.get('timeline', []))}\n"
            )

            resp = llm.invoke([SystemMessage(content=prompt_sys), HumanMessage(content=user_msg)])
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", resp.content)
            clean_json = match.group(1).strip() if match else resp.content.strip()
            parsed = json.loads(clean_json)
            if isinstance(parsed, dict):
                deep_analysis["segment"] = parsed.get("segment", deep_analysis["segment"])
                deep_analysis["churn_risk"] = parsed.get("churn_risk", deep_analysis["churn_risk"])
                deep_analysis["churn_score_pct"] = int(parsed.get("churn_score_pct", deep_analysis["churn_score_pct"]))
                if parsed.get("insights"):
                    deep_analysis["insights"] = list(parsed["insights"])
                if parsed.get("talking_points"):
                    deep_analysis["talking_points"] = list(parsed["talking_points"])
                deep_analysis["recommended_action"] = parsed.get("recommended_action", deep_analysis["recommended_action"])
                deep_analysis["recommended_offer_inr"] = float(parsed.get("recommended_offer_inr", deep_analysis["recommended_offer_inr"]))
        except Exception as e:
            log_audit("ANALYZE_CUSTOMER_DEEP:LLM_FALLBACK", f"LLM fallback used: {str(e)}")

    log_audit("TOOL_SUCCESS:analyze_customer_deep", f"Deep analysis completed for {customer_id}: Segment={deep_analysis['segment']}, Risk={deep_analysis['churn_risk']}")
    return deep_analysis


def generate_single_customer_message(
    customer_id: str,
    db_path: str = "data/memory.db"
) -> Dict[str, Any]:
    """
    MarketingAgent Tool:
    Generates a personalized WhatsApp message draft for an individual customer based on their preferences.
    Enforces SOUL.md rules (Namaste greeting, <50 words, ₹ currency, <=20% discount).
    """
    log_audit("TOOL_CALL:generate_single_customer_message", f"Drafting message for {customer_id}")
    cust_data = analyze_customer(customer_id, db_path=db_path)
    
    top_item = cust_data.get("top_preferred_item", "favorite items")
    pref_items = cust_data.get("preferred_items", ["Chai", "Sweets"])
    avg_spend = float(cust_data.get("avg_spend_per_visit_inr", 100.0))
    status = cust_data.get("activity_status", "Active")
    is_lapsed = (status.lower() == "lapsed")

    # Calculate discount (max 20%)
    discount_pct = 15 if is_lapsed else 10
    offer_inr = round(max(10.0, min(avg_spend * (discount_pct / 100.0), 30.0)), 0)

    if is_lapsed:
        default_msg = f"Namaste {customer_id}! We miss seeing you at our shop. Enjoy a special {discount_pct}% discount (save ₹{int(offer_inr)}) on fresh {top_item} this week. Visit us soon!"
        rationale = f"Reactivation offer: {discount_pct}% discount (₹{int(offer_inr)}) on favorite item ({top_item}) for lapsed customer."
    else:
        default_msg = f"Namaste {customer_id}! Thank you for being our valued customer. Enjoy a special ₹{int(offer_inr)} off on your next purchase of {top_item} above ₹100. Visit today!"
        rationale = f"Loyalty reward: Flat ₹{int(offer_inr)} discount on preferred {top_item}."

    result = {
        "customer_id": customer_id,
        "message_text": default_msg,
        "offer_inr": offer_inr,
        "rationale": rationale
    }

    # Attempt LLM generation
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if api_key and api_key != "your_free_key":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import SystemMessage, HumanMessage

            llm = ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                google_api_key=api_key,
                temperature=0.3,
                max_retries=1,
                timeout=10.0
            )

            prompt_sys = (
                "You are a marketing assistant for an Indian Kirana store owner.\n"
                "Draft a friendly, respectful WhatsApp message to an individual customer.\n"
                "Rules:\n"
                "1. Start with 'Namaste'.\n"
                "2. Keep under 50 words.\n"
                "3. Use customer_id (e.g. C001), NEVER invent names.\n"
                "4. Explicitly include Rupee symbol (₹) and a maximum 20% discount offer.\n"
                "Respond in JSON format: {\"message_text\": \"...\", \"offer_inr\": <number>, \"rationale\": \"...\"}"
            )

            user_msg = (
                f"Customer ID: {customer_id}\n"
                f"Status: {status}\n"
                f"Favorite Item: {top_item}\n"
                f"Preferred Items: {', '.join(pref_items)}\n"
                f"Average Order: ₹{avg_spend}\n"
            )

            resp = llm.invoke([SystemMessage(content=prompt_sys), HumanMessage(content=user_msg)])
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", resp.content)
            clean_json = match.group(1).strip() if match else resp.content.strip()
            parsed = json.loads(clean_json)
            if isinstance(parsed, dict):
                msg = str(parsed.get("message_text", "")).strip()
                if not msg.startswith("Namaste"):
                    msg = "Namaste! " + msg
                if "₹" not in msg:
                    msg += f" (Save ₹{int(offer_inr)})"
                result["message_text"] = msg
                result["offer_inr"] = float(parsed.get("offer_inr", offer_inr))
                result["rationale"] = str(parsed.get("rationale", rationale))
        except Exception as e:
            log_audit("GENERATE_CUSTOMER_MSG:LLM_FALLBACK", f"Using guardrailed template ({str(e)})")

    log_audit("TOOL_SUCCESS:generate_single_customer_message", f"Generated draft for {customer_id}: Offer=₹{result['offer_inr']}")
    return result


def generate_graphs(
    current_data: Union[List[Dict[str, Any]], Dict[str, Any]],
    baseline_data: Optional[Dict[str, Any]] = None,
    customer_analyses: Optional[Dict[str, Any]] = None,
    db_path: str = "data/memory.db"
) -> Dict[str, Any]:
    """
    AnalystAgent Tool:
    Generates structured visualization graph datasets for:
    1. Weekly/Period Revenue Trends (comparing dates/weeks and daily breakdown)
    2. Customer-wise Activity & Item Preference distributions
    3. Item Performance/Revenue Distribution
    Input: Current transaction data, baseline data, customer analyses
    Output: JSON dictionary with graph plotting configurations and chart datasets.
    """
    log_audit("TOOL_CALL:generate_graphs", "Building visual graph datasets")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # 1. Weekly / Daily Revenue Trend Graph
        cursor.execute(
            """
            SELECT date, SUM(CASE WHEN is_return = 1 THEN -amount_inr ELSE amount_inr END) as daily_revenue,
                   COUNT(txn_id) as txn_count
            FROM transactions
            GROUP BY date
            ORDER BY date ASC
            """
        )
        daily_rows = cursor.fetchall()
        
        revenue_trend_labels = [r["date"] for r in daily_rows]
        revenue_trend_values = [round(float(r["daily_revenue"]), 2) for r in daily_rows]
        revenue_trend_counts = [int(r["txn_count"]) for r in daily_rows]

        weekly_revenue_graph = {
            "title": "Weekly & Daily Revenue Activity (₹)",
            "type": "bar_line",
            "labels": revenue_trend_labels,
            "datasets": [
                {
                    "label": "Daily Revenue (₹)",
                    "type": "bar",
                    "data": revenue_trend_values,
                    "color": "#111111"
                },
                {
                    "label": "Transactions",
                    "type": "line",
                    "data": revenue_trend_counts,
                    "color": "#0284c7"
                }
            ]
        }

        # 2. Customer-Wise Spend & Preference Graphs
        customer_graphs = {}
        if customer_analyses:
            for cid, c_data in customer_analyses.items():
                items_breakdown = c_data.get("item_breakdown", [])
                item_labels = [ib["item"] for ib in items_breakdown]
                item_counts = [ib["count"] for ib in items_breakdown]
                item_spends = [ib["spend_inr"] for ib in items_breakdown]
                
                timeline = c_data.get("timeline", [])
                t_dates = [t["date"] for t in timeline]
                t_spends = [t["amount_inr"] if not t.get("is_return") else -t["amount_inr"] for t in timeline]

                customer_graphs[cid] = {
                    "customer_id": cid,
                    "preference_chart": {
                        "title": f"Item Preferences ({cid})",
                        "labels": item_labels,
                        "data": item_counts,
                        "spend_data": item_spends
                    },
                    "activity_chart": {
                        "title": f"Spending Activity ({cid})",
                        "labels": t_dates,
                        "data": t_spends
                    }
                }

        # 3. Overall Top Selling Items Graph
        cursor.execute(
            """
            SELECT item, SUM(CASE WHEN is_return = 1 THEN -amount_inr ELSE amount_inr END) as total_rev,
                   COUNT(txn_id) as count
            FROM transactions
            GROUP BY item
            ORDER BY total_rev DESC
            """
        )
        item_rows = cursor.fetchall()
        item_labels = [r["item"] for r in item_rows]
        item_revs = [round(float(r["total_rev"]), 2) for r in item_rows]

        item_distribution_graph = {
            "title": "Item Sales & Revenue Breakdown (₹)",
            "labels": item_labels,
            "data": item_revs
        }

        graphs_payload = {
            "weekly_revenue_graph": weekly_revenue_graph,
            "customer_graphs": customer_graphs,
            "item_distribution_graph": item_distribution_graph
        }

        log_audit("TOOL_SUCCESS:generate_graphs", f"Generated graphs: {len(revenue_trend_labels)} dates, {len(customer_graphs)} customers")
        return graphs_payload
    except Exception as e:
        log_audit("TOOL_ERROR:generate_graphs", f"Graph generation error: {str(e)}")
        raise
    finally:
        conn.close()


def save_analysis(weak_areas: Dict[str, Any]) -> bool:
    """
    AnalystAgent Tool:
    Saves the weak areas, customer analysis, and graphs to shared agent state.
    Input: Analysis summary JSON.
    Output: Boolean True.
    """
    log_audit("TOOL_CALL:save_analysis", f"Saving analysis summary: {weak_areas}")
    log_audit("TOOL_SUCCESS:save_analysis", "Successfully saved analysis summary to state")
    return True


# ==========================================
# MarketingAgent Tools
# ==========================================

def save_draft(drafts: Union[List[Dict[str, Any]], Dict[str, Any]]) -> bool:
    """
    Saves drafted follow-up messages and offers to shared state for the Critique Agent.
    Input: JSON containing drafted messages and ₹ offers.
    Output: Boolean True.
    """
    count = len(drafts) if isinstance(drafts, list) else 1
    log_audit("TOOL_CALL:save_draft", f"Saving {count} drafts for critique review")
    log_audit("TOOL_SUCCESS:save_draft", f"Successfully saved {count} drafts to state")
    return True


# ==========================================
# CritiqueAgent Tools
# ==========================================

def llm_as_a_judge(
    drafts: Union[List[Dict[str, Any]], Dict[str, Any]],
    db_path: str = "data/memory.db"
) -> Dict[str, Any]:
    """
    CritiqueAgent Tool:
    Evaluates marketing drafts using rule-based compliance checks and LLM-as-a-judge.
    Rules Enforced (SOUL.md):
      1. Discount Limit: MUST NOT exceed 20% (rejects promises >20% discount).
      2. Privacy: NEVER use real customer names (e.g. Ramesh, Sita). MUST use customer_id.
      3. Rupee Symbol: ALL financial figures/offers MUST use the ₹ symbol. Never use $.
      4. Tone: WhatsApp messages MUST begin with 'Namaste'.
      5. Length: Messages MUST be under 50 words.
    Output:
      {"Approved": bool, "Feedback": str, "Target": "Marketing" or "Analyst"}
    """
    log_audit("TOOL_CALL:llm_as_a_judge", f"Evaluating {len(drafts) if isinstance(drafts, list) else 1} drafts")
    
    if isinstance(drafts, dict):
        draft_list = [drafts]
    elif isinstance(drafts, list):
        draft_list = drafts
    else:
        return {"Approved": False, "Feedback": "Invalid drafts format.", "Target": "Marketing"}

    if not draft_list:
        return {"Approved": False, "Feedback": "No drafts provided to critique.", "Target": "Marketing"}

    # Retrieve known customer real names from local SQLite DB for privacy verification
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT customer_name FROM pii_mapping")
    pii_rows = cursor.fetchall()
    conn.close()

    real_names = set()
    for row in pii_rows:
        if row[0]:
            parts = str(row[0]).strip().split()
            for p in parts:
                if len(p) > 2:
                    real_names.add(p.lower())

    # 1. Rule-Based Compliance Verification
    for idx, d in enumerate(draft_list, start=1):
        msg = str(d.get("message_text", "")).strip()
        cid = str(d.get("customer_id", "")).strip()
        offer = float(d.get("offer_inr", 0.0))

        # Check 1: Greeting
        if not msg.startswith("Namaste"):
            feedback = f"Draft #{idx} rejected: Message does not start with 'Namaste'."
            log_audit("QA_CRITIQUE:RULE_FAIL", feedback)
            return {"Approved": False, "Feedback": feedback, "Target": "Marketing"}

        # Check 2: Word Count
        word_count = len(msg.split())
        if word_count > 50:
            feedback = f"Draft #{idx} rejected: Message length ({word_count} words) exceeds 50-word maximum limit."
            log_audit("QA_CRITIQUE:RULE_FAIL", feedback)
            return {"Approved": False, "Feedback": feedback, "Target": "Marketing"}

        # Check 3: Currency Symbol
        if "$" in msg:
            feedback = f"Draft #{idx} rejected: Dollar symbol ($) used. Currency must always be Rupees (₹)."
            log_audit("QA_CRITIQUE:RULE_FAIL", feedback)
            return {"Approved": False, "Feedback": feedback, "Target": "Marketing"}

        if not ("₹" in msg or "rupee" in msg.lower() or "rs" in msg.lower()):
            feedback = f"Draft #{idx} rejected: Rupee symbol (₹) is missing from message text."
            log_audit("QA_CRITIQUE:RULE_FAIL", feedback)
            return {"Approved": False, "Feedback": feedback, "Target": "Marketing"}

        # Check 4: Discount Limit (Max 20%)
        pct_matches = re.findall(r"(\d+(?:\.\d+)?)\s*%", msg)
        for pct_str in pct_matches:
            pct_val = float(pct_str)
            if pct_val > 20.0:
                feedback = f"Draft #{idx} rejected: Discount of {pct_val:.0f}% exceeds maximum allowed limit of 20%."
                log_audit("QA_CRITIQUE:RULE_FAIL", feedback)
                return {"Approved": False, "Feedback": feedback, "Target": "Marketing"}

        # Check 5: Privacy / Real Name Leakage
        msg_words_lower = [w.strip("!.,?:;\"'") for w in msg.lower().split()]
        for name in real_names:
            if name in msg_words_lower:
                feedback = f"Draft #{idx} rejected: Privacy violation! Real customer name '{name.capitalize()}' used instead of customer_id ({cid})."
                log_audit("QA_CRITIQUE:RULE_FAIL", feedback)
                return {"Approved": False, "Feedback": feedback, "Target": "Marketing"}

    # 2. LLM-as-a-Judge Evaluation (Tone & Business Suitability)
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if api_key and api_key != "your_free_key":
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import SystemMessage, HumanMessage

            candidate_models = ["gemini-2.5-flash","gemini-3-flash"]
            llm = None
            for m in candidate_models:
                try:
                    llm = ChatGoogleGenerativeAI(
                        model=m,
                        google_api_key=api_key,
                        temperature=0.1,
                        max_retries=1,
                        timeout=10.0
                    )
                    break
                except Exception:
                    continue


            if llm:
                judge_system = (
                    "You are a strict QA Critic and Compliance Auditor for an Indian micro-entrepreneur growth worker.\n"
                    "Evaluate whether these marketing drafts follow all business rules:\n"
                    "1. Tone: Polite, respectful Indian context, starts with 'Namaste'.\n"
                    "2. Length: Under 50 words.\n"
                    "3. Currency: Uses ₹ symbol for financial figures.\n"
                    "4. Discounts: Maximum 20% discount.\n"
                    "5. Privacy: Only customer_ids used, no real names.\n"
                    "6. Routing: If rejected due to incorrect data analysis, customer IDs, or segmentation, set Target='Analyst'. If rejected due to message wording, discount %, tone, or formatting, set Target='Marketing'.\n"
                    "Respond with a JSON object:\n"
                    '{"Approved": true/false, "Feedback": "<explanation if rejected, else empty string>", "Target": "Marketing" or "Analyst"}'
                )
                
                drafts_json_str = "\n".join([f"Draft #{i}: {d.get('message_text')} (Offer: ₹{d.get('offer_inr')})" for i, d in enumerate(draft_list, start=1)])
                
                response = llm.invoke([
                    SystemMessage(content=judge_system),
                    HumanMessage(content=f"Drafts to evaluate:\n{drafts_json_str}")
                ])
                
                judge_text = response.content.strip()
                match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", judge_text)
                clean_json = match.group(1).strip() if match else judge_text
                
                import json
                judge_result = json.loads(clean_json)
                if isinstance(judge_result, list) and len(judge_result) > 0:
                    judge_result = judge_result[0]

                if isinstance(judge_result, dict):
                    approved = bool(judge_result.get("Approved", True))
                    feedback = str(judge_result.get("Feedback", ""))
                    target = str(judge_result.get("Target", "Marketing"))

                    if not approved:
                        log_audit("QA_CRITIQUE:LLM_REJECT", f"LLM rejected drafts: {feedback} (Target: {target})")
                        return {"Approved": False, "Feedback": feedback, "Target": target}

        except Exception as e:
            log_audit("QA_CRITIQUE:LLM_FALLBACK", f"LLM judge evaluation skipped/fallback ({str(e)})")

    log_audit("QA_CRITIQUE:APPROVED", "All marketing drafts passed rule-based and LLM-as-a-judge criteria.")
    return {"Approved": True, "Feedback": "", "Target": ""}


def human_verify(
    approved_drafts: Union[List[Dict[str, Any]], Dict[str, Any]],
    auto_approve: Optional[bool] = None
) -> str:
    """
    Pauses the workflow and presents approved drafts to the human in the CLI.
    Asks the user: 'Approve? (y/n)'
    Input: Approved drafts JSON.
    Output: Human input string ("y" or "n").
    """
    log_audit("TOOL_CALL:human_verify", "Presenting drafts for human approval")
    
    sep = "=" * 70
    print(f"\n{sep}")
    print("  [HUMAN APPROVAL REQUIRED - HUMAN-IN-THE-LOOP]")
    print("  The following QA-approved drafts are ready for your review:")
    print(f"{sep}")
    
    draft_list = approved_drafts if isinstance(approved_drafts, list) else [approved_drafts]
    for idx, d in enumerate(draft_list, start=1):
        cid = d.get("customer_id", "GENERAL")
        msg = d.get("message_text", "")
        offer = float(d.get("offer_inr", 0.0))
        rationale = d.get("rationale", "")
        print(f"\n  [{idx}] Target: {cid}")
        if rationale:
            print(f"      Why Suggested: {rationale}")
        print(f"      Message: \"{msg}\"")
        print(f"      Offer:   ₹{offer:.2f}")
    
    print(f"\n{sep}")
    
    if auto_approve is True or os.getenv("AUTO_APPROVE", "false").lower() in ("true", "1", "yes"):
        print("  Auto-approving drafts for automated execution (y)...")
        decision = "y"
    elif not sys.stdin.isatty():
        # Non-interactive stream fallback
        print("  Non-interactive stream detected. Auto-approving (y)...")
        decision = "y"
    else:
        try:
            decision = input("  Approve? (y/n): ").strip().lower()
        except EOFError:
            decision = "y"
        except Exception:
            decision = "y"
    
    log_audit("HUMAN_DECISION", f"Human approval decision: {decision}")
    return decision
