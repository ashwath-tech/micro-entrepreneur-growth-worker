import os
import sys
import csv
import json
import sqlite3
import threading
import urllib.parse
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.state import create_initial_state, SharedAgentState
from src.tools import (
    log_audit,
    init_db,
    seed_historical_data,
    read_csv,
    mask_pii,
    convert_to_sql,
    read_sql,
    analyze_customer,
    analyze_customer_deep,
    generate_single_customer_message
)
from src.agents import analyst_node, marketing_node, qa_critic_node

app = FastAPI(title="Micro-Entrepreneur Growth Worker", version="1.0.0")

# Setup Jinja2 templates directory
templates_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates"))
templates = Jinja2Templates(directory=templates_dir)


class SalesRecordRequest(BaseModel):
    date: str = Field(..., description="Date of sale in YYYY-MM-DD format")
    customer_name: str = Field(..., description="Customer full name")
    phone: str = Field(..., description="Customer 10-digit phone number")
    item: str = Field(..., description="Item or comma-separated items purchased")
    amount_inr: float = Field(..., description="Sale amount in Indian Rupees (₹)")
    is_return: Optional[bool] = Field(default=False, description="Whether this transaction was a return/refund")


class HumanResponseRequest(BaseModel):
    action: str = Field(..., description="Human action: 'approve', 'reject', or 'retry'")
    drafts: Optional[List[Dict[str, Any]]] = Field(default=None, description="Optional edited/added drafts submitted by human")


class SingleMessageSendRequest(BaseModel):
    message_text: str = Field(..., description="Outreach message content")
    offer_inr: Optional[float] = Field(default=0.0, description="Offer/discount amount in ₹")
    rationale: Optional[str] = Field(default="", description="Strategic rationale for sending this message")


