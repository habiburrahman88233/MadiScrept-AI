import io
import json
import csv
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from PIL import Image, ImageOps
import streamlit as st
from google import genai
from google.genai import types
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from rapidfuzz import process, fuzz

_EXPLANATION_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="mediscript-search")
_OCR_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mediscript-ocr")

@st.cache_data(show_spinner=False)
def load_price_rows():
    """Load current medicine catalog, strengths and listed prices from CSV."""
    path = Path(__file__).resolve().parent / "medicine.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@st.cache_data(show_spinner=False)
def load_medicine_indexes():
    """Build lookup maps once per app session instead of rescanning 21k rows."""
    rows = load_price_rows()
    by_brand = {}
    by_generic = {}
    for row in rows:
        brand_key = _key(row.get("brand name"))
        generic_key = _key(row.get("generic"))
        by_brand.setdefault(brand_key, []).append(row)
        by_generic.setdefault(generic_key, []).append(row)
    brand_names = list(dict.fromkeys(str(r.get("brand name", "")).strip() for r in rows if r.get("brand name")))
    return brand_names, by_brand, by_generic


def _key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _strength_key(value):
    match = re.search(r"\d+(?:\.\d+)?\s*(?:mg|mcg|μg|g|ml|iu|%)", str(value or ""), re.I)
    return _key(match.group(0)) if match else ""


def _format_dataset_price(row):
    """Return CSV-listed unit/package prices with their stated basis."""
    package = str(row.get("package container", "") or "")
    if not re.search(r"৳\s*[\d,]+(?:\.\d+)?", package):
        package = str(row.get("Package Size", "") or "")
    unit = re.search(r"unit\s*price\s*:\s*৳\s*([\d,]+(?:\.\d+)?)", package, re.I)
    package_prices = re.findall(r"([^,:()]+(?:\([^)]*\))?)\s*:\s*৳\s*([\d,]+(?:\.\d+)?)", package)
    parts = []
    if unit:
        parts.append(f"৳ {unit.group(1)} / unit")
    for basis, amount in package_prices:
        basis = re.sub(r"^[,\s(]+|[,\s)]+$", "", basis).strip()
        if basis.lower().startswith("unit price"):
            continue
        item = f"৳ {amount} / {basis}" if basis else f"৳ {amount}"
        if item not in parts:
            parts.append(item)
    return "; ".join(parts) if parts else "ডেটা পাওয়া যায়নি"


@st.cache_data(show_spinner=False, max_entries=2048)
def dataset_price(brand_name, generic_name, dosage_form, query=""):
    """Never estimate a price; show it only when the CSV identifies the product."""
    rows = load_price_rows()
    brand = _key(brand_name)
    generic = _key(generic_name)
    form = _key(dosage_form)
    _, by_brand, _ = load_medicine_indexes()
    candidates = by_brand.get(brand, [])
    if generic:
        same_generic = [r for r in candidates if _key(r.get("generic")) == generic]
        if not same_generic:
            return "ডেটা পাওয়া যায়নি"
        candidates = same_generic
    if form:
        same_form = [r for r in candidates if form in _key(r.get("dosage form")) or _key(r.get("dosage form")) in form]
        if not same_form:
            return "ডেটা পাওয়া যায়নি"
        candidates = same_form
    strength = _strength_key(query)
    if strength:
        same_strength = [r for r in candidates if strength in _key(r.get("strength"))]
        if not same_strength:
            return "এই strength-এর দাম নেই"
        candidates = same_strength
    if not candidates:
        return "ডেটা পাওয়া যায়নি"
    # Brand-only matches can span strengths and package types. Never present an
    # arbitrary row as the price for the prescription.
    prices = {_format_dataset_price(r) for r in candidates}
    strengths = {_key(r.get("strength")) for r in candidates}
    if len(prices) > 1 or len(strengths) > 1:
        return "দাম ভিন্ন—যাচাই করুন"
    return next(iter(prices))


@st.cache_data(show_spinner=False)
def load_test_rows():
    path = Path(__file__).resolve().parent / "testrate.csv"
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@st.cache_data(show_spinner=False, max_entries=512)
def dataset_test_price(test_name):
    """Look up rates and the test purpose using the supplied CSV's headers."""
    wanted = _key(test_name)
    for row in load_test_rows():
        known = _key(row.get("Test name"))
        if wanted and (wanted == known or wanted in known or known in wanted):
            pop = str(row.get("Popular Diagnostic rate (BDT, photo)", "")).strip()
            govt = str(row.get("Government rate (BDT)", "")).strip()
            reason = str(row.get("Why the test is done (general information)", "")).strip()
            return pop or "N/A", govt or "রেট পাওয়া যায়নি", reason
    return None

@st.cache_data(show_spinner=False, max_entries=512)
def fuzzy_find_medicine(input_query, dosage_form="", cutoff=40):
    if not input_query:
        return None, []
    brand_names, by_brand, by_generic = load_medicine_indexes()
    clean_query = re.sub(r"\b(?:\d+(?:\.\d+)?\s*(?:mg|mcg|μg|g|ml|iu|%))\b", " ", input_query, flags=re.I)
    clean_query = re.sub(r"\b(?:syrup|syr|tablet|tab|capsule|cap|drop|suspension|susp)\b", " ", clean_query, flags=re.I)
    clean_query = re.sub(r"\s+", " ", clean_query).strip()
    normalized_query = _key(clean_query)
    prefix_matches = [b for b in brand_names if _key(b).startswith(normalized_query)] if normalized_query else []
    if prefix_matches:
        matched_brand = min(prefix_matches, key=lambda b: (len(_key(b)), b.lower()))
    else:
        match = process.extractOne(clean_query, brand_names, scorer=fuzz.token_set_ratio)
        matched_brand = match[0] if match and match[1] >= cutoff else None
    if not matched_brand:
        return None, []

    candidates = by_brand.get(_key(matched_brand), [])
    requested_form = _key(dosage_form)
    if requested_form:
        form_matches = [r for r in candidates if requested_form in _key(r.get("dosage form")) or _key(r.get("dosage form")) in requested_form]
        if form_matches:
            candidates = form_matches
    requested_strength = _strength_key(input_query)
    if requested_strength:
        strength_matches = [r for r in candidates if requested_strength in _key(r.get("strength"))]
        if strength_matches:
            candidates = strength_matches
    if not candidates:
        return None, []

    row = candidates[0]
    med_info = (
        row.get("brand name", ""), row.get("generic", ""), row.get("manufacturer", ""),
        row.get("dosage form", ""), row.get("package container", ""), "", row.get("strength", "")
    )
    alternatives = []
    generic_key = _key(row.get("generic"))
    form_key = _key(row.get("dosage form"))
    strength_key = _key(row.get("strength"))
    seen = set()
    for alt in by_generic.get(generic_key, []):
        alt_brand = str(alt.get("brand name", "")).strip()
        if not alt_brand or _key(alt_brand) == _key(matched_brand) or _key(alt.get("generic")) != generic_key:
            continue
        if form_key and _key(alt.get("dosage form")) != form_key:
            continue
        if requested_strength and strength_key and requested_strength not in _key(alt.get("strength")):
            continue
        if _key(alt_brand) in seen:
            continue
        seen.add(_key(alt_brand))
        alt_price = dataset_price(alt_brand, alt.get("generic"), alt.get("dosage form"), input_query)
        alternatives.append((alt_brand, alt.get("manufacturer", ""), alt_price, alt.get("dosage form", ""), alt.get("strength", "")))
    return med_info, alternatives

