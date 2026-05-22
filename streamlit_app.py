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

# ---------------- Utilities ----------------

def split_csv_like(value: str):
    if not value:
        return []
    return [p.strip() for p in value.split(",") if p.strip()]

def normalize_carrier_scac(raw_carrier: str) -> str:
    """
    Converts known carrier names into label-friendly SCACs.
    Keeps existing SCAC-style values unchanged.
    """
    if not raw_carrier:
        return ""

    value = raw_carrier.strip()
    upper = value.upper()

    # Southeastern Freight Lines
    if "SOUTHEASTERN" in upper:
        return "SEFL"

    # Default behavior: use the first word from the Carrier line
    return value.split()[0]

def parse_qty_value(raw: str) -> int:
    """
    Qty can be:
      - "3"
      - "C16, A26"  (treat as count)
      - "1 PLT" or "2 PALLETS" (extract leading int)
    """
    if not raw:
        return 1

    raw = raw.strip()

    if re.fullmatch(r"\d+", raw):
        return int(raw)

    m = re.match(r"^\s*(\d+)\b", raw)
    if m:
        return int(m.group(1))

    parts = split_csv_like(raw)
    return len(parts) if parts else 1

def extract_fields(text: str):
    """
    Supports multiple document formats:

    OLD format labels:
      - Sales Order: SO-...
      - Job Name: qty or csv list
      - Load Number: csv list
      - Pro Number:
      - Carrier:

    NEW format labels:
      - Primary Reference: SO-...
      - QTY:
      - PLT LOC.:
      - PRO Number:
      - Carrier:

    UPDATED BOL format:
      - Sales Order: SO-...
      - Quantity: same as QTY
      - Location: same as PLT LOC.
      - Pro Number:
      - Carrier:

    Also supports combined line like:
      "QTY: 1 PLT LOC.: C1"
    """

    # Carrier / PRO
    carrier_match = re.search(r"(?im)^\s*Carrier:\s*(.+?)\s*$", text)
    pro_match = re.search(r"(?im)^\s*PRO\s*Number:\s*([A-Za-z0-9-]+)\s*$", text)

    # Sales Order OR Primary Reference
    so_match = (
        re.search(r"(?im)^\s*Sales\s*Order:\s*(SO-\d+[\w-]*)\s*$", text)
        or re.search(r"(?im)^\s*Primary\s*Reference:\s*(SO-\d+[\w-]*)\s*$", text)
    )

    so = so_match.group(1).strip() if so_match else ""
    scac = normalize_carrier_scac(carrier_match.group(1)) if carrier_match else ""
    pro = pro_match.group(1).strip() if pro_match else ""

    # Job Name OR QTY OR Quantity
    job_match = re.search(r"(?im)^\s*Job\s*Name:\s*(.+?)\s*$", text)
    qty_match = (
        re.search(r"(?im)^\s*QTY:\s*(.+?)\s*$", text)
        or re.search(r"(?im)^\s*Quantity:\s*(.+?)\s*$", text)
    )

    job_raw = job_match.group(1).strip() if job_match else ""
    qty_raw_line = qty_match.group(1).strip() if qty_match else ""

    # Load Number OR PLT LOC OR Location
    load_match = re.search(r"(?im)^\s*Load\s*Number:\s*(.+?)\s*$", text)
    plt_match = (
        re.search(r"(?im)^\s*PLT\s*LOC\.?:\s*(.+?)\s*$", text)
        or re.search(r"(?im)^\s*Location:\s*(.+?)\s*$", text)
    )

    load_raw = load_match.group(1).strip() if load_match else ""
    plt_raw_line = plt_match.group(1).strip() if plt_match else ""

    # Combined new format: "QTY: 1 PLT LOC.: C1"
    if qty_raw_line and re.search(r"(?i)\bPLT\s*LOC\b", qty_raw_line):
        parts = re.split(r"(?i)\bPLT\s*LOC\.?:\s*", qty_raw_line, maxsplit=1)
        qty_part = parts[0].strip()
        loc_part = parts[1].strip() if len(parts) > 1 else ""
        qty_raw = qty_part
        plt_raw = loc_part
    else:
        qty_raw = qty_raw_line or job_raw
        plt_raw = plt_raw_line or load_raw

    qty = parse_qty_value(qty_raw)
    load_numbers = split_csv_like(plt_raw)

    # Fallback: Pieces: 3
    if not qty_raw:
        pieces_match = re.search(r"(?im)^\s*Pieces\s*[:\-]?\s*(\d+)\s*$", text)
        if pieces_match:
            qty = int(pieces_match.group(1))

    return {
        "scac": scac,
        "so": so,
        "pro": pro,
        "load_numbers": load_numbers,
        "qty": qty,
    }

