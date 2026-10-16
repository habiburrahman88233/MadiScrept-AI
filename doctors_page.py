import csv
import re
import unicodedata
from pathlib import Path
import streamlit as st
from rapidfuzz import fuzz

@st.cache_data(show_spinner=False)
def get_doctors_from_csv():
    try:
        path = Path(__file__).resolve().parent / "doctors.csv"
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            source = list(csv.DictReader(f))
        rows = []
        for d in source:
            address = " · ".join(x for x in (
                d.get("center_address", "").strip(),
                d.get("floor", "").strip(),
                d.get("designation_institute", "").strip(),
            ) if x)
            rows.append((
                d.get("doctor_name", "").strip(),
                d.get("specialty", "").strip(),
                d.get("degrees", "").strip(),
                d.get("center_name", "").strip(),
                address,
                d.get("visiting_days_time", "").strip(),
                "",
                d.get("contact_number", "").strip(),
            ))
        return rows, None
    except Exception as e:
        return [], str(e)

DISEASE_GUIDE_TEXT = [
    "**Medicine (মেডিসিন বিশেষজ্ঞ):** জ্বর, সর্দি, দীর্ঘস্থায়ী কাশি, পেটের গ্যাস, এসিডিটি, বদহজম, ডায়রিয়া, উচ্চ রক্তচাপ, দুর্বলতা ও টাইফয়েড—এ সকল রোগের চিকিৎসা করেন।",
    "**Cardiology (হৃদরোগ বিশেষজ্ঞ):** বুকে তীব্র ব্যথা, বুক ধড়ফড় করা, দম বন্ধ হয়ে আসা, হার্ট অ্যাটাক ও অনিয়মিত হৃদস্পন্দন—এ সকল রোগের চিকিৎসা করেন।",
    "**Gynecology (স্ত্রী ও প্রসূতি রোগ বিশেষজ্ঞ):** অনিয়মিত মাসিক, গর্ভকালীন জটিলতা, প্রসবসেবা, তলপেটে ব্যথা, জরায়ুর টিউমার ও বন্ধ্যাত্ব—এ সকল রোগের চিকিৎসা করেন।",
    "**Pediatrics (শিশু ও কিশোর রোগ বিশেষজ্ঞ):** নবজাতকের শ্বাসকষ্ট, জন্ডিস, নিউমোনিয়া, ডায়রিয়া, বমি, সঠিক শারীরিক বৃদ্ধি না হওয়া ও খিঁচুনি—এ সকল রোগের চিকিৎসা করেন।",
    "**Orthopedics (হাড়, জোড়া ও বাত-ব্যথা বিশেষজ্ঞ):** কোমর ও পিঠের ব্যথা, ঘাড়ের শিরটান, জয়েন্টের বাত-ব্যথা, হাড় ভাঙা, মচকে যাওয়া ও হাড়ের ক্ষয়—এ সকল রোগের চিকিৎসা করেন।",
    "**Neurology (ব্রেন, স্নায়ু ও নিউরো মেডিসিন):** দীর্ঘস্থায়ী মাথাব্যথা, মাইগ্রেন, স্ট্রোকের প্যারালাইসিস, হাত-পা অবশ ভাব, মৃগীরোগ ও স্মৃতিশক্তি লোপ—এ সকল রোগের চিকিৎসা করেন।",
    "**Dermatology (চর্ম, এলার্জি ও যৌনরোগ বিশেষজ্ঞ):** চুলকানি, দাদ, একজিমা, অ্যালার্জি, অতিরিক্ত চুল পড়া, তীব্র ব্রণ ও ত্বকের ইনফেকশন—এ সকল রোগের চিকিৎসা করেন।",
    "**ENT (নাক, কান ও গলা বিশেষজ্ঞ):** কান পাকা, কানে কম শোনা, নাকে মাংস বৃদ্ধি (পলিপ), সাইনোসাইটিস ও ঘন ঘন টনসিলের প্রদাহ—এ সকল রোগের চিকিৎসা করেন।",
    "**Urology (কিডনি, মূত্রনালী ও প্রস্টেট বিশেষজ্ঞ):** প্রস্রাবে জ্বালাপোড়া বা রক্ত যাওয়া, প্রস্রাব আটকে যাওয়া, কিডনিতে পাথর ও প্রস্টেট সমস্যা—এ সকল রোগের চিকিৎসা করেন।"
]

