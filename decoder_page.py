import io
import json
import csv
import hashlib
import re
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
    """Use the latest supplied Popular Diagnostic/Government test rate CSV."""
    wanted = _key(test_name)
    for row in load_test_rows():
        known = _key(row.get("Test Name"))
        if wanted and (wanted == known or wanted in known or known in wanted):
            pop = str(row.get("Popular Diagnostic Centre Rate (BDT)", "")).strip()
            govt = str(row.get("Govt Rate (BDT)", "")).strip()
            return pop or "N/A", govt or "N/A"
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


def generate_bilingual_html(vitals, diagnosis, meds_summary, tests_summary, comparison, sources=None):
    """Print-ready, Unicode bilingual report; browser print can save as PDF."""
    med_html = "".join(
        "<tr>" + "".join(f"<td>{html_escape(str(m.get(k, '')))}</td>" for k in ("name", "general_use", "form", "dose", "price", "confidence")) + "</tr>"
        for m in meds_summary
    ) or '<tr><td colspan="6">No medicines extracted / কোনো ওষুধ পাওয়া যায়নি</td></tr>'
    test_html = "".join(
        "<tr>" + "".join(f"<td>{html_escape(str(t.get(k, '')))}</td>" for k in ("name", "reason", "cost", "confidence")) + "</tr>"
        for t in tests_summary
    ) or '<tr><td colspan="4">No tests extracted / কোনো পরীক্ষা পাওয়া যায়নি</td></tr>'
    source_html = "".join(
        f'<li><a href="{html_escape(s["url"], quote=True)}">{html_escape(str(s["title"]))}</a></li>'
        for s in (sources or []) if str(s.get("url", "")).startswith("https://")
    ) or "<li>No cited search sources / cited source পাওয়া যায়নি</li>"
    agreement = f"{comparison}%" if comparison is not None else "Not run / চালানো হয়নি"
    safe_vitals, safe_diagnosis = html_escape(str(vitals)), html_escape(str(diagnosis))
    html = f'''<!doctype html><html lang="bn"><meta charset="utf-8"><title>MediScript AI report</title>
<style>body{{font-family:"Noto Serif Bengali","Nirmala UI",Vrinda,serif;color:#172b4d;max-width:950px;margin:32px auto;padding:0 22px;line-height:1.65}}h1,h2{{color:#0f766e}}.notice{{background:#fff7e6;padding:14px;border-left:4px solid #d97706}}table{{border-collapse:collapse;width:100%;margin:12px 0 24px}}th,td{{border:1px solid #dbe3eb;padding:9px;text-align:left}}th{{background:#edf6f5}}.score{{font-weight:bold;color:#0f766e}}@media print{{body{{margin:12mm auto}}button{{display:none}}}}</style>
<button onclick="window.print()">Print / PDF হিসেবে সংরক্ষণ</button><h1>MediScript AI · Clinical report / ক্লিনিক্যাল রিপোর্ট</h1>
<p><b>Patient info / রোগীর তথ্য:</b> {safe_vitals}</p><p><b>Clinical note / ক্লিনিক্যাল নোট:</b> {safe_diagnosis}</p>
<p><b>Independent reads agreement / দুই AI read-এর মিল:</b> {agreement}</p>
<h2>Medicines / ওষুধ</h2><table><thead><tr><th>Name / নাম</th><th>General use / সাধারণ ব্যবহার</th><th>Form / ধরন</th><th>Dose / সেবনবিধি</th><th>Dataset price / তালিকাভুক্ত দাম</th><th>Estimated match / আনুমানিক মিল</th></tr></thead><tbody>{med_html}</tbody></table>
<h2>Tests / পরীক্ষা</h2><table><thead><tr><th>Name / নাম</th><th>Common reason / সাধারণ কারণ</th><th>Dataset rates / তালিকাভুক্ত হার</th><th>Estimated match / আনুমানিক মিল</th></tr></thead><tbody>{test_html}</tbody></table>
<h2>Search sources / সার্চের উৎস</h2><ul>{source_html}</ul>
<div class="notice"><b>Important / জরুরি:</b> Medicine-use and test-reason summaries are general, search-grounded information; they do not explain the clinician's intent for this patient and can contain errors. Confidence percentages are heuristic text/data-match scores, not calibrated probabilities, and do not measure explanation accuracy. Review cited sources and verify the prescription with a qualified clinician. This tool is not a diagnosis or treatment recommendation. / ওষুধের ব্যবহার ও পরীক্ষার কারণ সাধারণ, search-grounded তথ্য; এই রোগীর ক্ষেত্রে চিকিৎসকের উদ্দেশ্য নয় এবং ভুল থাকতে পারে। Confidence শতাংশ calibrated probability নয়, ব্যাখ্যার accuracy-ও মাপে না। cited source দেখুন এবং চিকিৎসকের সঙ্গে প্রেসক্রিপশন যাচাই করুন।</div>
<p>Generated {html_escape(datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z'))} · Session-only processing / শুধু সেশনে প্রক্রিয়াকৃত</p></html>'''
    return html.encode("utf-8")


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
    Return only valid JSON with this structure:
    {"patient_info":"visible age/sex/date or empty","diagnosis":"visible note or empty",
     "medicines":[{"name":"visible brand/name","dosage_form":"visible form or empty",
     "strength":"visible strength or empty","dose":"visible dose or empty","duration":"visible duration or empty"}],
     "tests":[{"test_name":"visible test name"}]}
    """
    client = genai.Client(api_key=api_key)
    last_error = None
    for candidate_model in (model, "gemini-3.5-flash-lite"):
        try:
            if candidate_model == "gemini-3.8-flash":
                generation_config = types.GenerateContentConfig(
                    max_output_tokens=4096,
                    thinking_config=types.ThinkingConfig(thinking_level="low"),
                )
            else:
                generation_config = types.GenerateContentConfig(max_output_tokens=4096)
            response = client.models.generate_content(
                model=candidate_model,
                contents=[prompt, api_image],
                config=generation_config,
            )
            content = (response.text or "").replace("```json", "").replace("```", "").strip()
            extracted = json.loads(content)
            if isinstance(extracted, dict):
                return extracted, None
            last_error = "Gemini returned an invalid response format."
        except Exception as exc:
            last_error = exc
            error = str(exc).upper()
            # Retry another model only for availability/model errors, not for a
            # bad API key or a permission failure that will fail again anyway.
            if not any(code in error for code in ("503", "UNAVAILABLE", "404", "NOT_FOUND", "429", "RESOURCE_EXHAUSTED")):
                break
    return None, last_error


def search_general_uses(api_key, medicines, tests):
    """Search only extracted names; do not send the image or patient details."""
    med_names = [str(x.get("name", "")).strip() for x in medicines if isinstance(x, dict) and x.get("name")]
    test_names = [str(x.get("test_name", "")).strip() if isinstance(x, dict) else str(x).strip() for x in tests]
    test_names = [x for x in test_names if x]
    empty = {"medicines": {}, "tests": {}, "sources": []}
    if not med_names and not test_names:
        return empty, None
    prompt = f"""
