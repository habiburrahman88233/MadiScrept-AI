import streamlit as st


def render_about_page():
    st.markdown(
        """<div class="section-intro"><h2>আমাদের সম্পর্কে</h2>
        <p>MediScript AI — প্রেসক্রিপশন বুঝতে সহায়ক একটি শিক্ষামূলক স্বাস্থ্যতথ্য প্রকল্প</p></div>
        <div class="about-hero"><div class="about-kicker">Final-year project · Kushtia</div>
        <h2>স্বাস্থ্যতথ্যকে আরও সহজে বুঝতে</h2>
        <p>হাতের লেখা প্রেসক্রিপশন থেকে দৃশ্যমান তথ্য পড়া, ওষুধের তালিকার সঙ্গে মিল খোঁজা এবং স্থানীয় স্বাস্থ্যসেবার তথ্য এক জায়গায় দেখানোই এই প্রকল্পের লক্ষ্য।</p></div>""",
        unsafe_allow_html=True,
    )

    metrics = st.columns(3)
    for col, number, label in zip(
        metrics,
        ("01", "02", "03"),
        ("ছবি থেকে লেখা শনাক্ত", "ডেটাসেটের সঙ্গে মিল", "স্থানীয় সেবা খোঁজা"),
    ):
        with col:
            with st.container(border=True):
                st.markdown(f'<div class="about-stat">{number}</div><div>{label}</div>', unsafe_allow_html=True)

    left, middle, right = st.columns(3)
    with left:
        with st.container(border=True):
            st.markdown("### কীভাবে কাজ করে")
            st.markdown("১. প্রেসক্রিপশনের পরিষ্কার ছবি দিন।\n\n২. AI দৃশ্যমান লেখা শনাক্ত করে।\n\n৩. ওষুধ, পরীক্ষা ও ডাক্তার তথ্য সরবরাহ করা ডেটাসেটের সঙ্গে মিলিয়ে দেখানো হয়।")
    with middle:
        with st.container(border=True):
            st.markdown("### তথ্যের উৎস")
            st.markdown("- `medicine.csv` — ওষুধ, strength ও তালিকাভুক্ত দাম\n- `testrate.csv` — Popular Diagnostic ও সরকারি test rate\n- `doctors.csv` — চিকিৎসক ও চেম্বারের তথ্য")
    with right:
        with st.container(border=True):
            st.markdown("### সীমাবদ্ধতা ও গোপনীয়তা")
            st.markdown("- হাতের লেখা বা ঝাপসা ছবিতে OCR ভুল হতে পারে।\n- Confidence % dataset-match heuristic, calibrated probability নয়; hallucination শনাক্তের নিশ্চয়তা দেয় না।\n- দ্বিতীয় AI read ঐচ্ছিক এবং আরেকটি API call করে; দুই ফলের agreement সঠিকতার প্রমাণ নয়।\n- সম্মতির পর ছবি OCR-এর জন্য Gemini API-তে এবং শনাক্ত ওষুধ/test-এর নাম সাধারণ ব্যবহার খোঁজার জন্য Google Search-এ যায়। ছবি বা রোগীর পরিচিতি Search-এ পাঠানো হয় না।\n- OCR ফল পাওয়ার পর ওষুধ/test-এর ব্যাখ্যা আলাদাভাবে background-এ আসে; ছবি/রোগীর তথ্য disk বা database-এ রাখা হয় না।\n- Search-এর cited source সাধারণ ব্যবহার বোঝায়, এই prescription-এর নির্দেশনা নয়। source না এলে app-এ তা স্পষ্টভাবে জানানো হয়।\n- ফলাফল চিকিৎসকের পরামর্শ বা প্রেসক্রিপশনের বিকল্প নয়।")

    st.markdown("")
    st.caption("প্রকল্প উন্নয়ন: HRS · MediScript AI · 2026")
