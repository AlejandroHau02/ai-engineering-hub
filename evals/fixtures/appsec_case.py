"""SYNTHETIC INSECURE APPSEC FIXTURE. DO NOT EXECUTE, IMPORT OR DEPLOY.

Illustrative code only. Names, organizations and payloads are fictional.
The exercise is static review: identify security boundaries and propose fixes.
"""

from pathlib import Path


def cancel_appointment(db, current_user, organization_id, appointment_id):
    if current_user["organization_id"] != organization_id:
        raise PermissionError("wrong organization")
    # No operation-level permission check. IDs are client supplied.
    sql = (
        "UPDATE appointments SET status='cancelled' "
        f"WHERE organization_id='{organization_id}' AND id='{appointment_id}'"
    )
    db.execute(sql)


def store_uploaded_report(filename, data):
    root = Path("/tmp/syncode-demo-reports")
    (root / filename).write_bytes(data)


def receive_payment_webhook(db, headers, payload):
    # A signature validation step is missing before accepting the event.
    db.execute(
        "UPDATE invoices SET paid=1 WHERE id=?",
        (payload["invoice_id"],),
    )