Use Google Search to find reliable general health references (prefer government health agencies,
recognized hospitals, NHS/MedlinePlus/NIH, or official drug information). Give one short Bangla
sentence for the common general use of each medicine, and one short Bangla sentence for why each
test is commonly ordered. Do not infer the patient's diagnosis, recommend taking/stopping/changing
medicine, provide doses, or claim this prescription's doctor intended that use. If a reliable source
cannot confirm an item, write exactly 'বিশ্বস্ত তথ্য পাওয়া যায়নি'. Return one item for every input,
in the same order, and never omit an item. Return JSON matching this schema:
{{"medicines":[{{"index":1,"name":"...","line_bn":"..."}}],
  "tests":[{{"index":1,"name":"...","line_bn":"..."}}]}}
Medicines: {json.dumps(med_names, ensure_ascii=False)}
Tests: {json.dumps(test_names, ensure_ascii=False)}
"""
    try:
        client = genai.Client(api_key=api_key)
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
            for row in parsed.get(key, []):
                if isinstance(row, dict):
                    index = row.get("index")
                    if not index and row.get("name"):
                        names = med_names if key == "medicines" else test_names
                        wanted = _key(row.get("name"))
                        index = next((i for i, name in enumerate(names, 1) if _key(name) == wanted), None)
                        if not index:
                            candidate = process.extractOne(str(row.get("name")), names, scorer=fuzz.token_set_ratio)
                            if candidate and candidate[1] >= 85:
                                index = names.index(candidate[0]) + 1
                    if not index:
                        continue
                    result[key][int(index)] = {
                        "name": str(row.get("name", "")),
                        "line_bn": str(row.get("line_bn", "")),
                    }
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
    analysis["explanation_version"] = 3
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
    st.markdown("আপনার প্রেসক্রিপশন ছবি OCR-এর জন্য Gemini API-তে পাঠানো হবে। সাধারণ ব্যবহার/পরীক্ষার কারণ জানাতে শনাক্ত হওয়া ওষুধ ও test-এর নাম Google Search-এ পাঠানো হবে—ছবি বা রোগীর নাম নয়। Search grounding-এর অতিরিক্ত খরচ হতে পারে।")
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
        else:
            image_bytes = source_file.getvalue()
            cache_key = hashlib.sha256(image_bytes + system_api_key.encode("utf-8")).hexdigest()
            cached = st.session_state.get("prescription_analysis")
            if cached and cached.get("key") == cache_key:
                extracted = cached.get("result")
                comparison = cached.get("comparison")
                last_err = None
                st.caption("এই ছবির বিশ্লেষণ এই সেশনে আগেই করা হয়েছে।")
                if compare_reads and comparison is None:
                    with st.spinner("দ্বিতীয় AI read দিয়ে ফল তুলনা করা হচ্ছে..."):
                        second_result, second_error = transcribe_prescription(raw_image, system_api_key, model="gemini-3.7-flash")
                    if second_result:
                        comparison = compare_transcriptions(extracted, second_result)
                        st.session_state["prescription_analysis"]["comparison"] = comparison
                        record_session_audit(image_bytes, extracted, comparison)
                    else:
                        st.warning(f"দ্বিতীয় read সম্পন্ন হয়নি: {second_error}")
            else:
                with st.spinner("প্রেসক্রিপশনের লেখা শনাক্ত করে ডেটাসেটের সঙ্গে মিলানো হচ্ছে..."):
                    extracted, last_err = transcribe_prescription(raw_image, system_api_key)
                    comparison = None
                    if extracted and compare_reads:
                        second_result, second_error = transcribe_prescription(raw_image, system_api_key, model="gemini-3.7-flash")
                        if second_result:
                            comparison = compare_transcriptions(extracted, second_result)
                        else:
                            st.warning(f"দ্বিতীয় read সম্পন্ন হয়নি: {second_error}")
                if extracted:
                    st.session_state["prescription_analysis"] = {"key": cache_key, "result": extracted, "comparison": comparison}
                    record_session_audit(image_bytes, extracted, comparison)

            explanations = {"medicines": {}, "tests": {}, "sources": []}
            search_error = None
            search_pending = False
            if extracted:
                analysis_cache = st.session_state.get("prescription_analysis", {})
                if analysis_cache.get("key") == cache_key and analysis_cache.get("explanation_version") == 3:
                    explanations = analysis_cache["explanations"]
                    search_error = analysis_cache.get("search_error")
                else:
                    meds_to_explain = extracted.get("medicines", []) if isinstance(extracted.get("medicines", []), list) else []
                    tests_to_explain = extracted.get("tests", []) if isinstance(extracted.get("tests", []), list) else []
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
                st.error(f"এআই প্রসেসিং ত্রুটি: {last_err}")
            else:
                if comparison is not None:
                    st.metric("দুই AI read-এর agreement", f"{comparison}%")
                    st.warning("Agreement কম হলে নাম, strength, dose ও duration হাতে মিলিয়ে নিন। দুই মডেলের মিলও সঠিকতার নিশ্চয়তা নয়।")

                st.divider()
                st.subheader("ক্লিনিক্যাল অ্যানালাইসিস ও ওষুধের বিবরণ")
                st.caption("Confidence % হলো OCR text ও supplied dataset-এর মিলের আনুমানিক score—calibrated probability নয়। Google Search-এর use/reason সাধারণ তথ্য, আপনার prescription-এর উদ্দেশ্য নয়; চিকিৎসকের যাচাই করুন।")
                
                p_info = extracted.get('patient_info') or 'Not visible / দৃশ্যমান নয়'
                p_diag = extracted.get('diagnosis') or 'Not visible / দৃশ্যমান নয়'
                
                with st.container(border=True):
                    col_p1, col_p2 = st.columns([1, 1])
                    with col_p1:
                        st.markdown(f"**রোগীর তথ্য:** `{p_info}`")
                    with col_p2:
                        st.markdown(f"**ক্লিনিক্যাল নোট:** `{p_diag}`")
                    patient_score = 55 if p_info != 'Not visible / দৃশ্যমান নয়' else 0
                    note_score = 50 if p_diag != 'Not visible / দৃশ্যমান নয়' else 0
                    render_match_meter(patient_score, f"রোগীর তথ্য / Patient info · {patient_score}% আনুমানিক · independent source দিয়ে যাচাই হয়নি")
                    render_match_meter(note_score, f"ক্লিনিক্যাল নোট / Note · {note_score}% আনুমানিক · independent source দিয়ে যাচাই হয়নি")

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
                            "এই পরীক্ষার সাধারণ কারণ নির্ভরযোগ্য উৎসে পাওয়া যায়নি। কেন দেওয়া হয়েছে তা চিকিৎসকের কাছে জেনে নিন।"
                        )
                        test_rates = dataset_test_price(t_name)
                        test_confidence = estimated_match_score(t_name, [r.get("Test Name", "") for r in load_test_rows()]) if t_name else 0
                        if test_rates:
                            test_confidence = max(test_confidence, 85)
                        if test_rates:
                            pop_rate, govt_rate = test_rates
                            t_cost = f"Popular: ৳{pop_rate} | Govt: ৳{govt_rate}"
                        else:
                            pop_rate = govt_rate = None
                            t_cost = "ডেটা পাওয়া যায়নি"

                        with st.container(border=True):
                            render_match_meter(test_confidence, f"টেস্টের নাম ও rate dataset match · {test_confidence}% আনুমানিক" + (" · হাতে যাচাই করুন" if test_confidence < 75 else ""))
                            tc1, tc2 = st.columns([3, 1])
                            with tc1:
                                st.markdown(f"**{t_idx}. {t_name}**")
                                st.caption(f"পরীক্ষাটি সাধারণত কেন করা হয়: {t_purpose}")
                            with tc2:
                                if test_rates:
                                    st.markdown(
                                        f'<div class="test-rates"><span>Popular Diagnostic</span><b>৳ {html_escape(str(pop_rate))}</b><span>Government</span><b>৳ {html_escape(str(govt_rate))}</b></div>',
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
                bilingual_report = generate_bilingual_html(p_info, p_diag, pdf_meds, pdf_tests, comparison, explanations.get("sources", []))
                st.download_button(
                    "বাংলা + English রিপোর্ট · ডাউনলোড / Print → Save as PDF",
                    data=bilingual_report,
                    file_name="mediscript_bilingual_report.html",
                    mime="text/html",
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
