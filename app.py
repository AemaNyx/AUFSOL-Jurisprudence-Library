import json
import re
from pathlib import Path
import streamlit as st
from rank_bm25 import BM25Okapi

st.set_page_config(page_title="Philippine Jurisprudence Library", page_icon="⚖️", layout="wide", initial_sidebar_state="expanded")

st.markdown('''
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:ital,wght@0,400;0,600;0,700;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap');

    :root {
        --primary-navy: #172C3F;
        --text-charcoal: #25313B;
        --secondary-sage: #56716D;
        --accent-gold: #C6A66A;
        --bg-warm-paper: #F6F5F1;
        --bg-white: #FFFFFF;
        --bg-sidebar: #F0F1EF;
        --border-color: #E5E7EB;
        --text-muted: #64748B;
        --font-ui: 'Inter', system-ui, sans-serif;
        --font-serif: 'Source Serif 4', Georgia, serif;
    }

    .stApp { background-color: var(--bg-warm-paper); font-family: var(--font-ui); }
    header[data-testid="stHeader"] { background-color: var(--primary-navy) !important; }
    section[data-testid="stSidebar"] { background-color: var(--bg-sidebar) !important; border-right: 1px solid var(--border-color); }
    
    .reading-pane {
        background-color: var(--bg-white);
        padding: 3rem 4rem;
        border-radius: 8px;
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
        border: 1px solid var(--border-color);
        max-width: 780px;
        margin: 0 auto;
    }

    h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 { font-family: var(--font-ui) !important; color: var(--primary-navy) !important; }
    
    .case-title { font-family: var(--font-serif); font-size: 32px; font-weight: 700; color: var(--primary-navy); line-height: 1.2; margin-bottom: 12px; }
    .case-metadata { font-family: var(--font-ui); font-size: 13px; color: var(--text-muted); margin-bottom: 24px; border-bottom: 1px solid var(--border-color); padding-bottom: 16px; }
    .legal-body { font-family: var(--font-serif); font-size: 17.5px; line-height: 1.7; color: var(--text-charcoal); text-align: left; }
    .legal-body h3 { font-family: var(--font-ui) !important; font-size: 22px !important; color: var(--primary-navy) !important; margin-top: 2rem; margin-bottom: 1rem; border-bottom: 1px solid #EAF1F8; padding-bottom: 0.5rem; }
    .legal-body strong { color: var(--primary-navy); font-weight: 600; }

    .badge { background-color: #EAF1F8; color: var(--primary-navy); padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; display: inline-block; margin-right: 8px; margin-bottom: 8px; }
    .badge-accent { background-color: var(--accent-gold); color: var(--bg-white); }
    
    .study-navigator { font-family: var(--font-ui); font-size: 14px; }
    .nav-item { color: var(--text-muted); padding: 6px 0; display: block; }
    
    @media (max-width: 768px) {
        .reading-pane { padding: 1.5rem; }
        .case-title { font-size: 26px; }
        .legal-body { font-size: 16px; }
    }
    </style>
''', unsafe_allow_html=True)