def clean_pdf_text(text):
    if not text:
        return ""
    return str(text).replace("৳", "BDT ").replace("’", "'").replace("‘", "'").strip()

def generate_pdf(vitals, meds_summary, tests_summary):
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.setTitle("MediScript AI - Clinical Summary")
    
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, 750, "MediScript AI - Prescription Audit Report")
    p.setFont("Helvetica", 9)
    p.drawString(50, 736, "Dataset Coverage: 21,000+ Medicines | Kushtia Regional Healthcare")
    p.line(50, 725, 550, 725)
    
    y = 698
    p.setFont("Helvetica-Bold", 11)
    p.drawString(50, y, "1. Patient Details & Assessment:")
    p.setFont("Helvetica", 9)
    y -= 18
    p.drawString(65, y, clean_pdf_text(vitals))
    
    y -= 26
    p.setFont("Helvetica-Bold", 11)
    p.drawString(50, y, "2. Prescribed Medicines & Cost Intelligence:")
    y -= 18
    p.setFont("Helvetica", 9)
    for idx, m in enumerate(meds_summary, start=1):
        m_name = clean_pdf_text(m.get('name', ''))
        m_form = clean_pdf_text(m.get('form', ''))
        m_dose = clean_pdf_text(m.get('dose', 'As Directed'))
        m_price = clean_pdf_text(m.get('price', 'N/A'))
        p.setFont("Helvetica-Bold", 9)
        p.drawString(65, y, f"{idx}. {m_name} ({m_form})")
        p.setFont("Helvetica", 9)
        p.drawString(340, y, f"Dose: {m_dose}")
        p.drawString(460, y, f"Price: {m_price}")
        y -= 18
        if y < 80:
            p.showPage(); y = 750

    if tests_summary:
        y -= 14
        p.setFont("Helvetica-Bold", 11)
        p.drawString(50, y, "3. Recommended Diagnostic Investigations:")
        y -= 18
        p.setFont("Helvetica", 9)
        for t_idx, t in enumerate(tests_summary, start=1):
            t_name = clean_pdf_text(t.get('name', ''))
            t_cost = clean_pdf_text(t.get('cost', 'N/A'))
            p.setFont("Helvetica-Bold", 9)
            p.drawString(65, y, f"{t_idx}. {t_name}")
            p.setFont("Helvetica", 9)
            p.drawString(420, y, f"Est. Cost: {t_cost}")
            y -= 16
            if y < 80:
                p.showPage(); y = 750

    p.setFont("Helvetica-Oblique", 8)
    p.drawString(50, 40, "Heuristic match scores are not probabilities. Verify original prescription with a clinician.")
    p.save()
    buffer.seek(0)
    return buffer