def generate_barcode_image_path(value):
    code128 = barcode.get("code128", value, writer=ImageWriter())
    raw_path = os.path.join(tempfile.gettempdir(), value)
    return code128.save(raw_path, options={"write_text": False})

def make_single_label_pdf(so, scac, pro, pallet_location, idx, total):
    barcode_path = generate_barcode_image_path(pro) if pro else None

    pdf = FPDF(unit="pt", format=(792, 612))
    pdf.add_page()
    pdf.set_auto_page_break(False)

    # Sales Order / Primary Reference
    pdf.set_font("Arial", "B", 80)
    pdf.set_y(60)
    pdf.cell(792, 80, so, ln=1, align="C")

    # Barcode / PRO
    if barcode_path:
        pdf.image(barcode_path, x=196, y=160, w=400, h=100)
        pdf.set_y(270)
        pdf.set_font("Arial", "B", 24)
        pdf.cell(792, 30, pro, ln=1, align="C")

    # Carrier / SCAC
    pdf.set_y(360)
    pdf.set_font("Arial", "B", 130)
    pdf.cell(792, 100, scac, ln=1, align="C")

    # Bottom-left pallet location
    if pallet_location:
        pdf.set_font("Arial", "B", 72)
        pdf.set_xy(30, 545)
        pdf.cell(300, 80, pallet_location, ln=0, align="L")

    # Bottom-center count
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
        pallet_location = load_numbers[i] if i < len(load_numbers) else ""
        labels.append(
            make_single_label_pdf(
                so,
                scac,
                pro,
                pallet_location,
                i + 1,
                qty
            )
        )

    return labels

# ---------------- Manual Entry Mode ----------------

if manual_mode:
    st.subheader("Manual Shipment Entry")

    header = st.columns([3, 3, 2, 2, 3])
    header[0].markdown("**Sales Order / Primary Ref**")
    header[1].markdown("**PRO (Barcode)**")
    header[2].markdown("**Carrier**")
    header[3].markdown("**Quantity**")
    header[4].markdown("**Pallet Locations (comma-separated)**")

    rows = []

    for i in range(20):
        cols = st.columns([3, 3, 2, 2, 3])

        so = cols[0].text_input("", key=f"so_{i}")
        pro = cols[1].text_input("", key=f"pro_{i}")
        scac = cols[2].text_input("", key=f"scac_{i}")
        qty = cols[3].number_input("", min_value=1, value=1, key=f"qty_{i}")
        loads = cols[4].text_input("", key=f"load_{i}", help="Example: C16, A26")

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
            for pdf_bytes in all_labels:
                merged.insert_pdf(fitz.open(stream=pdf_bytes, filetype="pdf"))

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
        "Upload BOL / Shipment Confirmation PDFs",
        type="pdf",
        accept_multiple_files=True
    )

    if uploaded_files:
        all_labels = []
        combined_bol = fitz.open()
        today = datetime.now(ZoneInfo("America/Chicago")).strftime("%m/%d/%Y")

        for f in uploaded_files:
            doc = fitz.open(stream=f.read(), filetype="pdf")

            # Put signature text on every page
            for page in doc:
                page.insert_text(
                    (88, 745),
                    f"{shipper_name or '__________________'}    {today}",
                    fontsize=11,
                    fontname="helv"
                )

            combined_bol.insert_pdf(doc)

            # Extract labels per page
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
            for pdf_bytes in all_labels:
                merged.insert_pdf(fitz.open(stream=pdf_bytes, filetype="pdf"))

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