class WorkflowManager:
    """
    Thread-safe manager for the multi-agent workflow and Human-in-the-Loop (HITL) execution.
    Maintains real-time logs, agent states, unmasked drafts, and synchronization events.
    """
    def __init__(self):
        self.lock = threading.RLock()
        self.status = "IDLE"  # IDLE, RUNNING, WAITING_APPROVAL, ESCALATED, COMPLETED, REJECTED, FAILED
        self.current_step = "System ready."
        self.logs: List[Dict[str, str]] = []
        self.analysis_summary: Dict[str, Any] = {}
        self.customer_analysis: Dict[str, Any] = {}
        self.graphs: Dict[str, Any] = {}
        self.generated_drafts: List[Dict[str, Any]] = []
        self.unmasked_drafts: List[Dict[str, Any]] = []
        self.edited_drafts: Optional[List[Dict[str, Any]]] = None
        self.error_message: str = ""
        self.human_decision_event = threading.Event()
        self.human_decision: Optional[str] = None
        self.worker_thread: Optional[threading.Thread] = None

    def add_log(self, message: str) -> None:
        with self.lock:
            ts = datetime.now().strftime("%H:%M:%S")
            self.logs.append({"timestamp": ts, "message": message})
            log_audit("WEB_CONSOLE", message)

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "status": self.status,
                "current_step": self.current_step,
                "logs": list(self.logs),
                "analysis_summary": self.analysis_summary,
                "customer_analysis": self.customer_analysis,
                "graphs": self.graphs,
                "drafts": self.generated_drafts,
                "unmasked_drafts": self.unmasked_drafts,
                "error_message": self.error_message
            }

    def trigger_workflow(self, csv_path: str = "data/sales.csv") -> bool:
        with self.lock:
            if self.status in ("RUNNING", "WAITING_APPROVAL"):
                return False
            self.status = "RUNNING"
            self.current_step = "Starting multi-agent workflow..."
            self.logs = []
            self.analysis_summary = {}
            self.customer_analysis = {}
            self.graphs = {}
            self.generated_drafts = []
            self.unmasked_drafts = []
            self.edited_drafts = None
            self.error_message = ""
            self.human_decision_event.clear()
            self.human_decision = None

        self.worker_thread = threading.Thread(
            target=self._run_workflow_thread,
            args=(csv_path,),
            daemon=True
        )
        self.worker_thread.start()
        return True

    def submit_human_response(self, action: str, drafts: Optional[List[Dict[str, Any]]] = None) -> bool:
        with self.lock:
            if self.status not in ("WAITING_APPROVAL", "ESCALATED"):
                return False
            self.human_decision = action.strip().lower()
            self.edited_drafts = drafts
            log_suffix = f" with {len(drafts)} custom/edited drafts" if drafts else ""
            self.add_log(f"[HumanDecision] Received decision: '{self.human_decision.upper()}'{log_suffix}")
            self.human_decision_event.set()
            return True

    def _unmask_customer_analysis(self, customer_analysis: Dict[str, Any], db_path: str = "data/memory.db") -> Dict[str, Any]:
        """
        Unmasks customer names and phone numbers for human-facing dashboard display from SQLite pii_mapping.
        """
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT customer_id, customer_name, phone_number FROM pii_mapping")
        pii_map = {row[0]: {"name": row[1], "phone": row[2]} for row in cursor.fetchall()}
        conn.close()

        unmasked_cust = {}
        for cid, data in customer_analysis.items():
            entry = dict(data)
            entry["customer_name"] = pii_map.get(cid, {}).get("name", f"Customer {cid}")
            entry["phone_number"] = pii_map.get(cid, {}).get("phone", "N/A")
            unmasked_cust[cid] = entry
        return unmasked_cust

    def _unmask_drafts(self, drafts: List[Dict[str, Any]], db_path: str = "data/memory.db") -> List[Dict[str, Any]]:
        """
        Unmasks PII (customer names, phone numbers) for presentation in Human Approval box.
        Reads locally from SQLite pii_mapping table (never shared with LLM).
        """
        init_db(db_path)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT customer_id, customer_name, phone_number FROM pii_mapping")
        pii_map = {row[0]: {"name": row[1], "phone": row[2]} for row in cursor.fetchall()}
        conn.close()

        unmasked = []
        for d in drafts:
            cid = str(d.get("customer_id", "")).strip()
            msg = str(d.get("message_text", "")).strip()
            offer = float(d.get("offer_inr", 0.0))
            rationale = str(d.get("rationale", "")).strip()

            if cid in pii_map:
                c_name = pii_map[cid]["name"] or "Customer"
                c_phone = pii_map[cid]["phone"] or "N/A"
                # Create customer-facing message replacing ID with customer's real name
                display_msg = msg.replace(cid, c_name)
                if not rationale:
                    rationale = f"Retention incentive: Reactivates lapsed customer {cid} ({c_name}) based on purchase history."
            else:
                c_name = "Store Broadcast" if cid == "STORE_OFFER" else "General"
                c_phone = "All Customers" if cid == "STORE_OFFER" else "N/A"
                display_msg = msg
                if not rationale:
                    rationale = "Dead stock liquidation: Broadcast offer to boost store foot-traffic and clear inventory."

            unmasked.append({
                "customer_id": cid,
                "customer_name": c_name,
                "phone_number": c_phone,
                "message_text": msg,
                "display_message": display_msg,
                "offer_inr": offer,
                "rationale": rationale
            })
        return unmasked

    def _run_workflow_thread(self, csv_path: str) -> None:
        """Background execution thread running the multi-agent graph with HITL synchronization."""
        try:
            self.add_log(f"[Workflow] Initializing workflow with CSV: {csv_path}")
            seed_historical_data()

            # -------------------------------------------------------------
            # Step 1: Ingestion Agent (with Retry on Human Escalation)
            # -------------------------------------------------------------
            ingestion_success = False
            while not ingestion_success:
                with self.lock:
                    self.current_step = "Ingesting sales data and masking PII..."
                self.add_log(f"[IngestionAgent] Reading and validating {csv_path}...")

                try:
                    raw_data = read_csv(csv_path)
                    masked_data = mask_pii(raw_data)
                    convert_to_sql(masked_data)
                    self.add_log(f"[IngestionAgent] Ingested and masked {len(masked_data)} records into SQLite.")
                    ingestion_success = True
                except Exception as e:
                    err = str(e)
                    self.add_log(f"[IngestionAgent] ERROR: {err}")
                    with self.lock:
                        self.status = "ESCALATED"
                        self.current_step = "Ingestion Escalated: Waiting for user to fix CSV"
                        self.error_message = f"CSV Ingestion Failed: {err}"
                        self.human_decision_event.clear()
                        self.human_decision = None

                    self.add_log("[HumanEscalation] Workflow paused. Waiting for human to fix CSV and click Retry...")
                    self.human_decision_event.wait()

                    if self.human_decision == "retry":
                        self.add_log("[IngestionAgent] Human signaled CSV fixed. Retrying ingestion...")
                        with self.lock:
                            self.status = "RUNNING"
                            self.error_message = ""
                        continue
                    else:
                        self.add_log(f"[IngestionAgent] Cancelled by user ({self.human_decision}).")
                        with self.lock:
                            self.status = "REJECTED"
                            self.current_step = "Workflow halted by user."
                        return

            # -------------------------------------------------------------
            # Step 2: Analyst Agent
            # -------------------------------------------------------------
            with self.lock:
                self.current_step = "AnalystAgent checking trends, customer preferences & graphs..."
            self.add_log("[AnalystAgent] Querying SQLite transactions, baseline 30-day metrics & customer profiles...")

            shared_state = create_initial_state(csv_path)
            analyst_res = analyst_node(shared_state)
            analysis = analyst_res.get("analysis_summary", {})
            shared_state["analysis_summary"] = analysis

            unmasked_cust = self._unmask_customer_analysis(analysis.get("customer_analysis", {}))
            graphs_payload = analysis.get("graphs", {})

            with self.lock:
                self.analysis_summary = analysis
                self.customer_analysis = unmasked_cust
                self.graphs = graphs_payload

            self.add_log(f"[AnalystAgent] Trend: {analysis.get('revenue_trend')}")
            self.add_log(f"[AnalystAgent] Lapsed Customers: {analysis.get('lapsed_customers')}, Slow Items: {analysis.get('slow_moving_items')}")
            self.add_log(f"[AnalystAgent] Analyzed {len(unmasked_cust)} customer profiles & generated visual graph datasets.")

            # -------------------------------------------------------------
            # Step 3 & 4: Marketing Agent <-> QA Critic Agent Loop
            # -------------------------------------------------------------
            qa_approved = False
            retry_count = 0
            max_retries = 2

            while not qa_approved and retry_count <= max_retries:
                with self.lock:
                    self.current_step = f"MarketingAgent drafting messages (Attempt {retry_count + 1})..."
                self.add_log(f"[MarketingAgent] Drafting WhatsApp follow-ups & local offers in ₹ (Attempt {retry_count + 1})...")

                mkt_res = marketing_node(shared_state)
                drafts = mkt_res.get("generated_drafts", [])
                shared_state["generated_drafts"] = drafts

                self.add_log(f"[MarketingAgent] Generated {len(drafts)} drafts. Submitting to QA Critic...")

                with self.lock:
                    self.current_step = f"QACriticAgent auditing drafts (Attempt {retry_count + 1})..."
                self.add_log(f"[QACriticAgent] Evaluating drafts for discount limits (<=20%), ₹ symbol, tone, and privacy...")

                qa_res = qa_critic_node(shared_state)
                qa_status = qa_res.get("qa_status", "PENDING")
                qa_feedback = qa_res.get("qa_feedback", "")
                shared_state["qa_status"] = qa_status
                shared_state["qa_feedback"] = qa_feedback

                if qa_status == "APPROVED":
                    qa_approved = True
                    self.add_log("[QACriticAgent] ALL DRAFTS APPROVED! Business rules and tone guidelines met.")
                else:
                    retry_count += 1
                    shared_state["retry_count"] = retry_count
                    self.add_log(f"[QACriticAgent] REJECTED: {qa_feedback}. Looping back to MarketingAgent...")

            if not qa_approved:
                with self.lock:
                    self.status = "ESCALATED"
                    self.current_step = "QA Retries Exceeded: Escalated to human"
                    self.error_message = f"QA Critic could not approve drafts after {max_retries} retries: {qa_feedback}"
                self.add_log(f"[Escalation] {self.error_message}")
                return

            # -------------------------------------------------------------
            # Step 5: Human Approval (HITL) with PII Unmasking
            # -------------------------------------------------------------
            unmasked = self._unmask_drafts(shared_state.get("generated_drafts", []))
            with self.lock:
                self.status = "WAITING_APPROVAL"
                self.current_step = "Awaiting Human Approval (Review WhatsApp drafts)"
                self.generated_drafts = shared_state.get("generated_drafts", [])
                self.unmasked_drafts = unmasked
                self.human_decision_event.clear()
                self.human_decision = None

            self.add_log("[HumanApproval] Drafts unmasked using local SQLite memory.db. Waiting for Human Review in web frontend...")
            self.human_decision_event.wait()

            decision = self.human_decision or "reject"
            if decision == "approve":
                # Check if human provided custom/edited drafts
                if self.edited_drafts and len(self.edited_drafts) > 0:
                    drafts_to_save = self.edited_drafts
                    self.add_log(f"[HumanApproval] Applying human edits/additions ({len(drafts_to_save)} total drafts).")
                else:
                    drafts_to_save = unmasked

                # Save approved drafts to SQLite
                conn = sqlite3.connect("data/memory.db")
                cursor = conn.cursor()
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                final_unmasked = []
                final_generated = []

                for d in drafts_to_save:
                    cid = str(d.get("customer_id", "STORE_OFFER")).strip() or "STORE_OFFER"
                    c_name = str(d.get("customer_name", "")).strip()
                    c_phone = str(d.get("phone_number", "")).strip()
                    msg = str(d.get("message_text", d.get("display_message", ""))).strip()
                    disp_msg = str(d.get("display_message", msg)).strip() or msg
                    offer_val = float(d.get("offer_inr", 0.0))
                    rat = str(d.get("rationale", "")).strip()

                    cursor.execute(
                        """
                        INSERT INTO approved_drafts (customer_id, message_text, offer_inr, date_approved, rationale)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (cid, msg, offer_val, now_str, rat)
                    )

                    final_unmasked.append({
                        "customer_id": cid,
                        "customer_name": c_name if c_name else ("Store Broadcast" if cid == "STORE_OFFER" else cid),
                        "phone_number": c_phone if c_phone else ("All Customers" if cid == "STORE_OFFER" else "N/A"),
                        "message_text": msg,
                        "display_message": disp_msg,
                        "offer_inr": offer_val,
                        "rationale": rat
                    })
                    final_generated.append({
                        "customer_id": cid,
                        "message_text": msg,
                        "offer_inr": offer_val,
                        "rationale": rat
                    })

                conn.commit()
                conn.close()

                with self.lock:
                    self.unmasked_drafts = final_unmasked
                    self.generated_drafts = final_generated
                    self.status = "COMPLETED"
                    self.current_step = "Workflow Completed Successfully (Approved)"

                self.add_log(f"[HumanApproval] APPROVED! {len(final_unmasked)} drafts recorded in SQLite approved_drafts table.")
            else:
                self.add_log("[HumanApproval] REJECTED by human. No drafts were saved to approved_drafts table.")
                with self.lock:
                    self.status = "REJECTED"
                    self.current_step = "Workflow Completed (Declined by Human)"

        except Exception as e:
            err_str = f"Workflow execution crashed: {str(e)}"
            self.add_log(f"[FATAL_ERROR] {err_str}")
            with self.lock:
                self.status = "FAILED"
                self.current_step = "Workflow Execution Failed"
                self.error_message = err_str


# Global workflow manager instance
workflow_mgr = WorkflowManager()


# ==========================================
# REST API Endpoints
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serves the single-page minimalist web frontend."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.post("/api/add_data")
async def add_data(record: SalesRecordRequest):
    """
    Appends a new sales transaction to data/sales.csv.
    Generates an incremented integer txn_id.
    """
    csv_path = "data/sales.csv"
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    # Determine next transaction ID
    next_id = 1
    file_exists = os.path.exists(csv_path)

    if file_exists:
        try:
            with open(csv_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                existing_ids = []
                for r in reader:
                    tid = r.get("txn_id", "")
                    if tid and str(tid).isdigit():
                        existing_ids.append(int(tid))
                if existing_ids:
                    next_id = max(existing_ids) + 1
        except Exception:
            next_id = 1

    fieldnames = ["txn_id", "date", "customer_name", "phone", "item", "amount_inr", "is_return"]

    try:
        if file_exists and os.path.getsize(csv_path) > 0:
            with open(csv_path, mode="rb") as f:
                f.seek(-1, os.SEEK_END)
                last_char = f.read(1)
                if last_char not in (b"\n", b"\r"):
                    with open(csv_path, mode="a", encoding="utf-8") as af:
                        af.write("\n")

        with open(csv_path, mode="a" if file_exists else "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()

            writer.writerow({
                "txn_id": next_id,
                "date": record.date.strip(),
                "customer_name": record.customer_name.strip(),
                "phone": record.phone.strip(),
                "item": record.item.strip(),
                "amount_inr": record.amount_inr,
                "is_return": bool(record.is_return)
            })

        log_audit("API:add_data", f"Appended txn_id={next_id} for {record.customer_name} (₹{record.amount_inr})")
        return {
            "status": "success",
            "message": f"Sale record for {record.customer_name} appended successfully.",
            "txn_id": next_id
        }
    except Exception as e:
        log_audit("API_ERROR:add_data", str(e))
        raise HTTPException(status_code=500, detail=f"Failed to append to CSV: {str(e)}")


@app.post("/api/run_agents")
async def run_agents():
    """
    Triggers the LangGraph multi-agent workflow in the background.
    """
    started = workflow_mgr.trigger_workflow("data/sales.csv")
    if not started:
        raise HTTPException(status_code=400, detail="A workflow is already currently running or awaiting human approval.")
    return {"status": "started", "message": "Multi-agent weekly analysis started."}


@app.get("/api/status")
async def get_status():
    """
    Returns the real-time state of the LangGraph workflow, live console logs, and unmasked drafts.
    """
    return workflow_mgr.get_status()


@app.post("/api/human_response")
async def human_response(resp: HumanResponseRequest):
    """
    Receives the human's decision ('approve', 'reject', or 'retry') and resumes graph execution.
    """
    action = resp.action.strip().lower()
    if action not in ("approve", "reject", "retry"):
        raise HTTPException(status_code=400, detail="Invalid action. Must be 'approve', 'reject', or 'retry'.")

    success = workflow_mgr.submit_human_response(action, drafts=resp.drafts)
    if not success:
        raise HTTPException(status_code=400, detail="Workflow is not currently awaiting human interaction.")

    return {"status": "received", "action": action, "drafts_count": len(resp.drafts) if resp.drafts else 0}


@app.get("/api/customers")
async def get_customers(
    search: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: Optional[str] = "spend_desc"
):
    """
    Returns all customer profiles and their preference/activity analyses with unmasked names.
    Supports optional search query, status filtering ('active', 'lapsed'), and sorting.
    """
    status_data = workflow_mgr.get_status()
    cust_analysis = status_data.get("customer_analysis", {})
    if not cust_analysis:
        try:
            all_txns = read_sql("SELECT DISTINCT customer_id FROM transactions")
            raw_analysis = {r["customer_id"]: analyze_customer(r["customer_id"]) for r in all_txns if r.get("customer_id")}
            cust_analysis = workflow_mgr._unmask_customer_analysis(raw_analysis)
        except Exception:
            cust_analysis = {}

    results = list(cust_analysis.values())

    if status and status.upper() != "ALL":
        results = [c for c in results if str(c.get("activity_status", "")).upper() == status.upper()]

    if search:
        s = search.lower().strip()
        results = [
            c for c in results
            if s in str(c.get("customer_name", "")).lower()
            or s in str(c.get("phone_number", "")).lower()
            or s in str(c.get("customer_id", "")).lower()
            or any(s in str(item).lower() for item in c.get("preferred_items", []))
        ]

    sb = (sort_by or "spend_desc").lower()
    if sb == "spend_desc":
        results.sort(key=lambda x: float(x.get("total_spend_inr", 0.0)), reverse=True)
    elif sb == "spend_asc":
        results.sort(key=lambda x: float(x.get("total_spend_inr", 0.0)))
    elif sb == "visits_desc":
        results.sort(key=lambda x: int(x.get("visit_count", 0)), reverse=True)
    elif sb == "name_asc":
        results.sort(key=lambda x: str(x.get("customer_name", "")).lower())
    elif sb == "id_asc":
        results.sort(key=lambda x: str(x.get("customer_id", "")))

    return {
        "count": len(results),
        "total": len(cust_analysis),
        "customers": results
    }


@app.get("/api/customer/{customer_id}")
async def get_customer_detail(customer_id: str):
    """
    Returns the preference and activity breakdown, visual graph data,
    and outreach message history for a specific customer.
    """
    try:
        raw_data = analyze_customer(customer_id)
        unmasked = workflow_mgr._unmask_customer_analysis({customer_id: raw_data})
        cust_profile = unmasked.get(customer_id, raw_data)

        # Retrieve past approved/sent messages for this customer
        init_db("data/memory.db")
        conn = sqlite3.connect("data/memory.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, customer_id, message_text, offer_inr, date_approved, rationale
            FROM approved_drafts
            WHERE customer_id = ?
            ORDER BY id DESC
            """,
            (customer_id,)
        )
        msg_rows = cursor.fetchall()
        conn.close()

        cust_profile["messages"] = [
            {
                "id": r["id"],
                "customer_id": r["customer_id"],
                "message_text": r["message_text"],
                "display_message": str(r["message_text"]).replace(customer_id, cust_profile.get("customer_name", customer_id)),
                "offer_inr": float(r["offer_inr"] or 0.0),
                "date_approved": r["date_approved"],
                "rationale": r["rationale"] or ""
            }
            for r in msg_rows
        ]

        return cust_profile
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found: {str(e)}")