def generate_report_pdf(vitals, diagnosis, meds_summary, tests_summary, comparison, sources=None):
    """Build a direct-download PDF; use Windows' Bengali Unicode font when available."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=14 * mm, leftMargin=14 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm, title="MediScript AI Clinical Report")
    font_name = "MediScriptNotoBengali"
    font_path = Path(__file__).resolve().parent / "fonts" / "NotoSansBengali-Regular.ttf"
    if "MediScriptNotoBengali" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
        pdfmetrics.registerFontFamily(font_name, normal=font_name, bold=font_name,
                                      italic=font_name, boldItalic=font_name)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], fontName=font_name, fontSize=17,
                                 leading=23, textColor=colors.HexColor("#0f766e"), alignment=TA_CENTER, spaceAfter=8, shaping=True)
    section_style = ParagraphStyle("ReportSection", parent=styles["Heading2"], fontName=font_name, fontSize=12,
                                   leading=17, textColor=colors.HexColor("#0f766e"), spaceBefore=11, spaceAfter=5, shaping=True)
    body_style = ParagraphStyle("ReportBody", parent=styles["BodyText"], fontName=font_name, fontSize=9,
                                leading=13, wordWrap="LTR", shaping=True)
    small_style = ParagraphStyle("ReportSmall", parent=body_style, fontSize=8, leading=11, shaping=True)
    head_style = ParagraphStyle("ReportHead", parent=body_style, fontSize=8, leading=11,
                                textColor=colors.white, alignment=TA_CENTER, shaping=True)
    def para(value, style=body_style):
        value = html_escape(str(value or "")).replace("\n", "<br/>")
        return Paragraph(value or "—", style)

    story = [para("MediScript AI · Clinical Report / চিকিৎসা রিপোর্ট", title_style),
             para("রোগীর তথ্য: " + str(vitals or "দৃশ্যমান নয়")),
             para("রোগ / ডায়াগনসিস: " + str(diagnosis or "দৃশ্যমান নয়")),
             para("দুই AI read-এর মিল: " + (f"{comparison}%" if comparison is not None else "চালানো হয়নি")),
             para("ওষুধ / Medicines", section_style)]
    med_headers = ["ওষুধের নাম", "সাধারণ ব্যবহার", "ধরন", "সেবনবিধি", "তালিকাভুক্ত দাম"]
    med_data = [[para(h, head_style) for h in med_headers]]
    for med in meds_summary:
        med_data.append([para(med.get("name")), para(med.get("general_use")), para(med.get("form")),
                         para(med.get("dose")), para(med.get("price"))])
    if len(med_data) == 1:
        med_data.append([para("কোনো ওষুধ শনাক্ত হয়নি")] + [para("")] * 4)
    med_table = Table(med_data, colWidths=[35*mm, 56*mm, 23*mm, 38*mm, 28*mm], repeatRows=1, hAlign="LEFT")
    med_table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0f766e")),
        ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#cbd5e1")), ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("BACKGROUND", (0,1), (-1,-1), colors.white), ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5), ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
    story.extend([med_table, para("পরীক্ষা / Tests", section_style)])
    test_headers = ["পরীক্ষার নাম", "কেন করা হয়", "Popular / সরকারি রেট"]
    test_data = [[para(h, head_style) for h in test_headers]]
    for test in tests_summary:
        test_data.append([para(test.get("name")), para(test.get("reason")), para(test.get("cost"))])
    if len(test_data) == 1:
        test_data.append([para("কোনো পরীক্ষা শনাক্ত হয়নি"), para(""), para("")])
    test_table = Table(test_data, colWidths=[42*mm, 90*mm, 48*mm], repeatRows=1, hAlign="LEFT")
    test_table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0f766e")),
        ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#cbd5e1")), ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 5), ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5)]))
    story.append(test_table)
    valid_sources = [item for item in (sources or []) if str(item.get("url", "")).startswith("https://")]
    if valid_sources:
        story.append(para("তথ্যসূত্র / Sources", section_style))
        for source in valid_sources:
            title = html_escape(str(source.get("title", source["url"])))
            url = html_escape(str(source["url"]), quote=True)
            story.append(Paragraph(f'<link href="{url}" color="#0f766e">{title}</link>', small_style))
    story.extend([Spacer(1, 8), para("সাধারণ তথ্য হিসেবে দেখুন; প্রেসক্রিপশন ও ফলাফল চিকিৎসক/ফার্মাসিস্টের সঙ্গে যাচাই করুন। রোগের লেখা পড়ার স্কোর clinical diagnosis accuracy নয়.", small_style)])
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def transcribe_prescription(image, api_key, model="gemini-3.8-flash"):
    # Correct phone-camera orientation and keep much more detail than the old
    # 1280px cap, which blurred small handwritten strengths and doses.
    api_image = ImageOps.exif_transpose(image).copy()
    if max(api_image.size) > 2400:
        ratio = 2400.0 / max(api_image.size)
        new_size = (int(api_image.size[0] * ratio), int(api_image.size[1] * ratio))
        api_image = api_image.resize(new_size, Image.Resampling.LANCZOS)

    prompt = """
    Transcribe only writing visible in this prescription image. Do not guess, diagnose,
    explain medicine uses, or invent prices. Use empty strings for unreadable fields.
    For diagnosis_text_confidence, estimate only how clearly the written diagnosis can be read
    from the image, from 0 to 100. This is not clinical diagnostic accuracy.
    Return only valid JSON with this structure:
    {"patient_info":"visible age/sex/date or empty","diagnosis":"visible note or empty",
     "diagnosis_text_confidence":0,
     "medicines":[{"name":"visible brand/name","dosage_form":"visible form or empty",
     "strength":"visible strength or empty","dose":"visible dose or empty","duration":"visible duration or empty"}],
     "tests":[{"test_name":"visible test name"}]}
    """
    # Use the SDK's default retry policy, with a bounded per-request timeout.
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=25000),
    )
    last_error = None
    # Ask Gemini for schema-constrained output so OCR responses remain parseable.
    extraction_schema = {
        "type": "OBJECT",
        "required": ["patient_info", "diagnosis", "diagnosis_text_confidence", "medicines", "tests"],
        "properties": {
            "patient_info": {"type": "STRING"},
            "diagnosis": {"type": "STRING"},
            "diagnosis_text_confidence": {"type": "INTEGER"},
            "medicines": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "required": ["name", "dosage_form", "strength", "dose", "duration"],
                    "properties": {
                        "name": {"type": "STRING"},
                        "dosage_form": {"type": "STRING"},
                        "strength": {"type": "STRING"},
                        "dose": {"type": "STRING"},
                        "duration": {"type": "STRING"},
                    },
                },
            },
            "tests": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "required": ["test_name"],
                    "properties": {"test_name": {"type": "STRING"}},
                },
            },
        },
    }
    # Use Gemini 3.8 Flash first and Gemini 3.6 Flash as fallback.
    candidate_models = list(dict.fromkeys((model, "gemini-3.6-flash")))
    for candidate_model in candidate_models:
        try:
            generation_config = types.GenerateContentConfig(
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_schema=extraction_schema,
            )
            response = client.models.generate_content(
                model=candidate_model,
                contents=[prompt, api_image],
                config=generation_config,
            )
            content = (response.text or "").strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I).strip()
            extracted = json.loads(content)
            if isinstance(extracted, dict):
                extracted["_model_used"] = candidate_model
                return extracted, None
            last_error = "Gemini returned an invalid response format."
        except Exception as exc:
            last_error = exc
            error = str(exc).upper()
            # Try the fallback model for model-specific outages or unavailable IDs.
            # A 429 usually means project-wide quota exhaustion, so another model
            # request would waste time and hit the same limit.
            if isinstance(exc, json.JSONDecodeError):
                continue
            model_unavailable = any(code in error for code in ("404", "NOT_FOUND", "MODEL_NOT_FOUND"))
            service_unavailable = any(code in error for code in (
                "408", "500", "502", "503", "504", "UNAVAILABLE",
                "SERVICE_UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED",
            ))
            if not (model_unavailable or service_unavailable):
                break
    if isinstance(last_error, json.JSONDecodeError):
        return None, "Gemini-র OCR উত্তর সম্পূর্ণ JSON হয়নি। একই ছবি দিয়ে আবার চেষ্টা করুন; প্রেসক্রিপশনের লেখা পরিষ্কার ও পুরোটা ফ্রেমে রাখুন।"
    return None, last_error



def process_prescription_image(image, api_key, compare_reads=False):
    """Run slow model calls off the Streamlit render thread."""
    extracted, error = transcribe_prescription(image, api_key)
    comparison = None
    second_error = None
    if extracted and compare_reads:
        second_result, second_error = transcribe_prescription(image, api_key, model="gemini-3.6-flash")
        if second_result:
            comparison = compare_transcriptions(extracted, second_result)
    return extracted, comparison, error, second_error


@st.fragment(run_every=1.0)
def poll_ocr_job(cache_key, image_bytes):
    """Poll OCR in the background so uploading does not block the app UI."""
    analysis = st.session_state.get("prescription_analysis", {})
    if analysis.get("key") != cache_key:
        return
    future = analysis.get("ocr_future")
    if future is None:
        return
    if not future.done():
        elapsed = max(0, int(time.time() - analysis.get("started_at", time.time())))
        st.info(f"ছবি আপলোড সম্পন্ন · Gemini-তে OCR অনুরোধ পাঠানো হয়েছে · অপেক্ষা: {elapsed} সেকেন্ড। সার্ভারের উত্তর এলে ফল এখানে দেখাবে।")
        if elapsed >= 45:
            st.caption("সার্ভারে চাপ থাকলে উত্তর পেতে আরও সময় লাগতে পারে।")
        return
    try:
        extracted, comparison, error, second_error = future.result()
    except Exception as exc:
        extracted, comparison, error, second_error = None, None, exc, None
    analysis["result"] = extracted
    analysis["comparison"] = comparison
    analysis["ocr_error"] = str(error) if error else None
    analysis["second_read_error"] = str(second_error) if second_error else None
    analysis.pop("ocr_future", None)
    st.session_state["prescription_analysis"] = analysis
    if extracted:
        record_session_audit(image_bytes, extracted, comparison)
    st.rerun()

def search_general_uses(api_key, medicines, tests):
    """Search only extracted names; do not send the image or patient details."""
    med_names = [str(x.get("search_query") or x.get("name", "")).strip()
                 for x in medicines if isinstance(x, dict) and (x.get("search_query") or x.get("name"))]
    test_names = []
    empty = {"medicines": {}, "tests": {}, "sources": []}
    if not med_names:
        return empty, None
    prompt = f"""
