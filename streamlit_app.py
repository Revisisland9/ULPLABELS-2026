import streamlit as st
import fitz  # PyMuPDF
import re
from fpdf import FPDF
from io import BytesIO
import barcode
from barcode.writer import ImageWriter
import tempfile
import os
from datetime import datetime
from zoneinfo import ZoneInfo

# ---------------- Page Config & Branding ----------------

st.set_page_config(
    page_title="🧙‍♂️ Magic Labels 2.0",
    layout="centered"
)

st.title("🧙‍♂️ Magic Labels 2.0")
st.caption("Rapid Output Shipping System — v2")
st.error("🧪 STAGING ENVIRONMENT — Not Production", icon="🧪")

# ---------------- UI Controls ----------------

manual_mode = st.toggle("Manual Entry", value=False)
shipper_name = st.text_input("Enter Shipper Name (for signature box on BOL)", value="")
debug = st.toggle("Debug", value=False)

# ---------------- Utilities ----------------

def split_csv_like(value: str):
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]

def parse_job_name_qty(job_raw: str):
    if not job_raw:
        return 1

    job_raw = job_raw.strip()

    if re.fullmatch(r"\d+", job_raw):
        return int(job_raw)

    parts = split_csv_like(job_raw)
    if parts:
        return len(parts)

    return 1

def extract_fields(text):
    carrier_match = re.search(r"Carrier:\s*(.+)", text)
    so_match = re.search(r"Sales Order:\s*(SO-\d+[\w-]*)", text)
    pro_match = re.search(r"Pro Number:\s*(\d+)", text)

    job_match = re.search(r"Job Name:\s*(.+)", text)
    job_raw = job_match.group(1).strip() if job_match else ""
    qty = parse_job_name_qty(job_raw)

    load_match = re.search(r"Load Number:\s*(.+)", text)
    load_numbers = split_csv_like(load_match.group(1)) if load_match else []

    if not job_raw:
        pieces_match = re.search(r"(?i)Pieces\s*[:\-]?\s*(\d+)", text)
        if pieces_match:
            qty = int(pieces_match.group(1))

    return {
        "scac": carrier_match.group(1).strip().split()[0] if carrier_match else "",
        "so": so_match.group(1) if so_match else "",
        "pro": pro_match.group(1) if pro_match else "",
        "load_numbers": load_numbers,
        "qty": qty,
    }

def generate_barcode_image_path(value):
    code128 = barcode.get("code128", value, writer=ImageWriter())
    raw_path = os.path.join(tempfile.gettempdir(), value)
    return code128.save(raw_path, options={"write_text": False})

def make_single_label_pdf(so, scac, pro, load_text, idx, total):
    barcode_path = generate_barcode_image_path(pro) if pro else None

    pdf = FPDF(unit="pt", format=(792, 612))
    pdf.add_page()
    pdf.set_auto_page_break(False)

    # Sales Order
    pdf.set_font("Arial", "B", 80)
    pdf.set_y(60)
    pdf.cell(792, 80, so, ln=1, align="C")

    # Barcode (PRO)
    if barcode_path:
        pdf.image(barcode_path, x=196, y=160, w=400, h=100)
        pdf.set_y(270)
        pdf.set_font("Arial", "B", 24)
        pdf.cell(792, 30, pro, ln=1, align="C")

    # Load Number text
    if load_text:
        pdf.set_y(305)
        pdf.set_font("Arial", "B", 34)
        pdf.cell(792, 40, f"LOAD: {load_text}", ln=1, align="C")

    # Carrier
    pdf.set_y(360)
    pdf.set_font("Arial", "B", 130)
    pdf.cell(792, 100, scac, ln=1, align="C")

    # Count
    pdf.set_y(500)
    pdf.set_font("Arial", "B", 80)
    pdf.cell(792, 80, f"{idx} of {total}", ln=1, align="C")

    buffer = BytesIO()
    buffer.write(pdf.output(dest="S").encode("latin1"))
    buffer.seek(0)

    if barcode_path and os.path.exists(barcode_path):
        os.remove(barcode_path)

    return buffer.read()