@app.post("/api/customer/{customer_id}/analyze")
async def analyze_single_customer(customer_id: str):
    """
    Runs deep on-demand strategic customer analysis and churn risk assessment
    for a specific customer using AI/analytical heuristics.
    """
    try:
        raw_deep = analyze_customer_deep(customer_id)
        
        # Unmask customer name in the analysis response
        init_db("data/memory.db")
        conn = sqlite3.connect("data/memory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT customer_name, phone_number FROM pii_mapping WHERE customer_id = ?", (customer_id,))
        row = cursor.fetchone()
        conn.close()

        c_name = row[0] if row and row[0] else f"Customer {customer_id}"
        c_phone = row[1] if row and row[1] else "N/A"

        raw_deep["customer_name"] = c_name
        raw_deep["phone_number"] = c_phone

        # Replace customer_id with real name in talking points / insights
        unmasked_insights = [str(i).replace(customer_id, c_name) for i in raw_deep.get("insights", [])]
        unmasked_talking = [str(t).replace(customer_id, c_name) for t in raw_deep.get("talking_points", [])]
        unmasked_action = str(raw_deep.get("recommended_action", "")).replace(customer_id, c_name)

        raw_deep["insights"] = unmasked_insights
        raw_deep["talking_points"] = unmasked_talking
        raw_deep["recommended_action"] = unmasked_action

        log_audit("API:analyze_single_customer", f"Deep analysis completed for {customer_id} ({c_name})")
        return raw_deep
    except Exception as e:
        log_audit("API_ERROR:analyze_single_customer", f"Failed for {customer_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to analyze customer {customer_id}: {str(e)}")


