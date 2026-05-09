from datetime import datetime
import os

import oracledb
import requests
from flask import Flask, flash, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "mip-lab-secret")

BIAN_API_URL = os.getenv("BIAN_API_URL", "http://bian-api:8080")
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
            return redirect(url_for("index", account=selected_account, recipient=selected_recipient))
        return redirect(url_for("index", account=selected_account))

    balance = None
    history = []
    try:
        balance = get_balance(selected_account)
        history = get_history(selected_account)
    except Exception as ex:
        flash(f"Failed loading account data: {ex}", "error")

    return render_template(
        "index.html",
        accounts=accounts,
        selected_account=selected_account,
        selected_recipient=selected_recipient,
        balance=balance,
        history=history,
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
