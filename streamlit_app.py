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

st.set_page_config(page_title="R.O.S.S.", layout="centered")
st.title("R.O.S.S. — Rapid Output Shipping System")

manual_mode = st.toggle("Manual Entry", value=False)
shipper_name = st.text_input("Enter Shipper Name (for signature box on BOL)", value="")
debug = st.toggle("Debug", value=False)

# ---------------- Utilities ----------------

def split_csv_like(value: str):
    """Split on commas, strip whitespace, drop empties."""
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]

def parse_job_name_qty(job_raw: str):
    """
    Job Name drives quantity.
    - If job_raw is a number like "2" => qty=2
    - Else if comma-separated => qty=len(entries)
    - Else => qty=1
    """
    if not job_raw:
        return 1, []

    job_raw = job_raw.strip()

    # numeric?
    if re.fullmatch(r"\d+", job_raw):
        return int(job_raw), []

    # list?
    parts = split_csv_like(job_raw)
    if parts:
        return len(parts), parts

    return 1, []

def extract_fields(text):
    """
    Updated rules:
      - qty is driven by Job Name:
      - per-label Load Number token is displayed as text
      - barcode remains Pro Number
    """
    carrier_match = re.search(r"Carrier:\s*(.+)", text)
    so_match = re.search(r"Sales Order:\s*(SO-\d+[\w-]*)", text)

    # PRO is the barcode value
    pro_match = re.search(r"Pro Number:\s*(\d+)", text)

    # Job Name drives qty (numeric or CSV list)
    job_match = re.search(r"Job Name:\s*(.+)", text)
    job_raw = job_match.group(1).strip() if job_match else ""
    qty, job_list = parse_job_name_qty(job_raw)

    # Load Number provides per-label tokens (c16, a26, ...)
    load_match = re.search(r"Load Number:\s*(.+)", text)
    load_numbers = split_csv_like(load_match.group(1)) if load_match else []

    # Backward-compatible fallback if Job Name missing
    if not job_raw:
        pieces_match = re.search(r"(?i)Pieces\s*[:\-]?\s*(\d+)", text)
        if pieces_match:
            qty = int(pieces_match.group(1))

    return {
        "scac": carrier_match.group(1).strip().split()[0] if carrier_match else "",
        "so": so_match.group(1) if so_match else "",
        "pro": pro_match.group(1) if pro_match else "",
        "job_raw": job_raw,
        "job_list": job_list,
        "load_numbers": load_numbers,
        "qty": qty,
    }

def generate_barcode_image_path(value_to_encode: str):
    code128 = barcode.get("code128", value_to_encode, writer=ImageWriter())
    raw_path = os.path.join(tempfile.gettempdir(), f"{value_to_encode}")
    full_path = code128.save(raw_path, options={"write_text": False})
    return full_path

def make_single_label_pdf(so: str, scac: str, pro_barcode_value: str, load_text: str, idx: int, total: int):
    """
    One label page:
    - Barcode encodes PRO number (pro_barcode_value)
    - Load Number token is displayed as text (load_text)
    - Bottom shows idx of total
    """
    use_barcode = bool(pro_barcode_value)
    barcode_path = generate_barcode_image_path(pro_barcode_value) if use_barcode else None

    pdf = FPDF(unit="pt", format=(792, 612))  # Landscape
    pdf.add_page()
    pdf.set_auto_page_break(False)

    # Sales Order at the top
    pdf.set_font("Arial", "B", 80)
    pdf.set_y(60)
    pdf.cell(792, 80, so, ln=1, align="C")

    # Barcode section (PRO)
    if use_barcode:
        pdf.image(barcode_path, x=196, y=160, w=400, h=100)
        pdf.set_y(270)
        pdf.set_font("Arial", "B", 24)
        pdf.cell(792, 30, pro_barcode_value, ln=1, align="C")

    # NEW: Load Number token displayed as text
    if load_text:
        pdf.set_y(305)
        pdf.set_font("Arial", "B", 34)
        pdf.cell(792, 40, f"LOAD: {load_text}", ln=1, align="C")

    # Carrier (SCAC)
    pdf.set_y(360)
    pdf.set_font("Arial", "B", 130)
    pdf.cell(792, 100, scac, ln=1, align="C")

    # Quantity marker
    pdf.set_y(500)
    pdf.set_font("Arial", "B", 80)
    pdf.cell(792, 80, f"{idx} of {total}", ln=1, align="C")

    buffer = BytesIO()
    buffer.write(pdf.output(dest="S").encode("latin1"))
    buffer.seek(0)

    if barcode_path and os.path.exists(barcode_path):
        try:
            os.remove(barcode_path)
        except Exception:
            pass

    return buffer.read()

def make_labels_from_job_and_load(so: str, scac: str, pro: str, qty: int, load_numbers: list):
    """
    Create qty labels:
      - Barcode = PRO for every label
      - Load token = load_numbers[i] if present
    """
    total = max(1, int(qty or 1))
    pdfs = []
    for i in range(total):
        load_text = load_numbers[i] if i < len(load_numbers) else ""
        pdfs.append(make_single_label_pdf(so, scac, pro, load_text, i + 1, total))
    return pdfs

# ---------------- Manual Entry Mode ----------------