Use Google Search to find reliable general health references (prefer government health agencies,
recognized hospitals, NHS/MedlinePlus/NIH, or official drug information). Give one short Bangla
sentence for the common general use of each medicine. Do not infer the patient's diagnosis, recommend taking/stopping/changing
medicine, provide doses, or claim this prescription's doctor intended that use. If a reliable source
cannot confirm an item, write exactly 'বিশ্বস্ত তথ্য পাওয়া যায়নি'. Return one item for every input,
in the same order, and never omit an item. Return JSON matching this schema:
{{"medicines":[{{"index":1,"name":"...","line_bn":"..."}}],
  "tests":[]}}
Medicines: {json.dumps(med_names, ensure_ascii=False)}
Tests: []
"""
    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=25000,
                retry_options=types.HttpRetryOptions(
                    attempts=2, initial_delay=0.8, exp_base=2.0, max_delay=1.5,
                    jitter=0.3, http_status_codes=[408, 429, 500, 502, 503, 504],
                ),
            ),
        )
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config={
                "tools": [{"google_search": {}}],
                "response_format": {
                    "text": {
                        "mime_type": "application/json",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "medicines": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "index": {"type": "integer"},
                                            "name": {"type": "string"},
                                            "line_bn": {"type": "string"},
                                        },
                                        "required": ["index", "name", "line_bn"],
                                    },
                                },
                                "tests": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "index": {"type": "integer"},
                                            "name": {"type": "string"},
                                            "line_bn": {"type": "string"},
                                        },
                                        "required": ["index", "name", "line_bn"],
                                    },
                                },
                            },
                            "required": ["medicines", "tests"],
                        },
                    },
                },
                "temperature": 0.2,
            },
        )
        content = (response.text or "").replace("```json", "").replace("```", "").strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Grounded responses may add a short preamble around the JSON.
            start, end = content.find("{"), content.rfind("}")
            if start < 0 or end <= start:
                raise
            parsed = json.loads(content[start:end + 1])
        result = {"medicines": {}, "tests": {}, "sources": []}
        for key in ("medicines", "tests"):
            names = med_names if key == "medicines" else test_names
            for position, row in enumerate(parsed.get(key, []), start=1):
                if isinstance(row, dict):
                    index = row.get("index")
                    if not index and row.get("name") and names:
                        wanted = _key(row.get("name"))
                        index = next((i for i, name in enumerate(names, 1) if _key(name) == wanted), None)
                        if not index:
                            candidate = process.extractOne(str(row.get("name")), names, scorer=fuzz.token_set_ratio)
                            if candidate and candidate[1] >= 85:
                                index = names.index(candidate[0]) + 1
                    try:
                        index = int(index)
                    except (TypeError, ValueError):
                        index = position
                    if not 1 <= index <= len(names):
                        index = position
                    if 1 <= index <= len(names):
                        result[key][index] = {"name": str(row.get("name", names[index - 1])),
                                              "line_bn": str(row.get("line_bn", "")).strip()}
        for index, name in enumerate(med_names, start=1):
            if not result["medicines"].get(index, {}).get("line_bn"):
                result["medicines"][index] = {"name": name,
                    "line_bn": "এই ওষুধের নির্ভরযোগ্য সাধারণ ব্যবহার পাওয়া যায়নি। ব্র্যান্ডের জেনেরিক নাম মিলিয়ে আবার খুঁজুন।"}
        metadata = response.candidates[0].grounding_metadata if response.candidates else None
        for chunk in (getattr(metadata, "grounding_chunks", None) or []):
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            title = getattr(web, "title", None)
            if uri and uri.startswith("https://") and uri not in [s["url"] for s in result["sources"]]:
                result["sources"].append({"title": title or uri, "url": uri})
        # Keep useful grounded explanations even when Gemini omits source URLs;
        # the UI still displays the source panel whenever links are available.
        if not result["sources"]:
            return result, "Google Search ফল দিয়েছে, তবে cited link ফেরত দেয়নি।"
        return result, None
    except Exception as exc:
        return empty, str(exc)


@st.fragment(run_every=1.0)
def poll_explanation_job(cache_key):
    """Finish web lookups in the background and refresh once the result is ready."""
    analysis = st.session_state.get("prescription_analysis", {})
    if analysis.get("key") != cache_key:
        return
    future = analysis.get("explanation_future")
    if future is None:
        return
    if not future.done():
        st.caption("ওষুধ ও পরীক্ষার ব্যাখ্যা খোঁজা হচ্ছে… OCR ফল ইতিমধ্যে নিচে দেখা যাবে।")
        return
    try:
        explanations, search_error = future.result()
    except Exception as exc:
        explanations, search_error = {"medicines": {}, "tests": {}, "sources": []}, str(exc)
    analysis["explanations"] = explanations
    analysis["search_error"] = search_error
    analysis["explanation_version"] = 4
    analysis.pop("explanation_future", None)
    st.session_state["prescription_analysis"] = analysis
    st.rerun()


@st.fragment
def render_alternative_panel(idx, query, brand, alternatives):
    """Keep alternative pagination local so a refresh does not reload the page."""
    title_col, refresh_col = st.columns([5, 1])
    with title_col:
        st.markdown(f"**বিকল্প · {html_escape(str(brand))}**")
    offset_key = f"alternative_offset_{idx}_{_key(query)}"
    page_count = max(1, (len(alternatives) + 2) // 3)
    alt_page = int(st.session_state.get(offset_key, 0)) % page_count
    with refresh_col:
        if page_count > 1 and st.button("↻", key=f"alternative_refresh_{idx}_{_key(query)}", help="এই ওষুধের আরও বিকল্প দেখুন"):
            alt_page = (alt_page + 1) % page_count
            st.session_state[offset_key] = alt_page
    visible_alts = alternatives[alt_page * 3:alt_page * 3 + 3]
    alt_rows = []
    for item in visible_alts:
        alt_rows.append(
            '<div class="alternative-card"><div class="alternative-top">'
            f'<span class="alternative-name">{html_escape(str(item[0]))}</span>'
            f'<span class="alternative-price">{html_escape(str(item[2]))}</span></div>'
            '<div class="alternative-meta">জেনেরিক, strength ও form ডেটায় মিল</div>'
            f'<div class="alternative-meta">{html_escape(str(item[4]))} · {html_escape(str(item[3]))} · {html_escape(str(item[1]))}</div></div>'
        )
    st.markdown('<div class="alternative-list">' + ''.join(alt_rows) + '</div>', unsafe_allow_html=True)


def estimated_match_score(text, choices, exact_bonus=0):
    """A transparent matching heuristic, not a calibrated AI probability."""
    value = str(text or "").strip()
    if not value:
        return 0
    if not choices:
        return 45
    match = process.extractOne(value, choices, scorer=fuzz.token_set_ratio)
    if not match:
        return 25
    return 100 if _key(value) == _key(match[0]) else min(99, int(match[1]) + exact_bonus)


def render_match_meter(value, label):
    """Render a readable, labeled progress meter without Streamlit's split label layout."""
    percent = max(0, min(100, int(value)))
    safe_label = html_escape(str(label))
    st.markdown(
        f'<div class="match-meter" role="progressbar" aria-label="{safe_label}" '
        f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="{percent}">'
        f'<div class="match-meter__fill" style="width:{percent}%"></div>'
        f'<span class="match-meter__label">{safe_label}</span></div>',
        unsafe_allow_html=True,
    )