@app.post("/api/customer/{customer_id}/generate_message")
async def generate_customer_message_draft(customer_id: str):
    """
    Generates a personalized WhatsApp message draft for this customer based on
    their item preferences, purchase frequency, and SOUL guardrails.
    """
    try:
        raw_draft = generate_single_customer_message(customer_id)
        
        init_db("data/memory.db")
        conn = sqlite3.connect("data/memory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT customer_name, phone_number FROM pii_mapping WHERE customer_id = ?", (customer_id,))
        row = cursor.fetchone()
        conn.close()

        c_name = row[0] if row and row[0] else f"Customer {customer_id}"
        c_phone = row[1] if row and row[1] else "N/A"

        msg = raw_draft.get("message_text", "")
        disp_msg = msg.replace(customer_id, c_name)

        clean_phone = "".join(ch for ch in str(c_phone) if ch.isdigit())
        if len(clean_phone) == 10:
            clean_phone = "91" + clean_phone

        whatsapp_url = f"https://wa.me/{clean_phone}?text={urllib.parse.quote(disp_msg)}" if clean_phone else ""

        return {
            "customer_id": customer_id,
            "customer_name": c_name,
            "phone_number": c_phone,
            "message_text": msg,
            "display_message": disp_msg,
            "offer_inr": float(raw_draft.get("offer_inr", 0.0)),
            "rationale": raw_draft.get("rationale", ""),
            "whatsapp_url": whatsapp_url
        }
    except Exception as e:
        log_audit("API_ERROR:generate_customer_message_draft", f"Failed for {customer_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to generate message for {customer_id}: {str(e)}")


