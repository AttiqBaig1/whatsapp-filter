"""WhatsApp Number Filter Dashboard (Streamlit + MobileValidate SDK).

Run:  streamlit run app.py
"""
import io
import os
import random
import re
import time

import pandas as pd
import streamlit as st

try:
    from mobilevalidate import MobileValidate
except ImportError:  # SDK optional: Demo mode still works
    MobileValidate = None

CHUNK = 100  # API limit per real-time lookup request

st.set_page_config(page_title="WhatsApp Filter", page_icon="✅", layout="wide")
st.title("✅ WhatsApp Number Filter")
st.caption("CSV ya Excel upload karein — pata chalega kaun se numbers WhatsApp par registered hain.")


# ---------------------------------------------------------------- helpers
def clean_number(raw, default_country):
    """Normalise a cell into something the API accepts (E.164 when possible)."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    s = str(raw).strip()
    if re.fullmatch(r"\d+\.0", s):  # Excel float artefact: 923001234567.0
        s = s[:-2]
    if "e+" in s.lower():  # scientific notation from Excel
        try:
            s = str(int(float(s)))
        except ValueError:
            pass
    s = re.sub(r"[^\d+]", "", s)
    if s.startswith("00"):
        s = "+" + s[2:]
    if s and not s.startswith("+"):
        # 0300... = national format -> needs default_country; 92300... = already has country code
        if not s.startswith("0") or not default_country:
            s = "+" + s.lstrip("0") if s.startswith("0") else "+" + s
    return s


def read_upload(file):
    name = file.name.lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(file, dtype=str)
    raw = file.getvalue()
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return pd.read_csv(io.StringIO(raw.decode(enc)), dtype=str, sep=None, engine="python")
        except Exception:
            continue
    raise ValueError("File parse nahi ho saki")


def guess_phone_column(df):
    for c in df.columns:
        if re.search(r"phone|mobile|number|whatsapp|cell|contact", str(c), re.I):
            return c
    return df.columns[0]


def demo_lookup(numbers):
    """Fake results so the UI can be tried without an API key."""
    rows = []
    for n in numbers:
        valid = len(re.sub(r"\D", "", n)) >= 9
        reg = random.choice([True, True, False, None]) if valid else None
        rows.append({"e164": n, "number_status": "valid" if valid else "invalid_number",
                     "checks": {"whatsapp.registered": {"registered": reg,
                                "status": "completed" if reg is not None else "unknown"}}})
    time.sleep(0.2)
    return {"results": rows}


def classify(row):
    if row.get("number_status") not in (None, "valid"):
        return "Invalid number"
    chk = (row.get("checks") or {}).get("whatsapp.registered") or {}
    if chk.get("status") == "pending":
        return "Pending"
    reg = chk.get("registered")
    return "On WhatsApp" if reg is True else "Not on WhatsApp" if reg is False else "Unknown"


def to_excel_bytes(df):
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    demo = st.toggle("Demo mode (fake results)", value=False,
                     help="API key ke baghair UI test karne ke liye. Results asli nahi hote.")
    sandbox = st.toggle("Sandbox key", value=False, disabled=demo,
                        help="Public test key — sirf test numbers par kaam karti hai.")
    api_key = st.text_input("API key", type="password", disabled=demo or sandbox,
                            value=os.getenv("MOBILEVALIDATE_API_KEY", ""))
    default_country = st.text_input("Default country (ISO, e.g. PK)", value="PK", max_chars=2).upper().strip()
    st.caption("Jin numbers mein + / country code nahi hoga unhe is country ka maana jayega.")

# ---------------------------------------------------------------- upload
file = st.file_uploader("CSV ya Excel file upload karein", type=["csv", "xlsx", "xls", "xlsm"])
if not file:
    st.info("Shuru karne ke liye file upload karein. Kam az kam ek column mein phone numbers hone chahiye.")
    st.stop()

try:
    df = read_upload(file)
except Exception as e:
    st.error(f"File parse nahi ho saki: {e}")
    st.stop()

col = st.selectbox("Phone number wala column", list(df.columns), index=list(df.columns).index(guess_phone_column(df)))
st.write(f"**{len(df):,} rows** mili. Preview:")
st.dataframe(df.head(10), width="stretch")

if not st.button("🔍 WhatsApp check karein", type="primary"):
    st.stop()

# ---------------------------------------------------------------- run check
work = df.copy()
work["_clean"] = work[col].map(lambda x: clean_number(x, default_country))
unique_numbers = [n for n in work["_clean"].unique() if n]
if not unique_numbers:
    st.error("Koi number nahi mila.")
    st.stop()

client = None
if not demo:
    if MobileValidate is None:
        st.error("SDK install nahi hai: `pip install mobilevalidate-sdk`")
        st.stop()
    if sandbox:
        client = MobileValidate(sandbox=True)
    elif api_key:
        client = MobileValidate(api_key=api_key)
    else:
        st.error("API key daalein, ya Sandbox / Demo mode on karein.")
        st.stop()

results = {}  # cleaned input -> row
bar = st.progress(0.0, text="Checking…")
chunks = [unique_numbers[i:i + CHUNK] for i in range(0, len(unique_numbers), CHUNK)]
try:
    for i, chunk in enumerate(chunks, 1):
        if demo:
            resp = demo_lookup(chunk)
        else:
            kwargs = {"checks": ["whatsapp"], "wait": 30}
            if default_country:
                kwargs["default_country"] = default_country
            resp = client.lookup(chunk, **kwargs)
        # results come back in the same order as the input numbers
        for sent, row in zip(chunk, resp["results"]):
            results[sent] = row
        bar.progress(i / len(chunks), text=f"Checked {min(i * CHUNK, len(unique_numbers)):,} / {len(unique_numbers):,}")
except Exception as e:
    st.error(f"API error: {e}")
    st.caption("Ab tak ke results neeche dikhaye ja rahe hain.")
bar.empty()

out = df.copy()
out["WhatsApp Status"] = work["_clean"].map(lambda n: classify(results[n]) if n in results else "Not checked")
out["Normalized Number"] = work["_clean"].map(lambda n: (results.get(n) or {}).get("e164") or n)

# ---------------------------------------------------------------- dashboard
counts = out["WhatsApp Status"].value_counts()
total = len(out)
cols = st.columns(5)
cols[0].metric("Total rows", f"{total:,}")
cols[1].metric("On WhatsApp", f"{counts.get('On WhatsApp', 0):,}")
cols[2].metric("Not on WhatsApp", f"{counts.get('Not on WhatsApp', 0):,}")
cols[3].metric("Unknown / Pending", f"{counts.get('Unknown', 0) + counts.get('Pending', 0):,}")
cols[4].metric("Invalid", f"{counts.get('Invalid number', 0):,}")

left, right = st.columns([2, 1])
with right:
    st.bar_chart(counts)
with left:
    options = list(counts.index)
    pick = st.multiselect("Filter", options, default=options)
    shown = out[out["WhatsApp Status"].isin(pick)]
    st.dataframe(shown, width="stretch", height=360)

st.subheader("⬇️ Download")
d1, d2, d3 = st.columns(3)
wa = out[out["WhatsApp Status"] == "On WhatsApp"]
nwa = out[out["WhatsApp Status"] == "Not on WhatsApp"]
d1.download_button("WhatsApp wale (CSV)", wa.to_csv(index=False).encode("utf-8-sig"), "on_whatsapp.csv", "text/csv")
d1.download_button("WhatsApp wale (Excel)", to_excel_bytes(wa), "on_whatsapp.xlsx")
d2.download_button("Non-WhatsApp (CSV)", nwa.to_csv(index=False).encode("utf-8-sig"), "not_on_whatsapp.csv", "text/csv")
d2.download_button("Non-WhatsApp (Excel)", to_excel_bytes(nwa), "not_on_whatsapp.xlsx")
d3.download_button("Sab results (CSV)", out.to_csv(index=False).encode("utf-8-sig"), "all_results.csv", "text/csv")
d3.download_button("Sab results (Excel)", to_excel_bytes(out), "all_results.xlsx")