def record_session_audit(image_bytes, result, comparison=None):
    # Keep only non-identifying metadata in browser session memory; never persist
    # prescription images or patient details to disk/database.
    history = st.session_state.setdefault("audit_history", [])
    digest = hashlib.sha256(image_bytes).hexdigest()
    item = {
        "time": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "image_id": digest[:12],
        "medicines": len(result.get("medicines", [])) if isinstance(result.get("medicines", []), list) else 0,
        "tests": len(result.get("tests", [])) if isinstance(result.get("tests", []), list) else 0,
        "second_read_agreement": comparison,
    }
    if not history or history[-1].get("image_id") != item["image_id"]:
        history.append(item)
    st.session_state["audit_history"] = history[-10:]


def compare_transcriptions(first, second):
    """Return agreement across extracted fields to flag items for human review."""
    def normalize(value):
        return re.sub(r"\W+", "", str(value or "").casefold())
    fields = ["patient_info", "diagnosis"]
    a_meds = first.get("medicines", []) if isinstance(first.get("medicines", []), list) else []
    b_meds = second.get("medicines", []) if isinstance(second.get("medicines", []), list) else []
    fields.extend(f"med_{i}_{k}" for i in range(max(len(a_meds), len(b_meds))) for k in ("name", "strength", "dosage_form", "dose", "duration"))
    a_tests = first.get("tests", []) if isinstance(first.get("tests", []), list) else []
    b_tests = second.get("tests", []) if isinstance(second.get("tests", []), list) else []
    fields.extend(f"test_{i}" for i in range(max(len(a_tests), len(b_tests))))
    def value(obj, field):
        if field.startswith("med_"):
            _, index, key = field.split("_", 2)
            rows = obj.get("medicines", [])
            return rows[int(index)].get(key, "") if int(index) < len(rows) else ""
        if field.startswith("test_"):
            index = int(field.split("_")[1]); rows = obj.get("tests", [])
            row = rows[index] if index < len(rows) else {}
            return row.get("test_name", "") if isinstance(row, dict) else row
        return obj.get(field, "")
    checked = [f for f in fields if normalize(value(first, f)) or normalize(value(second, f))]
    if not checked:
        return 0
    return round(100 * sum(normalize(value(first, f)) == normalize(value(second, f)) for f in checked) / len(checked))

