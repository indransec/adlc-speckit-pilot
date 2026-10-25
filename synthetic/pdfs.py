"""Render synthetic claim documents as PDFs (reportlab). Text is laid out as 'Label: value'
lines so pypdf extraction is faithful, the way OCR'd forms typically arrive."""
from __future__ import annotations

import textwrap
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

WRAP = 105


def render_lines(title: str, lines: list[str]) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 60

    def newline(text: str, font="Helvetica", size=9.5):
        nonlocal y
        if y < 60:
            c.showPage(); y = h - 60
        c.setFont(font, size)
        c.drawString(50, y, text)
        y -= size + 4

    newline(title, "Helvetica-Bold", 12)
    y -= 6
    for line in lines:
        if not line:
            y -= 6
            continue
        for part in textwrap.wrap(line, WRAP) or [""]:
            newline(part)
    c.showPage()
    c.save()
    return buf.getvalue()


def line_row(i: int, l: dict) -> str:
    return (f"{i} | {l['description']} | {l['category']} | {l['amount_orig']:.2f} {l['currency']} | "
            f"claimed USD {l['claimed_usd']:.2f} | purchased {l['purchase_date']}")


CLAIM_TYPE_LABEL = {"delay": "Travel delay", "baggage_delay": "Baggage delay", "baggage_loss": "Baggage loss"}


def claim_form(c: dict) -> bytes:
    lines = [
        f"Claim reference: {c['claim_id']}",
        f"Claimant name: {c['claimant_name']}",
        f"Policy number: {c['policy_no']}",
        f"Date of birth: {c['dob']}",
        f"Passport number: {c['passport_no']}",
        f"Email: {c['claimant_email']}",
        f"Phone: {c['claimant_phone']}",
        f"Address: {c['claimant_address']}",
        "",
        f"Booking date: {c['booking_date']}",
        f"Flight number: {c['flight_no']}",
        f"Flight date: {c['flight_date']}",
        f"Route: {c['route']}",
        f"Claim type: {CLAIM_TYPE_LABEL[c['claim_type_declared']]}",
    ]
    if c["claim_type_declared"] == "delay":
        lines += [f"Delay claimed (hours): {c['delay_hours_claimed']}",
                  f"Compensation received from airline (USD): {c['compensation_received_usd']:.2f}"]
    if c["claim_type_declared"] == "baggage_delay":
        lines += [f"Bag delayed (hours): {c['bag_delay_hours_claimed']}"]
    lines += ["", f"What happened: {c['description']}", "", "Items / expenses claimed:"]
    lines += [line_row(i + 1, l) for i, l in enumerate(c["lines"])]
    lines += [f"Total claimed (USD): {c['claimed_usd']:.2f}", "", "Bank details for payment",
              f"Account holder: {c['bank_account_holder']}",
              f"Account number: {c['bank_account_no']}",
              f"Bank code: {c['bank_code']}", "",
              "Declaration: I confirm the information in this claim is true and complete.",
              f"Channel: {c['channel']}"]
    return render_lines("TRAVELSAFE INSURANCE - CLAIM FORM", lines)


def boarding_pass(f: dict) -> bytes:
    return render_lines("BOARDING PASS", [
        f"Passenger name: {f['passenger_name']}", f"Flight number: {f['flight_no']}",
        f"Flight date: {f['flight_date']}", f"Route: {f['route']}",
        f"Booking reference: {f['booking_ref']}", f"Seat: {f['seat']}"])


def airline_delay_letter(f: dict) -> bytes:
    return render_lines(f"{f['airline']} - FLIGHT DISRUPTION CONFIRMATION", [
        f"Passenger name: {f['passenger_name']}", f"Flight number: {f['flight_no']}",
        f"Flight date: {f['flight_date']}", f"Scheduled departure: {f['scheduled_dep']}",
        f"Actual departure: {f['actual_dep']}", f"Delay (hours): {f['delay_hours']:.1f}",
        f"Reason for delay: {f['cause_text']}",
        f"Compensation paid (USD): {f['compensation_usd']:.2f}"])


def pir(f: dict) -> bytes:
    lines = [f"PIR reference: {f['pir_ref']}", f"Passenger name: {f['passenger_name']}",
             f"Flight number: {f['flight_no']}", f"Flight date: {f['flight_date']}",
             f"Report type: {f['report_type']}"]
    if f["report_type"] == "Delayed baggage":
        lines.append(f"Bag delivered after (hours): {f['bag_delay_hours']}")
    else:
        lines.append("Bag status: Not traced after 21 days")
    return render_lines("PROPERTY IRREGULARITY REPORT", lines)


def receipt(f: dict) -> bytes:
    lines = [f"Merchant: {f['merchant']}", f"Date: {f['date']}", f"Item: {f['item']}",
             f"Amount: {f['amount']}", f"Payment: card ending {f['card_last4']}"]
    if f.get("scan_quality"):
        lines.insert(0, f"Scan quality: {f['scan_quality']}")
    if f.get("extra_text"):
        lines += ["", f["extra_text"]]
    return render_lines("RECEIPT", lines)


RENDERERS = {"claim_form": claim_form, "boarding_pass": boarding_pass,
             "airline_delay_letter": airline_delay_letter, "pir": pir, "receipt": receipt}