if manual_mode:
    st.markdown("### Manual Shipment Entry")

    if st.button("🗑️ Clear Form"):
        keys_to_clear = [
            k for k in st.session_state.keys()
            if k.startswith(("so_", "pro_", "scac_", "qty_", "load_"))
        ]
        for key in keys_to_clear:
            del st.session_state[key]
        st.success("Form cleared! All manual entries removed.")

    header_cols = st.columns([3, 3, 2, 2, 3])
    header_cols[0].markdown("**Sales Order**")
    header_cols[1].markdown("**PRO (Barcode)**")
    header_cols[2].markdown("**Carrier**")
    header_cols[3].markdown("**Quantity**")
    header_cols[4].markdown("**Load Numbers (comma-separated)**")

    entries = []
    show_next_row = True

    for i in range(20):
        if not show_next_row:
            break
        cols = st.columns([3, 3, 2, 2, 3])
        so = cols[0].text_input("", key=f"so_{i}")
        pro = cols[1].text_input("", key=f"pro_{i}")   # barcode value
        scac = cols[2].text_input("", key=f"scac_{i}")
        qty = cols[3].number_input("", key=f"qty_{i}", min_value=1, value=1, step=1)
        loads = cols[4].text_input("", key=f"load_{i}", help="Example: c16, a26")
        entries.append((so, pro, scac, qty, loads))
        if not so.strip():
            show_next_row = False

    if st.button("🚀 Generate Labels"):
        all_labels = []
        total_labels = 0

        for (so, pro, scac, qty, loads) in entries:
            if so.strip():
                load_numbers = split_csv_like(loads)
                label_pdfs = make_labels_from_job_and_load(
                    so=so.strip(),
                    scac=scac.strip(),
                    pro=pro.strip(),
                    qty=qty,
                    load_numbers=load_numbers
                )
                total_labels += len(label_pdfs)
                all_labels.extend(label_pdfs)

        if all_labels:
            timestamp = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y%m%d-%H%M%S")
            merged_label_pdf = fitz.open()
            for label_data in all_labels:
                temp_pdf = fitz.open(stream=label_data, filetype="pdf")
                merged_label_pdf.insert_pdf(temp_pdf)

            label_buffer = BytesIO()
            merged_label_pdf.save(label_buffer)
            label_buffer.seek(0)

            st.success(f"✅ Generated {total_labels} labels from manual entries.")
            st.download_button(
                label="📥 Download Manual Labels PDF",
                data=label_buffer,
                file_name=f"manual_labels_{timestamp}.pdf",
                mime="application/pdf"
            )
        else:
            st.warning("⚠️ No valid manual entries found.")

# ---------------- PDF Mode ----------------

if not manual_mode:
    uploaded_files = st.file_uploader(
        "Upload BOL PDFs (single combined or multiple individual)",
        type="pdf",
        accept_multiple_files=True
    )

    if uploaded_files:
        all_labels = []
        total_labels = 0
        combined_bol = fitz.open()
        today_str = datetime.now(ZoneInfo("America/Chicago")).strftime("%m/%d/%Y")

        for uploaded_file in uploaded_files:
            file_buffer = BytesIO(uploaded_file.read())
            doc = fitz.open(stream=file_buffer, filetype="pdf")

            # Insert shipper name + date on every page (same as v1)
            for page_num in range(len(doc)):
                page = doc[page_num]
                text_to_insert = f"{shipper_name or '__________________'}    {today_str}"
                page.insert_text((88, 745), text_to_insert, fontsize=11, fontname="helv", fill=(0, 0, 0))

            combined_bol.insert_pdf(doc)

            # For each page, parse and generate labels
            for page in doc:
                text = page.get_text()
                fields = extract_fields(text)

                if debug:
                    st.write("Parsed Fields:", fields)

                so = fields["so"].strip()
                scac = fields["scac"].strip()
                pro = fields["pro"].strip()

                if not so:
                    continue

                label_pdfs = make_labels_from_job_and_load(
                    so=so,
                    scac=scac,
                    pro=pro,
                    qty=fields["qty"],
                    load_numbers=fields["load_numbers"]
                )

                total_labels += len(label_pdfs)
                all_labels.extend(label_pdfs)

        if all_labels:
            timestamp = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y%m%d-%H%M%S")

            merged_label_pdf = fitz.open()
            for label_data in all_labels:
                temp_pdf = fitz.open(stream=label_data, filetype="pdf")
                merged_label_pdf.insert_pdf(temp_pdf)

            label_buffer = BytesIO()
            merged_label_pdf.save(label_buffer)
            label_buffer.seek(0)

            bol_buffer = BytesIO()
            combined_bol.save(bol_buffer)
            bol_buffer.seek(0)

            st.success(f"✅ Generated {total_labels} labels from uploaded BOL(s).")
            st.download_button(
                label="📥 Download Combined Labels PDF",
                data=label_buffer,
                file_name=f"labels_{timestamp}.pdf",
                mime="application/pdf"
            )
            st.download_button(
                label="📥 Download Combined BOLs PDF",
                data=bol_buffer,
                file_name=f"bols_{timestamp}.pdf",
                mime="application/pdf"
            )
        else:
            st.warning("⚠️ No valid BOLs found in the uploaded file(s).")
