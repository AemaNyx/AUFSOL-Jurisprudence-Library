import json
import re
from pathlib import Path
import streamlit as st
from rank_bm25 import BM25Okapi

st.set_page_config(page_title="Philippine Jurisprudence Cabinet", page_icon="⚖️", layout="wide")

st.markdown('''
    <style>
    .legal-title { font-family: 'Georgia', serif; color: #1E3A8A; font-size: 1.8rem; font-weight: bold; }
    .legal-body { font-family: 'Georgia', serif; font-size: 1.1rem; line-height: 1.6; color: #333333; }
    .badge { background-color: #DBEAFE; color: #1D4ED8; padding: 4px 8px; border-radius: 6px; font-size: 0.8rem; font-weight: bold; }
    </style>
''', unsafe_allow_html=True)

@st.cache_data
def load_library():
    library_path = Path("case_library/master_library.jsonl")
    cases = []
    if not library_path.exists():
        return [], None, []
    with open(library_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                cases.append(json.loads(line))
    tokenized_corpus = []
    for c in cases:
        text_block = f"{c.get('citation', '')} {c.get('category', '')} {c.get('digest', '')}".lower()
        tokens = re.split(r'\W+', text_block)
        tokenized_corpus.append([t for t in tokens if t])
    bm25_index = BM25Okapi(tokenized_corpus) if tokenized_corpus else None
    return cases, bm25_index, tokenized_corpus

cases, bm25, corpus = load_library()
st.sidebar.title("🗄️ Filing Cabinet")
if not cases:
    st.sidebar.warning("Library is empty. Run your Casebook Factory to digest cases.")
    st.stop()
subjects = sorted(list(set(c.get("category", "Uncategorized") for c in cases)))
selected_subject = st.sidebar.selectbox("📂 Browse by Subject", ["-- All Subjects --"] + subjects)
if selected_subject != "-- All Subjects --":
    folder_cases = [c for c in cases if c.get("category") == selected_subject]
else:
    folder_cases = cases
case_titles = [c.get("citation", "Unknown Case") for c in folder_cases]
selected_case_title = st.sidebar.selectbox("📄 Select Case", ["-- None --"] + case_titles)

st.title("⚖️ Philippine Jurisprudence Library")
search_query = st.text_input("🔍 Search Doctrines, Maxims, or Keywords (BM25 Exact Match)")
active_case = None

if search_query:
    query_tokens = [t for t in re.split(r'\W+', search_query.lower()) if t]
    scores = bm25.get_scores(query_tokens)
    top_n_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:5]
    st.subheader("Top Search Results")
    for idx in top_n_indices:
        if scores[idx] > 0:
            match = cases[idx]
            with st.expander(f"📑 {match.get('citation')} — Relevance Score: {scores[idx]:.2f}"):
                st.markdown(f"<span class='badge'>{match.get('category', '')}</span>", unsafe_allow_html=True)
                if st.button(f"Read Full Digest", key=f"btn_{idx}"):
                    active_case = match
elif selected_case_title != "-- None --":
    active_case = next((c for c in folder_cases if c.get("citation") == selected_case_title), None)

if active_case:
    st.markdown("---")
    st.markdown(f"<div class='legal-title'>{active_case.get('citation', 'Untitled')}</div>", unsafe_allow_html=True)
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**Subject Category:** {active_case.get('category', 'General')}")
    with col2:
        st.markdown(f"**Cataloged:** {active_case.get('date_cataloged', 'Unknown')[:10]}")
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(f"<div class='legal-body'>{active_case.get('digest', '')}</div>", unsafe_allow_html=True)
else:
    if not search_query:
        st.info("👈 Select a case from the Filing Cabinet or use the search bar above to begin reading.")
