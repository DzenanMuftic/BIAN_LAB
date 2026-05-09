from datetime import datetime
import os
from decimal import Decimal, ROUND_HALF_UP

import oracledb
import requests
from flask import Flask, flash, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "mip-lab-secret")

BIAN_API_URL = os.getenv("BIAN_API_URL", "http://bian-api:8080")
DEBEZIUM_URL = os.getenv("DEBEZIUM_URL", "http://debezium:8083")
DEBEZIUM_CONNECTOR = os.getenv("DEBEZIUM_CONNECTOR", "oracle19c-banking-connector")
BOOKINGS_TOPIC = os.getenv("BOOKINGS_TOPIC", "banking.XEPDB1.BANKING.BOOKINGS")
DB_USER = os.getenv("DB_USER", "BANKING")
DB_PASSWORD = os.getenv("DB_PASSWORD", "banking_pwd")
DB_DSN = os.getenv("DB_DSN", "oracle19c:1521/XEPDB1")

ACCOUNT_ALIASES = {
    "RS35105008123123123129": "BA391860123456789012",
    "BA35105008123123123129": "BA391860123456789012",
    "RS20905000876543210011": "BA391860123456789014",
    "BA20905000876543210011": "BA391860123456789014",
    "RS10915000444477770022": "BA391610123456789015",
    "BA10915000444477770022": "BA391610123456789015",
}


def get_connection():
    return oracledb.connect(user=DB_USER, password=DB_PASSWORD, dsn=DB_DSN)


def list_accounts():
    query = """
        SELECT ACCOUNT_ID, CURRENCY
        FROM EOD_BALANCE
        ORDER BY ACCOUNT_ID
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
    return [{"account_id": row[0], "currency": row[1]} for row in rows]


def get_balance(account_id):
    resp = requests.get(
        f"{BIAN_API_URL}/bian/v14/current-account/{account_id}/get-balance",
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_history(account_id):
    query = """
        SELECT BOOKING_ID,
               BOOKING_DATE,
               AMOUNT,
               CURRENCY,
               NVL(OPERATOR, '-') AS OPERATOR,
               NVL(DESCRIPTION, '-') AS DESCRIPTION
        FROM BOOKINGS
        WHERE ACCOUNT_ID = :account_id
        ORDER BY BOOKING_DATE DESC, BOOKING_ID DESC
        FETCH FIRST 50 ROWS ONLY
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, account_id=account_id)
            rows = cur.fetchall()

    history = []
    for row in rows:
        history.append(
            {
                "booking_id": row[0],
                "booking_date": row[1],
                "amount": float(row[2]),
                "currency": row[3],
                "operator": row[4],
                "description": row[5],
                "direction": "OUT" if float(row[2]) < 0 else "IN",
            }
        )
    return history


def get_latest_booking(account_id):
    query = """
        SELECT BOOKING_ID,
               ACCOUNT_ID,
               BOOKING_DATE,
               AMOUNT,
               CURRENCY,
               NVL(OPERATOR, '-') AS OPERATOR,
               NVL(DESCRIPTION, '-') AS DESCRIPTION
        FROM BOOKINGS
        WHERE ACCOUNT_ID = :account_id
        ORDER BY BOOKING_DATE DESC, BOOKING_ID DESC
        FETCH FIRST 1 ROWS ONLY
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, account_id=account_id)
            row = cur.fetchone()

    if not row:
        return None

    description = row[6] if row[6] else "-"
    payment_reference = None
    if description.startswith("PAYMENT_ORDER:"):
        payment_reference = description.split(":", 1)[1].strip()

    return {
        "booking_id": row[0],
        "account_id": row[1],
        "booking_date": row[2],
        "amount": Decimal(str(row[3])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "currency": row[4],
        "operator": row[5],
        "description": description,
        "payment_reference": payment_reference,
    }


def get_latest_operator_log(payment_reference):
    if not payment_reference:
        return None

    query = """
        SELECT LOG_ID,
               ACTION_AT,
               OPERATOR,
               ACTION,
               DETAILS
        FROM OPERATOR_LOG
        WHERE DETAILS LIKE :details
        ORDER BY ACTION_AT DESC, LOG_ID DESC
        FETCH FIRST 1 ROWS ONLY
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, details=f"%{payment_reference}%")
            row = cur.fetchone()

    if not row:
        return None

    return {
        "log_id": row[0],
        "event_time": row[1],
        "operator": row[2],
        "action": row[3],
        "details": row[4],
    }