def make_labels(so, scac, pro, qty, load_numbers):
    labels = []
    for i in range(qty):
        load_text = load_numbers[i] if i < len(load_numbers) else ""
        labels.append(make_single_label_pdf(
            so, scac, pro, load_text, i + 1, qty
        ))
    return labels

# ---------------- Manual Entry Mode ----------------

if manual_mode:
    st.subheader("Manual Shipment Entry")

    header = st.columns([3, 3, 2, 2, 3])
    header[0].markdown("**Sales Order**")
    header[1].markdown("**PRO (Barcode)**")
    header[2].markdown("**Carrier**")
    header[3].markdown("**Quantity**")
    header[4].markdown("**Load Numbers (comma-separated)**")

    rows = []
    for i in range(20):
        cols = st.columns([3, 3, 2, 2, 3])
        so = cols[0].text_input("", key=f"so_{i}")
        pro = cols[1].text_input("", key=f"pro_{i}")
        scac = cols[2].text_input("", key=f"scac_{i}")
        qty = cols[3].number_input("", min_value=1, value=1, key=f"qty_{i}")
        loads = cols[4].text_input("", key=f"load_{i}")
        if so.strip():
            rows.append((so, pro, scac, qty, loads))

    if st.button("🧙‍♂️ Generate Labels"):
        all_labels = []
        for so, pro, scac, qty, loads in rows:
            all_labels.extend(
                make_labels(
                    so.strip(),
                    scac.strip(),
                    pro.strip(),
                    qty,
                    split_csv_like(loads)
                )
            )

        if all_labels:
            ts = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y%m%d-%H%M%S")
            merged = fitz.open()
            for pdf in all_labels:
                merged.insert_pdf(fitz.open(stream=pdf, filetype="pdf"))

            buf = BytesIO()
            merged.save(buf)
            buf.seek(0)

            st.download_button(
                "📥 Download Labels PDF",
                buf,
                file_name=f"magic_labels_{ts}.pdf",
                mime="application/pdf"
            )

# ---------------- PDF Mode ----------------

if not manual_mode:
    uploaded_files = st.file_uploader(
        "Upload BOL PDFs",
        type="pdf",
        accept_multiple_files=True
    )

    if uploaded_files:
        all_labels = []
        combined_bol = fitz.open()
        today = datetime.now(ZoneInfo("America/Chicago")).strftime("%m/%d/%Y")

        for f in uploaded_files:
            doc = fitz.open(stream=f.read(), filetype="pdf")

            for page in doc:
                page.insert_text(
                    (88, 745),
                    f"{shipper_name or '__________________'}    {today}",
                    fontsize=11,
                    fontname="helv"
                )

            combined_bol.insert_pdf(doc)

            for page in doc:
                fields = extract_fields(page.get_text())
                if debug:
                    st.write(fields)

                if fields["so"]:
                    all_labels.extend(
                        make_labels(
                            fields["so"],
                            fields["scac"],
                            fields["pro"],
                            fields["qty"],
                            fields["load_numbers"]
                        )
                    )

        if all_labels:
            ts = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y%m%d-%H%M%S")

            merged = fitz.open()
            for pdf in all_labels:
                merged.insert_pdf(fitz.open(stream=pdf, filetype="pdf"))

            label_buf = BytesIO()
            merged.save(label_buf)
            label_buf.seek(0)

            bol_buf = BytesIO()
            combined_bol.save(bol_buf)
            bol_buf.seek(0)

            st.download_button(
                "📥 Download Labels PDF",
                label_buf,
                file_name=f"magic_labels_{ts}.pdf",
                mime="application/pdf"
            )

            st.download_button(
                "📥 Download BOLs PDF",
                bol_buf,
                file_name=f"bols_{ts}.pdf",
                mime="application/pdf"
            )