@st.dialog("ছবি বিশ্লেষণের আগে সম্মতি", width="small")
def prescription_consent_dialog():
    st.markdown("প্রেসক্রিপশনের ছবি OCR-এর জন্য Gemini API-তে পাঠানো হবে। ওষুধের সাধারণ ব্যবহার জানাতে শুধু শনাক্ত হওয়া ওষুধের নাম Google Search-এ যাবে—ছবি বা রোগীর নাম নয়। টেস্টের কারণ সরবরাহকৃত CSV থেকে দেখানো হয়। Search grounding-এর অতিরিক্ত খরচ হতে পারে।")
    st.markdown("এই অ্যাপ ছবি বা রোগীর তথ্য নিজস্ব disk/database-এ সংরক্ষণ করে না।")
    st.markdown("AI ভুল পড়তে পারে। ফল চিকিৎসক/ফার্মাসিস্টের যাচাই ছাড়া চিকিৎসার সিদ্ধান্তে ব্যবহার করবেন না।")
    if st.button("I Agree · আমি সম্মত", type="primary", use_container_width=True):
        st.session_state["prescription_consent"] = True
        st.rerun()


def render_decoder_page(system_api_key):
    st.markdown('<div class="section-intro"><h2>প্রেসক্রিপশন বিশ্লেষণ</h2><p>সম্মতি দিয়ে ছবি আপলোড, ক্যামেরা বা drag and drop-এর মাধ্যমে প্রেসক্রিপশন দিন।</p></div>', unsafe_allow_html=True)
    st.session_state.setdefault("prescription_consent", False)
    if not st.session_state["prescription_consent"]:
        st.info("ছবি বাছার আগে একবার সম্মতি দিতে হবে।")
        if st.button("প্রেসক্রিপশন আপলোড শুরু করুন", type="primary", use_container_width=True):
            prescription_consent_dialog()
        return

    st.caption("ছবি OCR-এর জন্য Gemini API-তে যায়; সাধারণ use তথ্যের জন্য শুধু নাম Google Search-এ যায়।")
    _, revoke_col = st.columns([5, 1])
    with revoke_col:
        if st.button("সম্মতি বাতিল", help="সম্মতি বাতিল করলে upload controls বন্ধ হবে"):
            st.session_state["prescription_consent"] = False
            st.rerun()
    with st.container(border=True):
        uploaded_file = st.file_uploader("ছবি বাছুন / click করুন অথবা drag and drop করুন", type=["jpg", "jpeg", "png"])
        with st.expander("📷 ক্যামেরা দিয়ে ছবি তুলুন", expanded=False):
            camera_file = st.camera_input("ছবি তুলুন")
    source_file = camera_file if camera_file is not None else uploaded_file
    compare_reads = False
    if source_file is not None:
        with st.expander("Advanced verification · অতিরিক্ত যাচাই", expanded=False):
            compare_reads = st.checkbox("দ্বিতীয় AI read দিয়ে তুলনা (অতিরিক্ত API call)", key="compare_reads")
    raw_image = None
    if source_file is not None:
        raw_image = Image.open(source_file)
        prev_l, prev_m, prev_r = st.columns([1, 2, 1])
        with prev_m:
            st.image(
                raw_image,
                caption=f"মূল ছবি: {raw_image.width} × {raw_image.height}px · OCR-এ সর্বোচ্চ 2400px পর্যন্ত পাঠানো হবে",
                width=320,
            )

    if source_file is not None:
        if not system_api_key:
            st.error("সিস্টেমে API Key নেই। অনুগ্রহ করে `.env` ফাইলে GEMINI_API_KEY দিন।")
            return

        image_bytes = source_file.getvalue()
        cache_key = hashlib.sha256(image_bytes + system_api_key.encode("utf-8")).hexdigest()
        cached = st.session_state.get("prescription_analysis", {})
        if cached.get("key") == cache_key and cached.get("ocr_future") is not None:
            poll_ocr_job(cache_key, image_bytes)
            return

        if cached.get("key") == cache_key and cached.get("ocr_error"):
            error_text = str(cached["ocr_error"])
            error_upper = error_text.upper()
            if any(code in error_upper for code in ("503", "UNAVAILABLE", "SERVICE_UNAVAILABLE")):
                st.error("Gemini-র 3.8 ও 3.6 Flash-এ চেষ্টা করেও সার্ভারের সাময়িক চাপের কারণে ছবিটি পড়া যায়নি। কিছুক্ষণ পরে একই ছবি দিয়ে আবার চেষ্টা করুন।")
            elif "429" in error_upper or "RESOURCE_EXHAUSTED" in error_upper:
                st.error("Gemini API project-এর request/token limit-এ পৌঁছেছে। কিছুক্ষণ অপেক্ষা করে আবার চেষ্টা করুন; বারবার চাপলে quota আরও দ্রুত শেষ হতে পারে।")
            else:
                st.error(f"ছবি বিশ্লেষণ শেষ হয়নি: {error_text}")
            if st.button("আবার চেষ্টা করুন", type="primary", key="retry_prescription_ocr"):
                st.session_state.pop("prescription_analysis", None)
                st.rerun()
            return

        if cached.get("key") == cache_key and cached.get("result"):
            extracted = cached.get("result")
            comparison = cached.get("comparison")
            last_err = None
            st.caption("এই ছবির বিশ্লেষণ এই সেশনে আগেই করা হয়েছে।" + (f" · মডেল: {extracted.get('_model_used')}" if extracted.get('_model_used') else ""))
            if compare_reads and comparison is None:
                st.info("দ্বিতীয় AI read চালু করতে আবার ছবি বিশ্লেষণ প্রয়োজন।")
        else:
            image_copy = Image.open(io.BytesIO(image_bytes)).copy()
            future = _OCR_POOL.submit(process_prescription_image, image_copy, system_api_key, compare_reads)
            st.session_state["prescription_analysis"] = {"key": cache_key, "ocr_future": future, "started_at": time.time()}
            poll_ocr_job(cache_key, image_bytes)
            return

        explanations = {"medicines": {}, "tests": {}, "sources": []}
        search_error = None
        search_pending = False
        if extracted:
            analysis_cache = st.session_state.get("prescription_analysis", {})
            if analysis_cache.get("key") == cache_key and analysis_cache.get("explanation_version") == 4:
                explanations = analysis_cache["explanations"]
                search_error = analysis_cache.get("search_error")
            else:
                meds_to_explain = extracted.get("medicines", []) if isinstance(extracted.get("medicines", []), list) else []
                tests_to_explain = extracted.get("tests", []) if isinstance(extracted.get("tests", []), list) else []
                for med in meds_to_explain:
                    if isinstance(med, dict) and med.get("name"):
                        med_info, _ = fuzzy_find_medicine(" ".join(str(v) for v in (med.get("name"), med.get("strength")) if v), med.get("dosage_form", ""))
                        if med_info and med_info[1]:
                            med["search_query"] = f"{med.get('name')} ({med_info[1]})"
                if analysis_cache.get("key") != cache_key or not analysis_cache.get("explanation_future"):
                    analysis_cache = {
                        **analysis_cache,
                        "key": cache_key,
                        "explanation_future": _EXPLANATION_POOL.submit(
                            search_general_uses, system_api_key, meds_to_explain, tests_to_explain
                        ),
                    }
                    st.session_state["prescription_analysis"] = analysis_cache
                search_pending = True
                poll_explanation_job(cache_key)

        if not extracted:
            error_text = str(last_err or "")
            error_upper = error_text.upper()
            if any(code in error_upper for code in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "504", "DEADLINE_EXCEEDED")):
                st.error("Gemini এখন ব্যস্ত বা সাময়িকভাবে অনুপলব্ধ। কিছুক্ষণ পরে আবার ছবি আপলোড করে চেষ্টা করুন।")
            elif "404" in error_upper or "NOT_FOUND" in error_upper:
                st.error("Gemini 3.8/3.6 Flash মডেল আপনার API key-তে পাওয়া যাচ্ছে না। Google AI Studio-তে সক্রিয় Gemini API key ও মডেল access যাচাই করুন।")
            else:
                st.error(f"এআই প্রসেসিং ত্রুটি: {last_err}")
        else:
            if comparison is not None:
                st.metric("দুই AI read-এর agreement", f"{comparison}%")
                st.warning("Agreement কম হলে নাম, strength, dose ও duration হাতে মিলিয়ে নিন। দুই মডেলের মিলও সঠিকতার নিশ্চয়তা নয়।")

            st.divider()
            st.subheader("ক্লিনিক্যাল অ্যানালাইসিস ও ওষুধের বিবরণ")
            st.caption("ওষুধের match score কেবল supplied dataset-এর সঙ্গে নামের আনুমানিক মিল; এটি নির্ভুলতার নিশ্চয়তা নয়। ওষুধের সাধারণ ব্যবহার Google Search থেকে আনা হয়; টেস্টের কারণ CSV থেকে। রোগের নাম পড়ার স্কোরটি OCR-পাঠের আনুমানিক স্কোর—রোগ নির্ণয়ের clinical accuracy নয়।")
            
            p_info = extracted.get('patient_info') or 'Not visible / দৃশ্যমান নয়'
            p_diag = extracted.get('diagnosis') or 'Not visible / দৃশ্যমান নয়'
            
            with st.container(border=True):
                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    st.markdown(f"**রোগীর তথ্য:** `{p_info}`")
                with col_p2:
                    st.markdown(f"**রোগ/ডায়াগনসিস:** `{p_diag}`")
                    try:
                        diagnosis_score = max(0, min(100, int(extracted.get("diagnosis_text_confidence", 0))))
                    except (TypeError, ValueError):
                        diagnosis_score = 0
                    if p_diag != 'Not visible / দৃশ্যমান নয়' and diagnosis_score:
                        render_match_meter(diagnosis_score, f"রোগের লেখা পড়ার AI আনুমানিক স্কোর · {diagnosis_score}% · রোগ নির্ণয়ের clinical accuracy নয়")
                    else:
                        st.caption("রোগের লেখা পড়ার স্কোর পাওয়া যায়নি; চিকিৎসাগত নির্ভুলতা এই টুল যাচাই করে না।")

            pdf_meds = []
            med_list = extracted.get("medicines", []) if isinstance(extracted.get("medicines", []), list) else []
            
            if med_list:
                st.markdown("#### প্রেসক্রাইব করা ওষুধ ও একই জেনেরিকের বিকল্প")
                for idx, med in enumerate(med_list, start=1):
                    if not isinstance(med, dict):
                        med = {"name": str(med)}
                    m_query = med.get("name", "")
                    m_strength = med.get("strength", "")
                    m_form = med.get("dosage_form", "")
                    m_dose = med.get("dose", "নির্দেশিত")
                    m_dur = med.get("duration", "")
                    dose_text = f"{m_dose} ({m_dur})" if m_dur else str(m_dose)
                    m_purpose = explanations.get("medicines", {}).get(idx, {}).get("line_bn", "")
                    m_purpose = m_purpose or (
                        "ব্যাখ্যা খোঁজা হচ্ছে…" if search_pending else
                        "এই নামটির নির্ভরযোগ্য সাধারণ ব্যবহার শনাক্ত করা যায়নি। ওষুধের প্যাকেটের জেনেরিক নাম চিকিৎসক বা ফার্মাসিস্টের সঙ্গে মিলিয়ে নিন।"
                    )
                    
                    lookup_query = " ".join(x for x in (m_query, m_strength) if x)
                    med_info, alts = fuzzy_find_medicine(lookup_query, m_form)
                    confidence = estimated_match_score(m_query, load_medicine_indexes()[0]) if m_query else 0
                    if med_info:
                        confidence = min(100, max(confidence, 75) + (10 if not m_strength or _strength_key(m_strength) in _key(med_info[6]) else -15))
                        if m_form and _key(m_form) in _key(med_info[3]):
                            confidence = min(99, confidence + 5)
                    confidence = max(0, min(100, confidence))
                    with st.container(border=True):
                        c_med, c_alt = st.columns([1.3, 1])
                        with c_med:
                            render_match_meter(confidence, f"ওষুধের নাম ও ডেটাসেট match · {confidence}% আনুমানিক" + (" · হাতে যাচাই করুন" if confidence < 75 else " · strength/form মিলিয়ে নিন"))
                            if med_info:
                                b_name, g_name, comp, form, price, ind, db_strength = med_info
                                price_display = dataset_price(b_name, g_name, form, lookup_query)
                                strength_label = f" · {db_strength}" if db_strength else ""
                                st.markdown(f"##### {idx:02d} · **{b_name}** ({form}{strength_label})")
                                st.caption(f"Generic: **{g_name}** | প্রস্তুতকারক: **{comp}**")
                                st.markdown(f'<div class="clinical-note"><span>ওষুধটির সাধারণ ব্যবহার</span>{html_escape(str(m_purpose))}</div>', unsafe_allow_html=True)
                                st.markdown(f'<div class="price-line"><span>তালিকাভুক্ত দাম</span><b>{html_escape(str(price_display))}</b></div>', unsafe_allow_html=True)
                                st.markdown(f'<div class="detail-line"><span>সেবনবিধি</span><b>{html_escape(str(dose_text))}</b></div>', unsafe_allow_html=True)
                                pdf_meds.append({"name": b_name, "general_use": m_purpose, "form": form, "dose": dose_text, "price": price_display, "confidence": f"{confidence}%"})
                            else:
                                price_display = "ডেটা পাওয়া যায়নি"
                                st.markdown(f"##### {idx:02d} · **{m_query}** ({m_form})")
                                st.markdown(f'<div class="clinical-note"><span>ওষুধটির সাধারণ ব্যবহার</span>{html_escape(str(m_purpose))}</div>', unsafe_allow_html=True)
                                st.markdown(f'<div class="price-line"><span>তালিকাভুক্ত দাম</span><b>{html_escape(str(price_display))}</b></div>', unsafe_allow_html=True)
                                st.markdown(f'<div class="detail-line"><span>সেবনবিধি</span><b>{html_escape(str(dose_text))}</b></div>', unsafe_allow_html=True)
                                pdf_meds.append({"name": m_query, "general_use": m_purpose, "form": m_form, "dose": dose_text, "price": price_display, "confidence": f"{confidence}%"})

                        with c_alt:
                            if med_info and alts:
                                render_alternative_panel(idx, m_query, b_name, alts)
                            else:
                                st.caption("কোনো সরাসরি বিকল্প প্রয়োজন নেই বা পাওয়া যায়নি।")

            tests_list = extracted.get("tests", []) if isinstance(extracted.get("tests", []), list) else []
            pdf_tests = []
            if tests_list:
                st.divider()
                st.markdown("#### নির্দেশিত ল্যাব টেস্ট ও ডেটাসেটের হার")
                for t_idx, t in enumerate(tests_list, start=1):
                    if isinstance(t, dict):
                        t_name = t.get("test_name", "")
                    else:
                        t_name = str(t)
                    t_purpose = explanations.get("tests", {}).get(t_idx, {}).get("line_bn", "")
                    t_purpose = t_purpose or (
                        "ব্যাখ্যা খোঁজা হচ্ছে…" if search_pending else
                        "এই পরীক্ষার কারণ CSV-তে নেই। কেন দেওয়া হয়েছে তা চিকিৎসকের কাছে জেনে নিন."
                    )
                    test_rates = dataset_test_price(t_name)
                    if test_rates and test_rates[2]:
                        t_purpose = test_rates[2]
                    test_confidence = estimated_match_score(t_name, [r.get("Test name", "") for r in load_test_rows()]) if t_name else 0
                    if test_rates:
                        test_confidence = max(test_confidence, 85)
                    if test_rates:
                        pop_rate, govt_rate, _csv_reason = test_rates
                        t_cost = f"Popular: ৳{pop_rate} | Govt: {govt_rate}"
                    else:
                        pop_rate = govt_rate = None
                        t_cost = "ডেটা পাওয়া যায়নি"

                    with st.container(border=True):
                        render_match_meter(test_confidence, f"টেস্টের নাম ও rate dataset match · {test_confidence}% আনুমানিক" + (" · হাতে যাচাই করুন" if test_confidence < 75 else ""))
                        tc1, tc2 = st.columns([3, 1])
                        with tc1:
                            st.markdown(f"**{t_idx}. {t_name}**")
                            st.markdown(
                                f'<div class="test-purpose-note"><strong>পরীক্ষাটি সাধারণত কেন করা হয়:</strong> {html_escape(str(t_purpose))}</div>',
                                unsafe_allow_html=True,
                            )
                        with tc2:
                            if test_rates:
                                govt_display = f"৳ {govt_rate}" if str(govt_rate).strip().isdigit() else str(govt_rate)
                                st.markdown(
                                    f'<div class="test-rates"><span>Popular Diagnostic</span><b>৳ {html_escape(str(pop_rate))}</b><span>Government</span><b>{html_escape(govt_display)}</b></div>',
                                    unsafe_allow_html=True,
                                )
                            else:
                                st.caption(t_cost)
                        pdf_tests.append({"name": t_name, "reason": t_purpose, "cost": t_cost, "confidence": f"{test_confidence}%"})

            if explanations.get("sources"):
                with st.expander("Google Search sources · উৎস দেখুন", expanded=False):
                    for source in explanations["sources"]:
                        st.markdown(f"- [{source['title']}]({source['url']})")
            elif search_error:
                st.caption("Search-এ নির্ভরযোগ্য cited source পাওয়া যায়নি; এই অবস্থাটি ব্যাখ্যা ও ডাউনলোড রিপোর্টে স্পষ্টভাবে দেখানো হয়েছে।")
            st.write("")
            pdf_report = generate_report_pdf(p_info, p_diag, pdf_meds, pdf_tests, comparison, explanations.get("sources", []))
            st.download_button(
                "PDF রিপোর্ট ডাউনলোড",
                data=pdf_report,
                file_name="mediscript_clinical_report.pdf",
                mime="application/pdf",
                use_container_width=True,
                disabled=search_pending,
            )
            st.divider()
            st.subheader("Session audit history · সেশন অডিট")
            st.caption("শুধু এই browser session-এ থাকে; patient name বা prescription image রাখা হয় না। Browser বন্ধ হলে history মুছে যেতে পারে।")
            audit_rows = st.session_state.get("audit_history", [])
            if audit_rows:
                st.dataframe(audit_rows, use_container_width=True, hide_index=True)
                audit_csv = io.StringIO()
                writer = csv.DictWriter(audit_csv, fieldnames=list(audit_rows[0].keys()))
                writer.writeheader(); writer.writerows(audit_rows)
                st.download_button("Session audit CSV ডাউনলোড", audit_csv.getvalue().encode("utf-8-sig"), "mediscript_session_audit.csv", "text/csv")



