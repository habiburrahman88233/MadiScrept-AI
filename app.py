import os
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

from decoder_page import render_decoder_page
from doctors_page import render_doctors_page
from about_page import render_about_page

load_dotenv()
SYSTEM_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")

st.set_page_config(
    page_title="MediScript AI | Clinical Decision Support",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="collapsed"
)

css_path = Path(__file__).resolve().parent / "ui.css"
if css_path.exists():
    st.markdown(f"<style>{css_path.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

st.markdown(
    '<div class="ambient-medical" aria-hidden="true"><span>+</span><span>+</span><span>+</span><span>+</span></div>',
    unsafe_allow_html=True,
)

st.markdown(
    """<div class="brand-head" id="home">
      <a class="brand-home" href="?page=Prescription#home" title="Home / মূল পৃষ্ঠা" aria-label="MediScript AI home">
      <div class="brand-mark"><svg viewBox="0 0 32 32" aria-hidden="true">
        <path d="M8 4v8a8 8 0 0 0 16 0V4M8 4H5v4m19-4h3v4M16 20v2a5 5 0 0 0 10 0v-1" />
        <circle cx="26" cy="19" r="2.5" /><path d="M5 4h6M21 4h6" />
      </svg></div>
      <div><div class="brand-title">MediScript <span>AI</span></div>
      <div class="brand-subtitle">Prescription reading and healthcare information</div></div></a>
    </div>""",
    unsafe_allow_html=True,
)

# Query parameter lets the linked brand return to the app's opening page.
pages = ["Prescription", "Doctor directory", "About"]
try:
    requested_page = st.query_params.get("page", "Prescription")
except Exception:
    requested_page = "Prescription"
if requested_page in pages and requested_page != st.session_state.get("_nav_query_page"):
    st.session_state["main_navigation"] = requested_page
    st.session_state["_nav_query_page"] = requested_page
selected_page = st.radio(
    "Main navigation", pages,
    index=pages.index(requested_page) if requested_page in pages else 0,
    horizontal=True, label_visibility="collapsed", key="main_navigation",
)
try:
    st.query_params["page"] = selected_page
    st.session_state["_nav_query_page"] = selected_page
except Exception:
    pass

if selected_page == "Prescription":
    render_decoder_page(SYSTEM_GEMINI_KEY)
elif selected_page == "Doctor directory":
    render_doctors_page()
else:
    render_about_page()

st.markdown('<div class="app-footer">© 2026_Habibur_Rahman_Shakib (HRS)_MediScript AI · Healthcare information assistant</div>', unsafe_allow_html=True)