@st.cache_data
def load_library():
    library_path = Path("case_library/master_library.jsonl")
    cases = []
    if not library_path.exists():
        return [], None
    with open(library_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                cases.append(json.loads(line))
    tokenized_corpus = []
    for c in cases:
        text_block = f"{c.get('citation', '')} {c.get('category', '')} {c.get('digest', '')}".lower()
        tokenized_corpus.append([t for t in re.split(r'\W+', text_block) if t])
    bm25_index = BM25Okapi(tokenized_corpus) if tokenized_corpus else None
    return cases, bm25_index

cases, bm25 = load_library()

def extract_headings(markdown_text):
    return [re.sub(r'\*\*(.*?)\*\*', r'\1', m.group(1).strip()) for m in re.finditer(r"^###\s+(.*)$", markdown_text, re.MULTILINE)]

col1, col2 = st.columns([1, 2])
with col1: st.markdown("<h2 style='margin-top:0; color: var(--primary-navy);'>⚖️ Jurisprudence Library</h2>", unsafe_allow_html=True)
with col2: search_query = st.text_input("🔍 Search cases, doctrines, maxims, or G.R. numbers...", label_visibility="collapsed")

st.sidebar.markdown("<h3 style='margin-top: 0; color: var(--primary-navy);'>🗄️ Filing Cabinet</h3>", unsafe_allow_html=True)
if not cases:
    st.sidebar.warning("Library is empty.")
    st.stop()

categories = sorted(list(set(c.get("category", "Uncategorized") for c in cases)))
nav_mode = st.sidebar.radio("View Mode", ["📁 Browse by Category", "📚 All Cases"], label_visibility="collapsed")

selected_case = None
if nav_mode == "📁 Browse by Category":
    selected_cat = st.sidebar.selectbox("Select Category", categories, label_visibility="collapsed")
    folder_cases = [c for c in cases if c.get("category") == selected_cat]
    case_titles = [c.get("citation", "Unknown Case") for c in folder_cases]
    st.sidebar.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
    selected_title = st.sidebar.radio(f"Cases in {selected_cat}", case_titles, label_visibility="collapsed")
    if selected_title:
        selected_case = next((c for c in folder_cases if c.get("citation") == selected_title), None)
else:
    st.sidebar.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)
    all_titles = [c.get("citation", "Unknown Case") for c in cases]
    selected_title = st.sidebar.selectbox("Select from all cases", all_titles, label_visibility="collapsed")
    if selected_title:
        selected_case = next((c for c in cases if c.get("citation") == selected_title), None)

if search_query and bm25:
    query_tokens = [t for t in re.split(r'\W+', search_query.lower()) if t]
    scores = bm25.get_scores(query_tokens)
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:10]
    st.markdown("### Search Results")
    has_results = False
    for idx in top_indices:
        if scores[idx] > 0:
            has_results = True
            match = cases[idx]
            with st.container():
                c1, c2 = st.columns([5, 1])
                with c1:
                    st.markdown(f"**{match.get('citation')}**")
                    st.markdown(f"<span class='badge'>{match.get('category', 'Uncategorized')}</span>", unsafe_allow_html=True)
                with c2:
                    if st.button("Read", key=f"read_{idx}"):
                        selected_case = match
                        search_query = "" 
                st.divider()
    if not has_results: st.info("No cases matched your search criteria.")
    st.stop()

if selected_case:
    read_col, nav_col = st.columns([3.5, 1], gap="large")
    with read_col:
        st.markdown("<div class='reading-pane'>", unsafe_allow_html=True)
        st.markdown(f"<span class='badge badge-accent'>{selected_case.get('category', 'General')}</span>", unsafe_allow_html=True)
        st.markdown(f"<div class='case-title'>{selected_case.get('citation')}</div>", unsafe_allow_html=True)
        
        # Fixed the indentation issue here by using string concatenation instead of multi-line f-string
        meta_html = "<div class='case-metadata'>Cataloged: " + selected_case.get('date_cataloged', '')[:10] + " &nbsp;|&nbsp; <a href='" + selected_case.get('url', '#') + "' target='_blank' style='color: var(--secondary-sage); text-decoration: none;'>View Original Source ↗</a></div>"
        st.markdown(meta_html, unsafe_allow_html=True)
        
        st.markdown(f"<div class='legal-body'>{selected_case.get('digest', '')}</div></div>", unsafe_allow_html=True)
        
    with nav_col:
        st.markdown("<div class='study-navigator'><h4 style='color: var(--primary-navy); margin-bottom: 16px; border-bottom: 2px solid var(--border-color); padding-bottom: 8px;'>On This Case</h4>", unsafe_allow_html=True)
        headings = extract_headings(selected_case.get('digest', ''))
        if headings:
            for heading in headings:
                st.markdown(f"<div class='nav-item'>• {heading[:35] + '...' if len(heading) > 38 else heading}</div>", unsafe_allow_html=True)
        else:
            st.markdown("<span style='color: var(--text-muted);'>No structured headings found.</span>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
else:
    st.info("👈 Select a case from the Filing Cabinet to begin reading.")