SPECIALTY_GROUPS = {
    "Medicine": ("medicine", "মেডিসিন", "internal medicine"),
    "Gynecology": ("gyn", "গাইনী", "গাইনি", "স্ত্রী", "প্রসূ", "প্রসু"),
    "Cardiology": ("cardio", "কার্ডিও", "হৃদরোগ", "হৃদযন্ত্র"),
    "Pediatrics": ("pediatr", "শিশু", "নবজাতক"),
    "Orthopedics": ("ortho", "অর্থোপেড", "হাড়", "হাড়", "অস্থি", "জোড়া", "জয়েন্ট"),
    "Neurology": ("neuro", "নিউরো", "স্নায়ু", "স্নায়ু", "মস্তিষ্ক", "ব্রেন"),
    "Dermatology": ("dermat", "চর্ম", "ত্বক", "skin"),
    "ENT": ("ent", "নাক", "কান", "গলা", "কর্ণ", "নাসা"),
    "Urology / Nephrology": ("uro", "nephro", "কিডনি", "মূত্র", "ইউরো", "নেফ্রো"),
    "Ophthalmology": ("ophthal", "চক্ষু", "চোখ"),
    "Psychiatry": ("psychiat", "মানসিক", "মনোরোগ"),
    "Surgery": ("surg", "সার্জ"),
    "Other specialties": (),
}


def specialty_matches_group(specialty, group):
    text = str(specialty or "").casefold()
    def matches(term):
        if term.isascii():
            return re.search(r"\b" + re.escape(term) + r"\w*", text) is not None
        return term in text
    if group == "Other specialties":
        return not any(matches(term) for terms in list(SPECIALTY_GROUPS.values())[:-1] for term in terms)
    return any(matches(term) for term in SPECIALTY_GROUPS[group])