@app.post("/api/customer/{customer_id}/send_message")
async def send_customer_outreach_message(customer_id: str, req: SingleMessageSendRequest):
    """
    Records an outreach message in SQLite approved_drafts, logs audit event,
    and returns a pre-formatted WhatsApp dispatch link.
    """
    try:
        init_db("data/memory.db")
        conn = sqlite3.connect("data/memory.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT customer_name, phone_number FROM pii_mapping WHERE customer_id = ?", (customer_id,))
        row = cursor.fetchone()

        c_name = row["customer_name"] if row and row["customer_name"] else f"Customer {customer_id}"
        c_phone = row["phone_number"] if row and row["phone_number"] else "N/A"

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg_text = req.message_text.strip()
        offer_val = float(req.offer_inr or 0.0)
        rat = req.rationale.strip() or f"Direct outreach to {c_name} ({customer_id})"

        cursor.execute(
            """
            INSERT INTO approved_drafts (customer_id, message_text, offer_inr, date_approved, rationale)
            VALUES (?, ?, ?, ?, ?)
            """,
            (customer_id, msg_text, offer_val, now_str, rat)
        )
        record_id = cursor.lastrowid
        conn.commit()
        conn.close()

        clean_phone = "".join(ch for ch in str(c_phone) if ch.isdigit())
        if len(clean_phone) == 10:
            clean_phone = "91" + clean_phone

        disp_msg = msg_text.replace(customer_id, c_name)
        whatsapp_url = f"https://wa.me/{clean_phone}?text={urllib.parse.quote(disp_msg)}" if clean_phone else ""

        log_audit("CUSTOMER_OUTREACH:SENT", f"Recorded outreach #{record_id} for {customer_id} ({c_name}) - ₹{offer_val}: {msg_text}")

        return {
            "status": "success",
            "message_id": record_id,
            "customer_id": customer_id,
            "customer_name": c_name,
            "phone_number": c_phone,
            "message_text": msg_text,
            "display_message": disp_msg,
            "offer_inr": offer_val,
            "date_recorded": now_str,
            "whatsapp_url": whatsapp_url
        }
    except Exception as e:
        log_audit("API_ERROR:send_customer_outreach_message", f"Failed for {customer_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to record message for {customer_id}: {str(e)}")


