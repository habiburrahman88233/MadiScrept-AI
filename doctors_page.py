import csv
import re
from pathlib import Path
import streamlit as st

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

def render_doctors_page():
    st.markdown('<div class="section-intro"><h2>কুষ্টিয়া চিকিৎসক ডিরেক্টরি</h2><p>বিভাগ বা নাম দিয়ে খুঁজে চেম্বার, সময় ও যোগাযোগের তথ্য দেখুন।</p></div>', unsafe_allow_html=True)

    with st.expander("বিশেষজ্ঞ বিভাগ সম্পর্কে জানুন", expanded=False):
        for line in DISEASE_GUIDE_TEXT:
            st.markdown(line, unsafe_allow_html=True)

    doctors, err = get_doctors_from_csv()

    if err:
        st.error(f"ডাক্তার CSV লোড এরর: {err}")
    elif not doctors:
        st.warning("doctors.csv-এ কোনো তথ্য পাওয়া যায়নি।")
    else:
        filter_col, search_col = st.columns([1, 1.5])
        with filter_col:
            available_groups = [g for g in SPECIALTY_GROUPS if g != "Other specialties" and any(specialty_matches_group(d[1], g) for d in doctors)]
            if any(specialty_matches_group(d[1], "Other specialties") for d in doctors):
                available_groups.append("Other specialties")
            selected_option = st.selectbox("বিশেষজ্ঞ বিভাগ", ["বিশেষজ্ঞ নির্বাচন করুন", "সব বিশেষজ্ঞ"] + available_groups)
        with search_col:
            search_name = st.text_input("ডাক্তার বা চেম্বারের নাম", placeholder="যেমন: ডাঃ ...")

        if selected_option == "বিশেষজ্ঞ নির্বাচন করুন" and not search_name.strip():
            st.info(f"ডিরেক্টরিতে {len(doctors)} জন চিকিৎসক আছেন। ফলাফল দেখতে বিভাগ বাছুন অথবা নাম দিয়ে খুঁজুন।")
            return

        filtered = doctors
        if selected_option not in ("বিশেষজ্ঞ নির্বাচন করুন", "সব বিশেষজ্ঞ"):
            filtered = [d for d in filtered if specialty_matches_group(d[1], selected_option)]
        if search_name.strip():
            query = search_name.strip().casefold()
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
        if page_count > 1:
            visible_pages = list(range(1, page_count + 1)) if page_count <= 7 else list(range(max(1, min(page - 2, page_count - 4)), max(1, min(page - 2, page_count - 4)) + 5))
            nav_cols = st.columns(len(visible_pages) + 2)
            with nav_cols[0]:
                if st.button("‹ আগের", key="doctor_page_prev", disabled=page <= 1, help="আগের পৃষ্ঠা"):
                    st.session_state[page_key] = page - 1
                    st.rerun()
            for col, page_number in zip(nav_cols[1:-1], visible_pages):
                with col:
                    if st.button(str(page_number), key=f"doctor_page_{page_number}", type="primary" if page_number == page else "secondary"):
                        st.session_state[page_key] = page_number
                        st.rerun()
            with nav_cols[-1]:
                if st.button("পরের ›", key="doctor_page_next", disabled=page >= page_count, help="পরের পৃষ্ঠা"):
                    st.session_state[page_key] = page + 1
                    st.rerun()
            st.caption(f"পৃষ্ঠা {page} / {page_count}")
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