PROBLEM_SPECIALTIES = {
    "Medicine": (
        "জ্বর", "জর", "jor", "jwar", "jhor", "fever", "high fever", "viral fever", "ডেঙ্গু", "dengue", "ম্যালেরিয়া", "malaria", "টাইফয়েড", "typhoid",
        "কাশি", "kashi", "cough", "শুকনো কাশি", "সর্দি", "sordi", "shordi", "cold", "thanda", "ফ্লু", "flu", "গলা ব্যথা", "গলাব্যথা", "sore throat", "sordi kashi", "সর্দি কাশি",
        "ডায়রিয়া", "diarrhea", "পাতলা পায়খানা", "patla paykhana", "pet kharap", "আমাশয়", "বমি", "vomiting", "vomit", "বমি বমি", "বদহজম", "indigestion",
        "গ্যাস", "gas", "গ্যাস্ট্রিক", "gastric", "এসিডিটি", "acidity", "অম্বল", "পেট ব্যথা", "পেটব্যথা", "pet betha", "stomach pain", "stomach ache", "কোষ্ঠকাঠিন্য", "constipation",
        "ডায়াবেটিস", "diabetes", "সুগার", "উচ্চ রক্তচাপ", "low blood pressure", "প্রেসার", "রক্তচাপ", "hypertension",
        "দুর্বলতা", "weakness", "রক্তস্বল্পতা", "anemia", "ওজন কম", "ওজন বেশি", "থাইরয়েড", "thyroid", "জন্ডিস", "jaundice",
        "শ্বাসকষ্ট", "shash kosto", "shortness of breath", "asthma", "অ্যাজমা", "বুকে কফ", "ক্ষুধা কম", "মাথা ঘোরা", "matha ghura", "বমি ভাব", "পা ফুলে", "শরীর ব্যথা", "body ache", "body pain",
        "কোলেস্টেরল", "cholesterol", "কিডনি রোগ", "লিভার", "পেটের সমস্যা", "মেডিসিন",
    ),
    "Cardiology": (
        "বুকে ব্যথা", "বুক ব্যথা", "buk betha", "chest pain", "বুক ধড়ফড়", "বুক ধড়ফড়", "buk dhorfor", "palpitation", "হার্টবিট", "হৃদস্পন্দন",
        "হার্ট", "heart", "heart problem", "heart disease", "হৃদরোগ", "হার্ট অ্যাটাক", "heart attack", "উচ্চ রক্তচাপ", "প্রেসার", "শ্বাসকষ্ট", "shortness of breath",
        "বুকে চাপ", "বুকে জ্বালা", "পা ফুলে", "হৃদযন্ত্র", "হার্ট ফেইলিউর",
    ),
    "Gynecology": (
        "মাসিক", "period", "পিরিয়ড", "অনিয়মিত মাসিক", "মাসিকের ব্যথা", "menstrual pain", "menstrual problem", "গর্ভাবস্থা", "pregnancy", "গর্ভবতী", "প্রসব",
        "তলপেটে ব্যথা", "বন্ধ্যাত্ব", "infertility", "স্ত্রী রোগ", "গাইনি", "সাদা স্রাব", "white discharge", "মেনোপজ", "menopause", "pcos", "পিসিওএস",
        "জরায়ু", "জরায়ু", "স্তনে ব্যথা", "স্তন", "গর্ভধারণ", "গর্ভপাত", "প্রসূতি",
    ),
    "Pediatrics": (
        "শিশু", "বাচ্চা", "child", "baby", "newborn", "নবজাতক", "শিশুর জ্বর", "শিশুর কাশি", "শিশুর ডায়রিয়া", "শিশুর বমি",
        "বাচ্চার জ্বর", "বাচ্চার কাশি", "নিউমোনিয়া", "pneumonia", "শিশুর খিঁচুনি", "শিশুর ওজন", "শিশুর বৃদ্ধি",
        "টিকা", "জন্মের পর", "নবজাতকের", "শিশু বিশেষজ্ঞ",
    ),
    "Orthopedics": (
        "কোমর ব্যথা", "komor betha", "back pain", "পিঠ ব্যথা", "ঘাড় ব্যথা", "ঘাড় ব্যথা", "neck pain", "joint pain", "জোড়া ব্যথা", "জোড়া ব্যথা", "jora betha", "হাড়", "হাড়", "জয়েন্ট", "জয়েন্ট",
        "হাঁটু ব্যথা", "knee pain", "কাঁধ ব্যথা", "shoulder pain", "গোড়ালি", "হাত ভাঙা", "পা ভাঙা", "ভাঙা", "মচক",
        "বাত ব্যথা", "arthritis", "আর্থ্রাইটিস", "অস্টিওপোরোসিস", "হাড় ক্ষয়", "সায়াটিকা", "sciatica", "জয়েন্ট ফুলে",
    ),
    "Neurology": (
        "মাথাব্যথা", "মাথা ব্যথা", "matha betha", "headache", "মাইগ্রেন", "migraine", "খিঁচুনি", "seizure", "convulsion", "অবশ", "প্যারালাইসিস",
        "স্ট্রোক", "stroke", "স্নায়ু", "স্নায়ু", "মাথা ঘোরা", "vertigo", "কাঁপুনি", "হাত কাঁপা", "স্মৃতি", "পারকিনসন",
        "ঘুমের সমস্যা", "বেলস পালসি", "নিউরোপ্যাথি",
    ),
    "Dermatology": (
        "চুলকানি", "chulkani", "itch", "rash", "র‍্যাশ", "দাদ", "একজিমা", "eczema", "অ্যালার্জি", "allergy", "ব্রণ", "acne",
        "চুল পড়া", "চুল পড়া", "hair fall", "hair loss", "ত্বক", "চর্ম", "সোরিয়াসিস", "psoriasis", "ফুসকুড়ি", "খোসপাঁচড়া", "scabies",
        "নখের সমস্যা", "ত্বকের দাগ", "মুখে ঘা", "চর্মরোগ", "ছত্রাক", "ফাঙ্গাস",
    ),
    "ENT": (
        "কান", "kan betha", "ear", "earache", "কানে ব্যথা", "কানে পুঁজ", "শুনতে কম", "নাক", "nak bondho", "নাক বন্ধ", "blocked nose", "নাক দিয়ে রক্ত", "গলা", "gola betha", "throat",
        "গলাব্যথা", "সাইনাস", "sinus", "টনসিল", "tonsil", "কণ্ঠস্বর", "ভয়েস", "নাক ডাকা", "কানে কম শোনা",
        "নাকের পলিপ", "গিলতে কষ্ট", "কানে শোঁ শোঁ",
    ),
    "Urology / Nephrology": (
        "প্রস্রাব", "urine", "urination", "kidney", "কিডনি", "কিডনিতে পাথর", "মূত্র", "প্রোস্টেট", "prostate",
        "প্রস্রাবে জ্বালা", "burning urination", "প্রস্রাবে রক্ত", "প্রস্রাব আটকে", "বারবার প্রস্রাব", "কিডনি ব্যথা", "kidney stone", "কিডনিতে পাথর", "কিডনি ফেইলিউর",
        "মূত্রনালী", "urinary", "বিছানা ভেজানো", "অণ্ডকোষ", "testicular",
    ),
    "Ophthalmology": (
        "চোখ", "eye", "দৃষ্টি", "ঝাপসা", "চোখে ব্যথা", "চোখ লাল", "চোখ চুলকায়", "চোখ চুলকায়", "ছানি", "cataract",
        "গ্লুকোমা", "glaucoma", "চোখ দিয়ে পানি", "চোখে কম দেখা", "চোখের ডাক্তার", "চশমা",
    ),
    "Psychiatry": (
        "মানসিক", "উদ্বেগ", "anxiety", "অনিদ্রা", "insomnia", "বিষণ্নতা", "depression", "মনোরোগ", "প্যানিক",
        "panic attack", "মানসিক চাপ", "stress", "আসক্তি", "নেশা", "ঘুম হয় না", "ঘুম হয় না", "আচরণগত সমস্যা",
    ),
    "Surgery": (
        "অস্ত্রোপচার", "অপারেশন", "হার্নিয়া", "হার্নিয়া", "hernia", "পিত্তথলি", "gallbladder", "অ্যাপেন্ডিক্স", "appendix",
        "পাইলস", "hemorrhoids", "ফিস্টুলা", "fistula", "গলব্লাডার", "পেটের গুটি", "গলায় গুটি", "ঘা", "পুঁজ",
    ),
}