@app.get("/api/customer/{customer_id}/messages")
async def get_customer_messages(customer_id: str):
    """
    Returns all past outreach messages and recorded offers for a specific customer.
    """
    try:
        init_db("data/memory.db")
        conn = sqlite3.connect("data/memory.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT customer_name, phone_number FROM pii_mapping WHERE customer_id = ?", (customer_id,))
        p_row = cursor.fetchone()
        c_name = p_row["customer_name"] if p_row and p_row["customer_name"] else f"Customer {customer_id}"

        cursor.execute(
            """
            SELECT id, customer_id, message_text, offer_inr, date_approved, rationale
            FROM approved_drafts
            WHERE customer_id = ?
            ORDER BY id DESC
            """,
            (customer_id,)
        )
        rows = cursor.fetchall()
        conn.close()

        msgs = []
        for r in rows:
            raw_m = str(r["message_text"])
            msgs.append({
                "id": r["id"],
                "customer_id": r["customer_id"],
                "customer_name": c_name,
                "message_text": raw_m,
                "display_message": raw_m.replace(customer_id, c_name),
                "offer_inr": float(r["offer_inr"] or 0.0),
                "date_approved": r["date_approved"],
                "rationale": r["rationale"] or ""
            })

        return {"customer_id": customer_id, "customer_name": c_name, "count": len(msgs), "messages": msgs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch messages for {customer_id}: {str(e)}")



@app.get("/api/day_details/{date}")
async def get_day_details(date: str):
    """
    Returns all transaction records, revenue breakdown, and item statistics for a specific date.
    Unmasks customer names for human-facing dashboard view.
    """
    try:
        init_db("data/memory.db")
        conn = sqlite3.connect("data/memory.db")
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT customer_id, customer_name, phone_number FROM pii_mapping")
        pii_map = {row["customer_id"]: {"name": row["customer_name"], "phone": row["phone_number"]} for row in cursor.fetchall()}

        cursor.execute(
            """
            SELECT txn_id, date, customer_id, item, amount_inr, is_return
            FROM transactions
            WHERE date = ?
            ORDER BY txn_id ASC
            """,
            (date.strip(),)
        )
        rows = cursor.fetchall()
        conn.close()

        txns = []
        total_revenue = 0.0
        item_summary = {}

        for r in rows:
            amt = float(r["amount_inr"])
            is_ret = bool(r["is_return"])
            effective_amt = -amt if is_ret else amt
            total_revenue += effective_amt
            cid = r["customer_id"]

            raw_items = [i.strip() for i in str(r["item"]).split(",") if i.strip()]
            for item in raw_items:
                item_summary[item] = item_summary.get(item, 0) + 1

            txns.append({
                "txn_id": r["txn_id"],
                "date": r["date"],
                "customer_id": cid,
                "customer_name": pii_map.get(cid, {}).get("name", f"Customer {cid}"),
                "phone_number": pii_map.get(cid, {}).get("phone", "N/A"),
                "item": r["item"],
                "amount_inr": amt,
                "is_return": is_ret
            })

        return {
            "date": date,
            "total_transactions": len(txns),
            "total_revenue_inr": round(total_revenue, 2),
            "items_sold_count": sum(item_summary.values()),
            "items_summary": item_summary,
            "transactions": txns
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch day details: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.app:app", host="127.0.0.1", port=8000, reload=True)