def get_today_bookings_sum(account_id):
    query = """
        SELECT NVL(SUM(AMOUNT), 0)
        FROM BOOKINGS
        WHERE ACCOUNT_ID = :account_id
          AND TRUNC(BOOKING_DATE) = TRUNC(SYSDATE)
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, account_id=account_id)
            row = cur.fetchone()
    return Decimal(str(row[0] if row and row[0] is not None else 0)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def get_eod_amount(account_id):
    query = """
        SELECT EOD_AMOUNT
        FROM EOD_BALANCE
        WHERE ACCOUNT_ID = :account_id
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, account_id=account_id)
            row = cur.fetchone()

    if not row:
        return Decimal("0.00")
    return Decimal(str(row[0])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_debezium_status():
    try:
        status_resp = requests.get(
            f"{DEBEZIUM_URL}/connectors/{DEBEZIUM_CONNECTOR}/status",
            timeout=5,
        )
        status_resp.raise_for_status()
        status = status_resp.json()

        topics_resp = requests.get(
            f"{DEBEZIUM_URL}/connectors/{DEBEZIUM_CONNECTOR}/topics",
            timeout=5,
        )
        topics_resp.raise_for_status()
        topics_json = topics_resp.json()

        connector_data = topics_json.get(DEBEZIUM_CONNECTOR, {})
        topic_names = connector_data.get("topics", []) if isinstance(connector_data, dict) else []

        connector_state = status.get("connector", {}).get("state", "UNKNOWN")
        task_states = [t.get("state", "UNKNOWN") for t in status.get("tasks", [])]
        tasks_ok = bool(task_states) and all(state == "RUNNING" for state in task_states)

        return {
            "ok": connector_state == "RUNNING" and tasks_ok,
            "connector_state": connector_state,
            "task_states": task_states,
            "topics": topic_names,
            "bookings_topic_seen": BOOKINGS_TOPIC in topic_names,
        }
    except Exception as ex:
        return {
            "ok": False,
            "connector_state": "UNREACHABLE",
            "task_states": [],
            "topics": [],
            "bookings_topic_seen": False,
            "error": str(ex),
        }


def build_last_transaction_flow(account_id, balance_snapshot):
    booking = get_latest_booking(account_id)
    if not booking:
        return None

    log_entry = get_latest_operator_log(booking.get("payment_reference"))
    debezium = get_debezium_status()

    eod = get_eod_amount(account_id)
    bookings_sum = get_today_bookings_sum(account_id)
    expected_current = (eod + bookings_sum).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    valkey_current = None
    valkey_ok = False
    if balance_snapshot and balance_snapshot.get("availableBalance") is not None:
        try:
            valkey_current = Decimal(str(balance_snapshot.get("availableBalance"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            valkey_ok = valkey_current == expected_current
        except Exception:
            valkey_current = None

    checkpoints = [
        {
            "name": "Flask -> BIAN API",
            "state": "ok" if booking.get("operator") == "BIAN_API" else "warn",
            "detail": f"Latest booking operator is {booking.get('operator')} for selected account.",
        },
        {
            "name": "Oracle (BOOKINGS)",
            "state": "ok",
            "detail": f"BOOKING_ID {booking.get('booking_id')} persisted with amount {booking.get('amount')} {booking.get('currency')}.",
        },
        {
            "name": "Debezium + Kafka topic",
            "state": "ok" if debezium.get("ok") and debezium.get("bookings_topic_seen") else "warn",
            "detail": (
                f"Connector={debezium.get('connector_state')}, tasks={','.join(debezium.get('task_states', [])) or 'n/a'}, "
                f"topicSeen={debezium.get('bookings_topic_seen')}"
            ),
        },
        {
            "name": "Valkey-backed current balance",
            "state": "ok" if valkey_ok else "warn",
            "detail": (
                f"BIAN API balance={valkey_current if valkey_current is not None else 'n/a'}, "
                f"expected from Oracle={expected_current}"
            ),
        },
    ]

    if booking.get("operator") == "BIAN_API":
        checkpoints.append(
            {
                "name": "Note about this transfer",
                "state": "info",
                "detail": "BIAN_API writes Valkey immediately after Oracle insert. Kafka consumer skips OPERATOR=BIAN_API events to avoid double-apply.",
            }
        )

    return {
        "booking": booking,
        "operator_log": log_entry,
        "debezium": debezium,
        "expected_current": expected_current,
        "valkey_current": valkey_current,
        "checkpoints": checkpoints,
    }


@app.get("/")
def index():
    accounts = list_accounts()
    if not accounts:
        return render_template(
            "index.html",
            accounts=[],
            selected_account=None,
            selected_recipient=None,
            balance=None,
            history=[],
            now=datetime.utcnow(),
        )

    account_ids = [a["account_id"] for a in accounts]
    valid_accounts = set(account_ids)

    requested_account = request.args.get("account")
    selected_tab = request.args.get("tab", "history")
    if selected_tab not in {"history", "flow"}:
        selected_tab = "history"
    selected_account = ACCOUNT_ALIASES.get(requested_account, requested_account) if requested_account else account_ids[0]
    account_redirect_needed = False
    if selected_account not in valid_accounts:
        selected_account = account_ids[0]
        account_redirect_needed = requested_account is not None
    elif requested_account is not None and selected_account != requested_account:
        account_redirect_needed = True

    recipients = [a["account_id"] for a in accounts if a["account_id"] != selected_account]
    requested_recipient = request.args.get("recipient")
    selected_recipient = ACCOUNT_ALIASES.get(requested_recipient, requested_recipient) if requested_recipient else None
    recipient_redirect_needed = False
    if recipients:
        if selected_recipient not in recipients:
            selected_recipient = recipients[0]
            recipient_redirect_needed = requested_recipient is not None
        elif requested_recipient is not None and selected_recipient != requested_recipient:
            recipient_redirect_needed = True
    else:
        selected_recipient = None

    if account_redirect_needed or recipient_redirect_needed:
        if selected_recipient:
            return redirect(url_for("index", account=selected_account, recipient=selected_recipient, tab=selected_tab))
        return redirect(url_for("index", account=selected_account, tab=selected_tab))

    balance = None
    history = []
    last_flow = None
    try:
        balance = get_balance(selected_account)
        history = get_history(selected_account)
        if selected_tab == "flow":
            last_flow = build_last_transaction_flow(selected_account, balance)
    except Exception as ex:
        flash(f"Failed loading account data: {ex}", "error")

    return render_template(
        "index.html",
        accounts=accounts,
        selected_account=selected_account,
        selected_recipient=selected_recipient,
        selected_tab=selected_tab,
        balance=balance,
        history=history,
        last_flow=last_flow,
        now=datetime.utcnow(),
    )


@app.post("/transfer")
def transfer():
    source_account = request.form.get("source_account", "").strip()
    recipient_account = request.form.get("recipient_account", "").strip()
    amount = request.form.get("amount", "").strip()
    description = request.form.get("description", "").strip()

    if not source_account or not recipient_account or not amount:
        flash("Source account, recipient account, and amount are required.", "error")
        return redirect(url_for("index", account=source_account, recipient=recipient_account))

    if source_account == recipient_account:
        flash("Recipient must be different from source account.", "error")
        return redirect(url_for("index", account=source_account, recipient=recipient_account))

    payload = {
        "amount": amount,
        "currency": "EUR",
        "counterpartyIban": recipient_account,
        "description": description or f"Transfer to {recipient_account}",
    }

    try:
        resp = requests.post(
            f"{BIAN_API_URL}/bian/v14/payment-order/{source_account}/initiate",
            json=payload,
            timeout=15,
        )
        data = resp.json()
        if resp.status_code >= 400:
            flash(f"Transfer failed: {data}", "error")
        else:
            flash(
                f"Transfer accepted. Reference: {data.get('paymentOrderReference', 'n/a')}",
                "success",
            )
    except Exception as ex:
        flash(f"Transfer request failed: {ex}", "error")

    return redirect(url_for("index", account=source_account, recipient=recipient_account))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