def _normalize_problem_text(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"[^\w\u0980-\u09ff]+", " ", value)
    return " ".join(value.split())


def match_problem(problem):
    """Match Bangla, English, and Banglish problem names with cautious typo tolerance."""
    query = _normalize_problem_text(problem)
    if not query:
        return [], None, False

    aliases = []
    exact = []
    for group, terms in PROBLEM_SPECIALTIES.items():
        for term in terms:
            alias = _normalize_problem_text(term)
            if not alias:
                continue
            aliases.append((group, alias))
            if re.search(r"[a-z0-9]", alias):
                found = re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", query)
            else:
                found = alias in query
            if found:
                exact.append((group, alias))

    if exact:
        exact.sort(key=lambda item: len(item[1]), reverse=True)
        groups = list(dict.fromkeys(group for group, _ in exact))
        return groups, exact[0][1], False

    query_words = query.split()
    candidates = []
    for width in range(1, min(4, len(query_words)) + 1):
        candidates.extend(" ".join(query_words[i:i + width]) for i in range(len(query_words) - width + 1))

    best_score = 0
    best_aliases = []
    for group, alias in aliases:
        if len(alias.replace(" ", "")) < 4:
            continue
        for candidate in candidates:
            if abs(len(candidate.replace(" ", "")) - len(alias.replace(" ", ""))) > max(2, len(alias.replace(" ", "")) // 4):
                continue
            score = fuzz.ratio(alias, candidate)
            if score > best_score:
                best_score, best_aliases = score, [(group, alias)]
            elif score == best_score:
                best_aliases.append((group, alias))

    if best_score < 82 or not best_aliases:
        return [], None, False
    groups = list(dict.fromkeys(group for group, _ in best_aliases))
    return groups, best_aliases[0][1], True


def problem_groups(problem):
    return match_problem(problem)[0]


def render_doctor_pagination(page, page_count, key_suffix):
    if page_count <= 1:
        return
    if page_count <= 9:
        items = list(range(1, page_count + 1))
    else:
        items = [1, 2]
        if page > 4:
            items.append("…")
        items.extend(n for n in (page - 1, page, page + 1) if 2 < n < page_count - 1)
        if page < page_count - 3:
            items.append("…")
        items.extend([page_count - 1, page_count])
        items = list(dict.fromkeys(items))

    with st.container():
        left, center, right = st.columns([1, 4, 1])
        with center:
            nav = st.columns([0.85] + [0.42] * len(items) + [0.85], gap="small")
            with nav[0]:
                if st.button("‹", key=f"doctor_prev_{key_suffix}", disabled=page <= 1, help="আগের পৃষ্ঠা", use_container_width=True):
                    st.session_state["doctor_directory_page"] = page - 1
                    st.rerun()
            for idx, item in enumerate(items, start=1):
                with nav[idx]:
                    if item == "…":
                        st.button("…", key=f"doctor_ellipsis_{key_suffix}_{idx}", disabled=True, use_container_width=True)
                    elif st.button(str(item), key=f"doctor_page_{key_suffix}_{item}", type="primary" if item == page else "secondary", use_container_width=True):
                        st.session_state["doctor_directory_page"] = item
                        st.rerun()
            with nav[-1]:
                if st.button("›", key=f"doctor_next_{key_suffix}", disabled=page >= page_count, help="পরের পৃষ্ঠা", use_container_width=True):
                    st.session_state["doctor_directory_page"] = page + 1
                    st.rerun()
    st.caption(f"পৃষ্ঠা {page} / {page_count}")


def render_doctors_page():
    st.markdown('<div class="section-intro"><h2>কুষ্টিয়া চিকিৎসক ডিরেক্টরি</h2><p>সমস্যা, বিশেষজ্ঞ বিভাগ বা ডাক্তারের নাম দিয়ে খুঁজুন।</p></div>', unsafe_allow_html=True)

    with st.expander("বিশেষজ্ঞ বিভাগ সম্পর্কে জানুন", expanded=False):
        for line in DISEASE_GUIDE_TEXT:
            st.markdown(line, unsafe_allow_html=True)

    doctors, err = get_doctors_from_csv()

    if err:
        st.error(f"ডাক্তার CSV লোড এরর: {err}")
    elif not doctors:
        st.warning("doctors.csv-এ কোনো তথ্য পাওয়া যায়নি।")
    else:
        with st.container(border=True):
            st.markdown("**ডাক্তার খুঁজুন**")
            filter_col, query_col = st.columns([1, 2])
            with query_col:
                search_query = st.text_input("ডাক্তার / রোগ বা সমস্যা", placeholder="নাম বা সমস্যা লিখুন—যেমন ডাঃ ..., জ্বর, বুকে ব্যথা", key="doctor_combined_query")
            available_groups = [g for g in SPECIALTY_GROUPS if g != "Other specialties" and any(specialty_matches_group(d[1], g) for d in doctors)]
            if any(specialty_matches_group(d[1], "Other specialties") for d in doctors):
                available_groups.append("Other specialties")
            matched_problem_groups, matched_problem_term, fuzzy_problem_match = match_problem(search_query)
            recommendation = "সমস্যার মিল: " + ", ".join(matched_problem_groups) if matched_problem_groups else None
            if recommendation:
                filter_options = [recommendation] + [g for g in available_groups if g in matched_problem_groups]
            else:
                filter_options = ["সব বিশেষজ্ঞ"] + available_groups
            if st.session_state.get("doctor_specialty_filter") not in filter_options:
                st.session_state["doctor_specialty_filter"] = filter_options[0]
            with filter_col:
                selected_option = st.selectbox(
                    "সমস্যা অনুযায়ী বিশেষজ্ঞ" if recommendation else "বিশেষজ্ঞ",
                    filter_options,
                    key="doctor_specialty_filter",
                )
            if matched_problem_term:
                if fuzzy_problem_match:
                    st.caption(f'কাছাকাছি বানানের মিল: “{matched_problem_term}” · এই specialty-র ডাক্তার দেখানো হচ্ছে।')
                elif search_query.strip():
                    st.caption(f'রোগ/উপসর্গের মিল: “{matched_problem_term}”')
            st.caption("একই সার্চ বক্সে ডাক্তারের নাম, বিশেষজ্ঞের নাম বা উপসর্গ লিখুন।")

        filtered = doctors
        if recommendation and selected_option == recommendation:
            filtered = [d for d in filtered if any(specialty_matches_group(d[1], group) for group in matched_problem_groups)]
        elif recommendation:
            filtered = [d for d in filtered if specialty_matches_group(d[1], selected_option)]
        elif selected_option != "সব বিশেষজ্ঞ":
            filtered = [d for d in filtered if specialty_matches_group(d[1], selected_option)]
        if search_query.strip():
            query = search_query.strip().casefold()
            if not matched_problem_groups:
                filtered = [d for d in filtered if query in " ".join(str(x) for x in d[:6]).casefold()]

        st.markdown(f"**{len(filtered)} জন চিকিৎসক পাওয়া গেছে**")
        if not filtered:
            st.info("এই খোঁজের সঙ্গে মেলে এমন চিকিৎসক পাওয়া যায়নি।")
            return

        page_size = 10
        page_count = (len(filtered) + page_size - 1) // page_size
        page_key = "doctor_directory_page"
        page = int(st.session_state.get(page_key, 1))
        page = max(1, min(page, page_count))
        if page != st.session_state.get(page_key):
            st.session_state[page_key] = page
        render_doctor_pagination(page, page_count, "top")
        start = (page - 1) * page_size
        for idx, d in enumerate(filtered[start:start + page_size], start=start + 1):
            d_name, d_sp, d_deg, c_name, c_addr, d_days, d_time, d_phone = d
            with st.container(border=True):
                r_col1, r_col2 = st.columns([3, 1])
                with r_col1:
                    st.markdown(f"#### {d_name}")
                    st.caption(f"{d_sp} · {d_deg}")
                    st.write(f"**চেম্বার:** {c_name}")
                    st.write(f"**ঠিকানা:** {c_addr}")
                    st.write(f"**সময়:** {d_days}")
                with r_col2:
                    st.write(f"**যোগাযোগ:** {d_phone}")
                    first_phone = d_phone.split(",", 1)[0].strip().translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789"))
                    st.link_button("কল করুন", f"tel:{first_phone}", use_container_width=True)

        render_doctor_pagination(page, page_count, "bottom")
