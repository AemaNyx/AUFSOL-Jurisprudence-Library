import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import streamlit as st
from rank_bm25 import BM25Okapi

# --- 1. PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Philippine Jurisprudence Library",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- 2. SESSION STATE INITIALIZATION ---
if "theme_mode" not in st.session_state:
    st.session_state.theme_mode = "Light"
if "focus_mode" not in st.session_state:
    st.session_state.focus_mode = False
if "study_mode" not in st.session_state:
    st.session_state.study_mode = "📖 First-Time Read"
if "font_scale" not in st.session_state:
    st.session_state.font_scale = "Standard"
if "selected_lookup_key" not in st.session_state:
    st.session_state.selected_lookup_key = None
if "selected_category" not in st.session_state:
    st.session_state.selected_category = "-- All Categories --"
if "active_search_query" not in st.session_state:
    st.session_state.active_search_query = ""


# --- 3. DATA LOADING & BM25 LEXICAL INDEXING ---
@st.cache_data(show_spinner=False)
def load_library() -> Tuple[List[Dict[str, Any]], Optional[BM25Okapi], List[List[str]]]:
    """Loads all records from case_library/master_library.jsonl and builds BM25 index."""
    candidates = [
        Path("case_library/master_library.jsonl"),
        Path("master_library.jsonl"),
    ]
    cases: List[Dict[str, Any]] = []
    lib_path = next((p for p in candidates if p.exists()), None)

    if lib_path is not None:
        with open(lib_path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if not obj.get("lookup_key"):
                        obj["lookup_key"] = f"CASE_{idx + 1}"
                    # Composite unique ID for multi-version cases (same G.R. + different category)
                    obj["_uid"] = f"{obj.get('lookup_key', idx)}::{obj.get('category', '')}::{idx}"
                    cases.append(obj)
                except Exception:
                    continue

    tokenized_corpus: List[List[str]] = []
    for c in cases:
        raw_blob = f"{c.get('citation', '')} {c.get('lookup_key', '')} {c.get('category', '')} {c.get('digest', '')}".lower()
        tokens = [t for t in re.split(r"\W+", raw_blob) if t]
        tokenized_corpus.append(tokens)

    bm25 = BM25Okapi(tokenized_corpus) if tokenized_corpus else None
    return cases, bm25, tokenized_corpus


# --- 4. MARKDOWN DIGEST PARSER & STRUCTURED EXTRACTOR ---
MAJOR_SEC_PATTERN = re.compile(
    r"^(?:#{1,4}\s*)?(?:\*\*)?([1-7](?:\.5)?)[.)]?\s+(.+?)(?:\*\*)?\s*$"
)

INLINE_LABEL_PATTERN = re.compile(
    r"^(?:\*\*)?("
    r"Concise Recognizable Title|Full Official Caption|Citation & Metadata|"
    r"Key Parties & Roles(?:\s*\(The Cast\))?|How the Case Reached the Supreme Court|"
    r"Primary Topic Hierarchy|Secondary Topic Hierarchy(?:\s*\(if any\))?|"
    r"Full Text of Governing[^:]*|Historical Landmark Rule|"
    r"Factual Trigger(?:\s*\(Application to the Story\))?|"
    r"Main Thesis|Specific Contentions|Recitation-Ready Question|"
    r"Assigned Course & Syllabus Focus|Doctrinal Requisites[^:]*|"
    r"Subject-Specific Terms of Art[^:]*|Cold-Call Synthesis[^:]*|"
    r"Separate Opinions Summary|Verbatim/Near-Verbatim Fallo|Plain-Language Outcome"
    r")(?:\*\*)?\s*:\s*(.*)$",
    re.IGNORECASE,
)


def slugify(text: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return clean or "section"


def parse_digest_structure(digest_text: str) -> Dict[str, Any]:
    """
    Parses a standardized 7+1 Full-Text Substitute digest into:
    - sections: list of {num, title, anchor, raw_lines, subsections}
    - metadata_map: extracted key-value fields from Section 1, Section 2, Section 6.5, Section 7
    - quotes: list of extracted verbatim blockquotes / golden quotes
    - issues: list of extracted issues from Section 5
    """
    lines = digest_text.splitlines()
    sections: List[Dict[str, Any]] = []
    metadata_map: Dict[str, str] = {}
    quotes: List[str] = []
    issues: List[str] = []

    current_sec: Optional[Dict[str, Any]] = None

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            if current_sec is not None:
                current_sec["lines"].append("")
            continue

        # Check if line is a major section heading (1 through 7, including 6.5)
        heading_candidate = re.sub(r"^#{1,4}\s*", "", stripped)
        heading_candidate_clean = re.sub(r"^\*\*|\*\*$", "", heading_candidate).strip()
        m_sec = MAJOR_SEC_PATTERN.match(heading_candidate_clean)

        if m_sec and len(heading_candidate_clean) < 120:
            sec_num = m_sec.group(1)
            sec_title = m_sec.group(2).strip("* ").strip()
            anchor_id = f"sec-{sec_num.replace('.', '-')}-{slugify(sec_title)[:28]}"
            current_sec = {
                "num": sec_num,
                "title": f"{sec_num}. {sec_title}",
                "short_title": sec_title,
                "anchor": anchor_id,
                "lines": [],
            }
            sections.append(current_sec)
            continue

        # Also catch generic ### markdown headings if a digest uses custom headers
        if stripped.startswith("### ") and current_sec is None:
            h_text = stripped.lstrip("#").strip("* ").strip()
            anchor_id = f"sec-{slugify(h_text)[:32]}"
            current_sec = {
                "num": "•",
                "title": h_text,
                "short_title": h_text,
                "anchor": anchor_id,
                "lines": [],
            }
            sections.append(current_sec)
            continue

        if current_sec is None:
            current_sec = {
                "num": "0",
                "title": "Case Overview",
                "short_title": "Case Overview",
                "anchor": "sec-overview",
                "lines": [],
            }
            sections.append(current_sec)

        current_sec["lines"].append(raw_line)

        # Extract key inline metadata fields
        m_label = INLINE_LABEL_PATTERN.match(stripped)
        if m_label:
            k_raw = m_label.group(1).strip().lower()
            v_raw = re.sub(r"^\*\*|\*\*$", "", m_label.group(2).strip()).strip()
            if "concise recognizable title" in k_raw:
                metadata_map["concise_title"] = v_raw
            elif "full official caption" in k_raw:
                metadata_map["official_caption"] = v_raw
            elif "citation & metadata" in k_raw:
                metadata_map["citation_metadata"] = v_raw
            elif "key parties" in k_raw:
                metadata_map["key_parties"] = v_raw
            elif "how the case reached" in k_raw:
                metadata_map["procedural_posture"] = v_raw
            elif "primary topic hierarchy" in k_raw:
                metadata_map["primary_topic"] = v_raw
            elif "factual trigger" in k_raw and v_raw:
                metadata_map["factual_trigger"] = v_raw
            elif "cold-call synthesis" in k_raw and v_raw:
                metadata_map["cold_call"] = v_raw
            elif "subject-specific terms" in k_raw and v_raw:
                metadata_map["magic_words"] = v_raw
            elif "doctrinal requisites" in k_raw and v_raw:
                metadata_map["doctrinal_requisites"] = v_raw
            elif "verbatim/near-verbatim fallo" in k_raw and v_raw:
                metadata_map["fallo"] = v_raw
            elif "plain-language outcome" in k_raw and v_raw:
                metadata_map["outcome"] = v_raw

        # Collect issues in Section 5
        if current_sec["num"] == "5":
            if re.match(r"^\d+[.)]\s+", stripped) or "whether or not" in stripped.lower():
                issues.append(re.sub(r"^\*\*|\*\*$", "", stripped))

        # Collect Golden Verbatim Quotes
        if "golden verbatim quote" in stripped.lower() or stripped.startswith(">"):
            q_clean = re.sub(r"^>\s*", "", stripped)
            q_clean = re.sub(r"^(?:\*\*)?\(?c\)?\s*Golden Verbatim Quote(?:s)?(?:\*\*)?\s*:\s*", "", q_clean, flags=re.IGNORECASE).strip()
            if len(q_clean) > 25:
                quotes.append(q_clean)

    return {
        "sections": sections,
        "metadata": metadata_map,
        "quotes": quotes,
        "issues": issues,
    }


def format_inline_markdown(text: str, highlight_terms: Optional[List[str]] = None) -> str:
    """Safely converts Markdown bold/italic/code into styled HTML and highlights search terms."""
    escaped = html.escape(text)
    # Bold **text**
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    # Italic *text*
    escaped = re.sub(r"(?<!\*)\*([^\*\n]+?)\*(?!\*)", r"<em>\1</em>", escaped)
    # Badges like [PRIMARY SYLLABUS ISSUE] or [Procedural / Secondary Issue]
    escaped = re.sub(
        r"\[(PRIMARY SYLLABUS ISSUE)\]",
        r"<span class='issue-pill issue-primary'>PRIMARY SYLLABUS ISSUE</span>",
        escaped,
        flags=re.IGNORECASE,
    )
    escaped = re.sub(
        r"\[(Procedural\s*/\s*Secondary Issue|SECONDARY ISSUE|PROCEDURAL ISSUE)\]",
        r"<span class='issue-pill issue-secondary'>\1</span>",
        escaped,
        flags=re.IGNORECASE,
    )
    if highlight_terms:
        for term in highlight_terms:
            if len(term) >= 3:
                pattern = re.compile(f"({re.escape(term)})", re.IGNORECASE)
                escaped = pattern.sub(r"<mark class='search-mark'>\1</mark>", escaped)
    return escaped


def render_section_lines_to_html(lines: List[str], sec_num: str, highlight_terms: Optional[List[str]] = None) -> str:
    """Renders the lines of a digest section into structured, high-readability legal HTML."""
    html_parts: List[str] = []
    for raw in lines:
        s = raw.strip()
        if not s:
            continue

        # Clean stray markdown h3/h4 inside section
        if s.startswith("###") or s.startswith("####"):
            sub_h = s.lstrip("#").strip("* ").strip()
            html_parts.append(f"<h4 class='digest-subheading'>{format_inline_markdown(sub_h, highlight_terms)}</h4>")
            continue

        # Sub-headings like A. Petitioner's Arguments / B. Respondent's Arguments
        if re.match(r"^(?:\*\*)?[A-Z]\.[\s]+(?:Petitioner|Respondent|Public|Private|OSG|Accused|Defense)", s):
            clean_sub = re.sub(r"^\*\*|\*\*$", "", s)
            html_parts.append(f"<div class='party-header'>{format_inline_markdown(clean_sub, highlight_terms)}</div>")
            continue

        # Issue breakdown sub-parts: (a) Issue & Short Answer, (b) Step-by-Step Ratio, (c) Golden Verbatim Quote, (d) Plain-Language
        m_sub = re.match(r"^(?:\*\*)?(\([a-d]\)\s*[^:]+)(?:\*\*)?\s*:\s*(.*)$", s, re.IGNORECASE)
        if m_sub:
            sub_label = re.sub(r"^\*\*|\*\*$", "", m_sub.group(1)).strip()
            sub_rest = m_sub.group(2).strip()
            lower_lbl = sub_label.lower()
            if "(c)" in lower_lbl or "verbatim quote" in lower_lbl:
                html_parts.append(
                    f"<div class='quote-callout'>"
                    f"<div class='quote-callout-label'>📜 {html.escape(sub_label)}</div>"
                    f"<div class='quote-callout-body'>{format_inline_markdown(sub_rest, highlight_terms)}</div>"
                    f"</div>"
                )
            elif "(d)" in lower_lbl or "recit-ready" in lower_lbl or "plain-language" in lower_lbl:
                html_parts.append(
                    f"<div class='recit-callout'>"
                    f"<span class='recit-label'>🎯 {html.escape(sub_label)}:</span> "
                    f"{format_inline_markdown(sub_rest, highlight_terms)}"
                    f"</div>"
                )
            elif "(a)" in lower_lbl:
                html_parts.append(
                    f"<div class='issue-verdict-banner'>"
                    f"<span class='verdict-tag'>{html.escape(sub_label)}:</span> "
                    f"{format_inline_markdown(sub_rest, highlight_terms)}"
                    f"</div>"
                )
            else:
                html_parts.append(
                    f"<p class='digest-p'><strong class='inline-lead'>{html.escape(sub_label)}:</strong> "
                    f"{format_inline_markdown(sub_rest, highlight_terms)}</p>"
                )
            continue

        # Blockquotes
        if s.startswith(">"):
            q_body = s.lstrip(">").strip()
            html_parts.append(
                f"<blockquote class='legal-blockquote'>{format_inline_markdown(q_body, highlight_terms)}</blockquote>"
            )
            continue

        # Section 3 Chronological Timeline Entries: **[Date / Phase]:** Event
        if sec_num == "3" and re.match(r"^\*\*[^*]+\*\*\s*:?", s):
            m_time = re.match(r"^\*\*([^*]+?)\*\*\s*:?\s*(.*)$", s)
            if m_time:
                dt_label = m_time.group(1).strip(": ").strip()
                ev_body = m_time.group(2).strip()
                html_parts.append(
                    f"<div class='timeline-row'>"
                    f"<div class='timeline-date'>{html.escape(dt_label)}</div>"
                    f"<div class='timeline-event'>{format_inline_markdown(ev_body, highlight_terms)}</div>"
                    f"</div>"
                )
                continue

        # Standard Inline Labels (Concise Recognizable Title:, Main Thesis:, etc.)
        m_inline = INLINE_LABEL_PATTERN.match(s)
        if m_inline:
            lbl = re.sub(r"^\*\*|\*\*$", "", m_inline.group(1)).strip()
            val = m_inline.group(2).strip()
            if "factual trigger" in lbl.lower():
                html_parts.append(
                    f"<div class='trigger-box'>"
                    f"<div class='trigger-title'>⚡ {html.escape(lbl)}</div>"
                    f"<div class='trigger-text'>{format_inline_markdown(val, highlight_terms)}</div>"
                    f"</div>"
                )
            elif "verbatim/near-verbatim fallo" in lbl.lower():
                html_parts.append(
                    f"<div class='fallo-box'>"
                    f"<div class='fallo-title'>⚖️ {html.escape(lbl)}</div>"
                    f"<div class='fallo-text'>{format_inline_markdown(val, highlight_terms)}</div>"
                    f"</div>"
                )
            else:
                html_parts.append(
                    f"<p class='digest-p'><strong class='inline-lead'>{html.escape(lbl)}:</strong> "
                    f"{format_inline_markdown(val, highlight_terms)}</p>"
                )
            continue

        # Numbered items (e.g. Issues list 1., 2., 3.)
        m_num = re.match(r"^(\d{1,2}[.)])\s+(.+)$", s)
        if m_num:
            num_badge = m_num.group(1)
            rest_txt = m_num.group(2)
            html_parts.append(
                f"<div class='numbered-item'>"
                f"<span class='num-circle'>{html.escape(num_badge)}</span>"
                f"<div class='num-content'>{format_inline_markdown(rest_txt, highlight_terms)}</div>"
                f"</div>"
            )
            continue

        # Default paragraph
        html_parts.append(f"<p class='digest-p'>{format_inline_markdown(s, highlight_terms)}</p>")

    return "\n".join(html_parts)


def get_search_snippet(text: str, query_tokens: List[str], window: int = 220) -> str:
    """Extracts a contextual snippet around the first matched token with <mark> highlights."""
    if not text:
        return ""
    lower_text = text.lower()
    best_pos = -1
    for tok in query_tokens:
        if len(tok) >= 2:
            pos = lower_text.find(tok.lower())
            if pos != -1:
                best_pos = pos
                break
    if best_pos == -1:
        snippet = text[:window].replace("\n", " ")
    else:
        start = max(0, best_pos - 70)
        end = min(len(text), best_pos + window - 70)
        snippet = ("..." if start > 0 else "") + text[start:end].replace("\n", " ") + ("..." if end < len(text) else "")
    return format_inline_markdown(snippet, query_tokens)


# --- 5. THEME & RESPONSIVE CSS INJECTION ---
def inject_custom_css(theme: str, focus_mode: bool, font_scale: str):
    is_dark = theme == "Dark"

    # Font scaling map
    scale_map = {
        "Compact": ("16px", "28px", "19px"),
        "Standard": ("17.5px", "32px", "21px"),
        "Comfortable": ("19.5px", "35px", "23px"),
    }
    body_px, title_px, h3_px = scale_map.get(font_scale, ("17.5px", "32px", "21px"))

    if is_dark:
        colors = {
            "--bg-app": "#0F172A",
            "--bg-surface": "#1E293B",
            "--bg-elevated": "#263349",
            "--bg-sidebar": "#131C2E",
            "--bg-header": "#0B1120",
            "--text-primary": "#F1F5F9",
            "--text-secondary": "#CBD5E1",
            "--text-muted": "#94A3B8",
            "--accent-navy": "#93C5FD",
            "--accent-primary": "#3B82F6",
            "--accent-sage": "#5EEAD4",
            "--accent-gold": "#D4AF37",
            "--border-subtle": "#334155",
            "--callout-blue-bg": "rgba(59, 130, 246, 0.12)",
            "--callout-green-bg": "rgba(20, 184, 166, 0.12)",
            "--callout-gold-bg": "rgba(212, 175, 55, 0.14)",
            "--mark-bg": "rgba(234, 179, 8, 0.35)",
            "--mark-text": "#FEF08A",
            "--shadow-card": "0 8px 24px rgba(0, 0, 0, 0.35)",
        }
    else:
        colors = {
            "--bg-app": "#F6F5F1",
            "--bg-surface": "#FFFFFF",
            "--bg-elevated": "#F9F8F5",
            "--bg-sidebar": "#F0F1EF",
            "--bg-header": "#172C3F",
            "--text-primary": "#25313B",
            "--text-secondary": "#334155",
            "--text-muted": "#64748B",
            "--accent-navy": "#172C3F",
            "--accent-primary": "#1E3A5F",
            "--accent-sage": "#56716D",
            "--accent-gold": "#C6A66A",
            "--border-subtle": "#E5E7EB",
            "--callout-blue-bg": "#EAF1F8",
            "--callout-green-bg": "#EAF3EE",
            "--callout-gold-bg": "#F7F0E5",
            "--mark-bg": "#FEF08A",
            "--mark-text": "#172C3F",
            "--shadow-card": "0 2px 12px rgba(23, 44, 63, 0.05)",
        }

    css_vars = "\n".join(f"        {k}: {v};" for k, v in colors.items())

    focus_css = ""
    if focus_mode:
        focus_css = """
        section[data-testid="stSidebar"] {
            display: none !important;
        }
        .reader-sheet {
            max-width: 820px !important;
            margin: 0 auto !important;
        }
        """

    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400&family=IBM+Plex+Mono:wght@400;500&display=swap');

        :root {{
{css_vars}
            --font-ui: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            --font-serif: 'Source Serif 4', Georgia, Cambria, serif;
            --font-mono: 'IBM Plex Mono', monospace;
            --body-size: {body_px};
            --title-size: {title_px};
            --h3-size: {h3_px};
        }}

        /* App Surface & Base Typography */
        .stApp {{
            background-color: var(--bg-app) !important;
            color: var(--text-primary) !important;
            font-family: var(--font-ui) !important;
        }}

        /* Top Native Streamlit Bar */
        header[data-testid="stHeader"] {{
            background-color: var(--bg-app) !important;
            border-bottom: 1px solid var(--border-subtle);
        }}

        /* Reduce excessive top padding */
        .block-container {{
            padding-top: 1.5rem !important;
            padding-bottom: 4rem !important;
            max-width: 1440px !important;
        }}

        /* Sidebar Styling (The Filing Cabinet) */
        section[data-testid="stSidebar"] {{
            background-color: var(--bg-sidebar) !important;
            border-right: 1px solid var(--border-subtle) !important;
        }}
        section[data-testid="stSidebar"] * {{
            color: var(--text-primary) !important;
        }}

        /* Top Institutional Masthead Banner */
        .library-masthead {{
            background-color: var(--bg-header);
            color: #FFFFFF;
            padding: 1.15rem 1.75rem;
            border-radius: 10px;
            margin-bottom: 1.25rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 1rem;
            border-bottom: 3px solid var(--accent-gold);
            box-shadow: var(--shadow-card);
        }}
        .masthead-brand {{
            display: flex;
            align-items: center;
            gap: 0.85rem;
        }}
        .masthead-icon {{
            font-size: 1.75rem;
            line-height: 1;
        }}
        .masthead-title {{
            font-family: var(--font-serif);
            font-size: 1.45rem;
            font-weight: 700;
            color: #FFFFFF !important;
            letter-spacing: -0.01em;
            margin: 0;
        }}
        .masthead-subtitle {{
            font-family: var(--font-ui);
            font-size: 0.78rem;
            color: #CBD5E1;
            margin: 0;
            letter-spacing: 0.02em;
        }}
        .masthead-stats {{
            display: flex;
            gap: 0.75rem;
            align-items: center;
            flex-wrap: wrap;
        }}
        .stat-pill {{
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.18);
            color: #F8FAFC;
            font-family: var(--font-mono);
            font-size: 0.75rem;
            padding: 0.3rem 0.7rem;
            border-radius: 6px;
        }}

        /* Central Legal Reader Sheet */
        .reader-sheet {{
            background-color: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 2.75rem 3.25rem;
            max-width: 780px;
            margin: 0 auto 2rem auto;
            box-shadow: var(--shadow-card);
        }}

        /* Breadcrumb & Metadata Badges */
        .breadcrumb-bar {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 0.5rem;
            margin-bottom: 0.9rem;
        }}
        .cat-badge {{
            background-color: var(--callout-blue-bg);
            color: var(--accent-navy);
            border: 1px solid var(--border-subtle);
            font-family: var(--font-ui);
            font-size: 0.76rem;
            font-weight: 600;
            padding: 0.25rem 0.7rem;
            border-radius: 6px;
            letter-spacing: 0.01em;
        }}
        .gr-badge {{
            background-color: var(--callout-gold-bg);
            color: var(--text-primary);
            font-family: var(--font-mono);
            font-size: 0.75rem;
            font-weight: 500;
            padding: 0.25rem 0.65rem;
            border-radius: 6px;
        }}

        /* Case Title & Metadata Header */
        .case-main-title {{
            font-family: var(--font-serif);
            font-size: var(--title-size);
            font-weight: 700;
            color: var(--accent-navy);
            line-height: 1.25;
            margin: 0.25rem 0 0.75rem 0;
            letter-spacing: -0.015em;
        }}
        .case-meta-row {{
            font-family: var(--font-ui);
            font-size: 0.82rem;
            color: var(--text-muted);
            padding-bottom: 1.15rem;
            margin-bottom: 1.5rem;
            border-bottom: 1px solid var(--border-subtle);
            display: flex;
            flex-wrap: wrap;
            gap: 1rem;
            align-items: center;
        }}
        .source-link {{
            color: var(--accent-sage) !important;
            font-weight: 600;
            text-decoration: none;
        }}
        .source-link:hover {{
            text-decoration: underline;
        }}

        /* Digest Section Cards & Headings */
        .digest-section-block {{
            margin-top: 2.1rem;
            padding-top: 0.25rem;
        }}
        .digest-section-header {{
            display: flex;
            align-items: baseline;
            gap: 0.65rem;
            border-bottom: 2px solid var(--border-subtle);
            padding-bottom: 0.55rem;
            margin-bottom: 1.1rem;
            scroll-margin-top: 80px;
        }}
        .sec-num-badge {{
            background-color: var(--bg-header);
            color: #FFFFFF;
            font-family: var(--font-mono);
            font-size: 0.78rem;
            font-weight: 600;
            padding: 0.2rem 0.55rem;
            border-radius: 5px;
            flex-shrink: 0;
        }}
        .sec-heading-text {{
            font-family: var(--font-ui);
            font-size: var(--h3-size);
            font-weight: 700;
            color: var(--accent-navy);
            margin: 0;
            line-height: 1.3;
        }}

        /* Legal Body Text (Left-aligned, 1.7 line height) */
        .legal-prose {{
            font-family: var(--font-serif);
            font-size: var(--body-size);
            line-height: 1.72;
            color: var(--text-primary);
            text-align: left;
        }}
        .digest-p {{
            margin: 0 0 0.95rem 0;
            text-align: left;
        }}
        .inline-lead {{
            font-family: var(--font-ui);
            font-size: 0.92em;
            color: var(--accent-navy);
            font-weight: 600;
        }}
        .digest-subheading {{
            font-family: var(--font-ui);
            font-size: 1.05rem;
            font-weight: 600;
            color: var(--accent-navy);
            margin: 1.25rem 0 0.5rem 0;
        }}
        .party-header {{
            font-family: var(--font-ui);
            font-size: 0.95rem;
            font-weight: 700;
            color: var(--accent-navy);
            background-color: var(--bg-elevated);
            border-left: 3px solid var(--accent-sage);
            padding: 0.45rem 0.85rem;
            margin: 1rem 0 0.65rem 0;
            border-radius: 0 6px 6px 0;
        }}

        /* Specialized Callouts (Factual Trigger, Quotes, Recit Ready, Fallo) */
        .trigger-box {{
            background-color: var(--callout-green-bg);
            border-left: 4px solid var(--accent-sage);
            padding: 1rem 1.25rem;
            border-radius: 0 8px 8px 0;
            margin: 1rem 0 1.35rem 0;
        }}
        .trigger-title {{
            font-family: var(--font-ui);
            font-size: 0.82rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--accent-sage);
            margin-bottom: 0.35rem;
        }}
        .trigger-text {{
            font-family: var(--font-serif);
            font-size: var(--body-size);
            line-height: 1.65;
            color: var(--text-primary);
        }}

        .quote-callout, .legal-blockquote {{
            background-color: var(--callout-gold-bg);
            border-left: 4px solid var(--accent-gold);
            padding: 0.95rem 1.25rem;
            border-radius: 0 8px 8px 0;
            margin: 0.95rem 0;
            font-family: var(--font-serif);
            font-style: italic;
        }}
        .quote-callout-label {{
            font-family: var(--font-ui);
            font-style: normal;
            font-size: 0.78rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--accent-navy);
            margin-bottom: 0.35rem;
        }}

        .recit-callout {{
            background-color: var(--callout-blue-bg);
            border-left: 4px solid var(--accent-primary);
            padding: 0.9rem 1.15rem;
            border-radius: 0 8px 8px 0;
            margin: 0.85rem 0 1.25rem 0;
            font-family: var(--font-serif);
        }}
        .recit-label {{
            font-family: var(--font-ui);
            font-size: 0.84rem;
            font-weight: 700;
            color: var(--accent-navy);
        }}

        .issue-verdict-banner {{
            background-color: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-left: 4px solid var(--accent-navy);
            padding: 0.75rem 1rem;
            border-radius: 6px;
            margin: 1rem 0 0.75rem 0;
        }}
        .verdict-tag {{
            font-family: var(--font-ui);
            font-weight: 700;
            color: var(--accent-navy);
        }}

        .fallo-box {{
            background-color: var(--bg-elevated);
            border: 1px solid var(--border-subtle);
            border-top: 3px solid var(--accent-gold);
            padding: 1.1rem 1.35rem;
            border-radius: 8px;
            margin: 1.1rem 0;
        }}
        .fallo-title {{
            font-family: var(--font-ui);
            font-size: 0.82rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--accent-navy);
            margin-bottom: 0.4rem;
        }}

        /* Section 3 Timeline Rows */
        .timeline-row {{
            display: grid;
            grid-template-columns: 165px 1fr;
            gap: 1rem;
            padding: 0.65rem 0;
            border-bottom: 1px dashed var(--border-subtle);
            align-items: baseline;
        }}
        .timeline-date {{
            font-family: var(--font-ui);
            font-size: 0.86rem;
            font-weight: 700;
            color: var(--accent-navy);
        }}
        .timeline-event {{
            font-family: var(--font-serif);
            font-size: var(--body-size);
            line-height: 1.65;
        }}

        /* Section 5 Numbered Issues */
        .numbered-item {{
            display: flex;
            gap: 0.75rem;
            align-items: flex-start;
            margin-bottom: 0.85rem;
        }}
        .num-circle {{
            font-family: var(--font-mono);
            font-size: 0.82rem;
            font-weight: 600;
            color: var(--accent-navy);
            background-color: var(--callout-blue-bg);
            padding: 0.15rem 0.5rem;
            border-radius: 5px;
            flex-shrink: 0;
            margin-top: 0.15rem;
        }}
        .issue-pill {{
            font-family: var(--font-ui);
            font-size: 0.7rem;
            font-weight: 700;
            padding: 0.15rem 0.5rem;
            border-radius: 4px;
            margin-right: 0.4rem;
            vertical-align: middle;
        }}
        .issue-primary {{
            background-color: var(--accent-gold);
            color: #FFFFFF;
        }}
        .issue-secondary {{
            background-color: var(--callout-blue-bg);
            color: var(--accent-navy);
        }}

        /* Right Sidebar: Study Navigator & Quick Aids */
        .navigator-card {{
            background-color: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 1.2rem 1.25rem;
            position: sticky;
            top: 1rem;
            box-shadow: var(--shadow-card);
        }}
        .navigator-title {{
            font-family: var(--font-ui);
            font-size: 0.82rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--accent-navy);
            margin-bottom: 0.75rem;
            padding-bottom: 0.5rem;
            border-bottom: 2px solid var(--border-subtle);
        }}
        .toc-link {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.42rem 0.5rem;
            border-radius: 6px;
            text-decoration: none !important;
            color: var(--text-secondary) !important;
            font-family: var(--font-ui);
            font-size: 0.82rem;
            line-height: 1.35;
            transition: background-color 0.15s, color 0.15s;
        }}
        .toc-link:hover {{
            background-color: var(--callout-blue-bg);
            color: var(--accent-navy) !important;
            font-weight: 600;
        }}
        .toc-num {{
            font-family: var(--font-mono);
            font-size: 0.72rem;
            font-weight: 600;
            color: var(--accent-sage);
            min-width: 24px;
        }}

        /* Search Highlight */
        mark.search-mark {{
            background-color: var(--mark-bg);
            color: var(--mark-text);
            padding: 0.05rem 0.25rem;
            border-radius: 3px;
            font-weight: 600;
        }}

        /* Search Result Card */
        .search-card {{
            background-color: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-left: 4px solid var(--accent-navy);
            border-radius: 8px;
            padding: 1.15rem 1.35rem;
            margin-bottom: 0.85rem;
            box-shadow: var(--shadow-card);
        }}
        .search-snippet {{
            font-family: var(--font-serif);
            font-size: 0.96rem;
            color: var(--text-secondary);
            line-height: 1.55;
            margin-top: 0.5rem;
        }}

        /* Mobile & Tablet Responsive Breakpoints */
        @media (max-width: 992px) {{
            .reader-sheet {{
                padding: 1.75rem 1.5rem;
                max-width: 100%;
            }}
            .navigator-card {{
                position: static;
                margin-top: 1rem;
            }}
        }}
        @media (max-width: 640px) {{
            .block-container {{
                padding-left: 0.75rem !important;
                padding-right: 0.75rem !important;
            }}
            .library-masthead {{
                padding: 0.9rem 1.1rem;
            }}
            .masthead-title {{
                font-size: 1.18rem;
            }}
            .reader-sheet {{
                padding: 1.25rem 1.05rem;
                border-radius: 8px;
            }}
            .case-main-title {{
                font-size: 1.45rem;
            }}
            .timeline-row {{
                grid-template-columns: 1fr;
                gap: 0.2rem;
            }}
        }}

        {focus_css}
        </style>
        """,
        unsafe_allow_html=True,
    )


# --- 6. LOAD LIBRARY DATA ---
cases, bm25, tokenized_corpus = load_library()

# Ensure default selected case if available
if cases and not st.session_state.selected_lookup_key:
    st.session_state.selected_lookup_key = cases[0]["_uid"]

# Inject active CSS
inject_custom_css(
    st.session_state.theme_mode,
    st.session_state.focus_mode,
    st.session_state.font_scale,
)


# --- 7. SIDEBAR: THE FILING CABINET ---
with st.sidebar:
    st.markdown("### 🗄️ Filing Cabinet")
    st.caption("Categorized Jurisprudence Repository")

    if not cases:
        st.warning("No cases found in `case_library/master_library.jsonl`. Run the Desktop Generator to populate your library.")
    else:
        # Extract actual categories from JSONL records
        actual_categories = sorted(list({c.get("category", "Uncategorized") or "Uncategorized" for c in cases}))
        cat_counts = {
            cat: sum(1 for c in cases if (c.get("category") or "Uncategorized") == cat)
            for cat in actual_categories
        }

        cat_options = ["-- All Categories --"] + actual_categories
        if st.session_state.selected_category not in cat_options:
            st.session_state.selected_category = "-- All Categories --"

        def _format_cat(opt: str) -> str:
            if opt == "-- All Categories --":
                return f"📚 All Cases ({len(cases)})"
            return f"📁 {opt} ({cat_counts.get(opt, 0)})"

        chosen_cat = st.selectbox(
            "Browse by Syllabus Category",
            options=cat_options,
            index=cat_options.index(st.session_state.selected_category),
            format_func=_format_cat,
        )
        st.session_state.selected_category = chosen_cat

        # Filter cases inside selected category
        if chosen_cat == "-- All Categories --":
            filtered_cases = cases
        else:
            filtered_cases = [c for c in cases if (c.get("category") or "Uncategorized") == chosen_cat]

        # Quick filter inside the filing cabinet
        cabinet_filter = st.text_input(
            "Filter list by title or G.R. No.",
            placeholder="e.g., Chi Ming Tsoi or 119190",
            label_visibility="collapsed",
        )
        if cabinet_filter.strip():
            q_sub = cabinet_filter.strip().lower()
            filtered_cases = [
                c for c in filtered_cases
                if q_sub in c.get("citation", "").lower() or q_sub in c.get("lookup_key", "").lower()
            ]

        st.markdown(f"**Cases in Folder ({len(filtered_cases)})**")

        if filtered_cases:
            uid_list = [c["_uid"] for c in filtered_cases]
            uid_to_label = {
                c["_uid"]: f"📄 {c.get('citation', 'Untitled Case')}"
                for c in filtered_cases
            }
            current_idx = uid_list.index(st.session_state.selected_lookup_key) if st.session_state.selected_lookup_key in uid_list else 0

            chosen_uid = st.radio(
                "Select Case to Read",
                options=uid_list,
                index=current_idx,
                format_func=lambda u: uid_to_label.get(u, u),
                label_visibility="collapsed",
            )
            if chosen_uid != st.session_state.selected_lookup_key:
                st.session_state.selected_lookup_key = chosen_uid
                st.session_state.active_search_query = ""
                st.rerun()
        else:
            st.info("No cases match that filter.")

        st.divider()
        st.markdown("##### ⚙️ Reader Preferences")
        font_choice = st.select_slider(
            "Typography Size",
            options=["Compact", "Standard", "Comfortable"],
            value=st.session_state.font_scale,
        )
        if font_choice != st.session_state.font_scale:
            st.session_state.font_scale = font_choice
            st.rerun()


# --- 8. TOP INSTITUTIONAL MASTHEAD & CONTROLS ---
unique_cats_count = len({c.get("category", "Uncategorized") for c in cases})
st.markdown(
    f"""
    <div class="library-masthead">
        <div class="masthead-brand">
            <span class="masthead-icon">⚖️</span>
            <div>
                <div class="masthead-title">Philippine Jurisprudence Library</div>
                <div class="masthead-subtitle">Full-Text Substitute Case Digests • Doctrinal Matrix • Lexical Research Engine</div>
            </div>
        </div>
        <div class="masthead-stats">
            <span class="stat-pill">📚 {len(cases)} Indexed Cases</span>
            <span class="stat-pill">🗂️ {unique_cats_count} Syllabus Topics</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Top Control Strip: Search Bar | Study Persona Switcher | Focus Mode | Light/Dark Mode
ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([3.2, 2.2, 1.0, 1.0], gap="small")

with ctrl_col1:
    search_input = st.text_input(
        "Universal Lexical Search",
        value=st.session_state.active_search_query,
        placeholder="🔍 Search citations, G.R. numbers, doctrines, Latin maxims, or verbatim phrases...",
        label_visibility="collapsed",
    )
    if search_input != st.session_state.active_search_query:
        st.session_state.active_search_query = search_input

with ctrl_col2:
    mode_options = [
        "📖 First-Time Read",
        "⚡ Exam & Recit Review",
        "🔎 Legal Research",
    ]
    selected_mode = st.selectbox(
        "Workflow Mode",
        options=mode_options,
        index=mode_options.index(st.session_state.study_mode),
        label_visibility="collapsed",
        help="Switch layout between First-Time Deep Reading, Rapid Exam/Recit Review, and Legal Argument Research.",
    )
    if selected_mode != st.session_state.study_mode:
        st.session_state.study_mode = selected_mode
        st.rerun()

with ctrl_col3:
    focus_btn_label = "🧘 Exit Focus" if st.session_state.focus_mode else "🧘 Focus Mode"
    if st.button(focus_btn_label, use_container_width=True, help="Hide sidebars and center the legal document for distraction-free reading"):
        st.session_state.focus_mode = not st.session_state.focus_mode
        st.rerun()

with ctrl_col4:
    theme_btn_label = "☀️ Light Mode" if st.session_state.theme_mode == "Dark" else "🌙 Dark Mode"
    if st.button(theme_btn_label, use_container_width=True, help="Toggle between Warm Paper Light Mode and Low-Glare Slate Dark Mode"):
        st.session_state.theme_mode = "Dark" if st.session_state.theme_mode == "Light" else "Light"
        st.rerun()


# --- 9. BM25 LEXICAL SEARCH RESULTS VIEW ---
query_str = st.session_state.active_search_query.strip()
query_tokens = [t for t in re.split(r"\W+", query_str.lower()) if t]

if query_str and bm25 and cases:
    scores = bm25.get_scores(query_tokens)
    # Also boost exact substring matches in citation or digest
    ranked_pairs = []
    for idx, base_score in enumerate(scores):
        c_item = cases[idx]
        haystack = f"{c_item.get('citation', '')} {c_item.get('category', '')} {c_item.get('digest', '')}".lower()
        exact_boost = 5.0 if query_str.lower() in haystack else 0.0
        total_score = float(base_score) + exact_boost
        if total_score > 0:
            ranked_pairs.append((idx, total_score))

    ranked_pairs.sort(key=lambda x: x[1], reverse=True)

    res_hdr_col1, res_hdr_col2 = st.columns([4, 1])
    with res_hdr_col1:
        st.markdown(f"#### 🔎 Lexical Search Results for *'{html.escape(query_str)}'* ({len(ranked_pairs)} matching cases)")
    with res_hdr_col2:
        if st.button("✖ Clear Search", use_container_width=True):
            st.session_state.active_search_query = ""
            st.rerun()

    if not ranked_pairs:
        st.info("No exact lexical matches found. Try searching for a broader doctrinal term, G.R. number, or party surname.")
    else:
        for idx, score_val in ranked_pairs[:15]:
            match_case = cases[idx]
            snippet_html = get_search_snippet(match_case.get("digest", ""), query_tokens)
            parsed_m = parse_digest_structure(match_case.get("digest", ""))
            quote_preview = parsed_m["quotes"][0] if parsed_m["quotes"] else ""

            st.markdown(
                f"""
                <div class="search-card">
                    <div class="breadcrumb-bar">
                        <span class="cat-badge">{html.escape(match_case.get('category', 'General'))}</span>
                        <span class="gr-badge">{html.escape(match_case.get('lookup_key', ''))}</span>
                        <span class="gr-badge">BM25 Score: {score_val:.2f}</span>
                    </div>
                    <div style="font-family: var(--font-serif); font-size: 1.25rem; font-weight: 700; color: var(--accent-navy);">
                        {format_inline_markdown(match_case.get('citation', 'Untitled'), query_tokens)}
                    </div>
                    <div class="search-snippet">{snippet_html}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            btn_c1, btn_c2, _ = st.columns([1.4, 2.0, 4.6])
            with btn_c1:
                if st.button("📖 Open Case Digest", key=f"open_search_{idx}", use_container_width=True):
                    st.session_state.selected_lookup_key = match_case["_uid"]
                    st.session_state.active_search_query = ""
                    st.rerun()
            with btn_c2:
                if quote_preview:
                    with st.popover("📋 Copy Citation & Holding"):
                        st.caption("Ready to paste into legal pleadings or notes:")
                        st.code(f"{match_case.get('citation', '')}\n\n{quote_preview}", language="markdown")
            st.markdown("<div style='margin-bottom: 0.75rem;'></div>", unsafe_allow_html=True)

    st.stop()


# --- 10. MAIN CASE READER & PERSONA WORKFLOWS ---
if not cases:
    st.info("👈 Add `master_library.jsonl` to `case_library/` to browse and read your digested jurisprudence.")
    st.stop()

active_case = next(
    (c for c in cases if c["_uid"] == st.session_state.selected_lookup_key),
    cases[0],
)

digest_raw = active_case.get("digest", "")
parsed = parse_digest_structure(digest_raw)
sections = parsed["sections"]
meta = parsed["metadata"]
quotes = parsed["quotes"]
issues_list = parsed["issues"]

# Mobile / Compact Case Switcher & Section Jumper (Highly accessible on phones/tablets)
with st.expander("📱 Quick Case & Section Jumper (Mobile / Tablet Navigation)", expanded=False):
    m_col1, m_col2 = st.columns(2)
    with m_col1:
        all_uids = [c["_uid"] for c in cases]
        all_labels = {c["_uid"]: f"{c.get('citation', '')} [{c.get('category', '')}]" for c in cases}
        mob_uid = st.selectbox(
            "Switch Case",
            options=all_uids,
            index=all_uids.index(active_case["_uid"]) if active_case["_uid"] in all_uids else 0,
            format_func=lambda u: all_labels.get(u, u),
        )
        if mob_uid != active_case["_uid"]:
            st.session_state.selected_lookup_key = mob_uid
            st.rerun()
    with m_col2:
        if sections:
            toc_links_inline = " • ".join(
                f"<a href='#{s['anchor']}' style='color: var(--accent-navy); font-weight:600; text-decoration:none;'>[{html.escape(s['num'])}] {html.escape(s['short_title'][:22])}</a>"
                for s in sections
            )
            st.markdown(f"<div style='padding-top:1.6rem; font-size:0.84rem;'>{toc_links_inline}</div>", unsafe_allow_html=True)

# Layout allocation: Focus Mode (single centered column) vs Desktop 2-column (Reader + Study Navigator)
if st.session_state.focus_mode:
    reader_container = st.container()
    nav_container = None
else:
    reader_container, nav_container = st.columns([3.3, 1.15], gap="large")


# --- 10A. CENTRAL READING PANE ---
with reader_container:
    cat_label = active_case.get("category", "General Jurisprudence") or "General Jurisprudence"
    gr_label = active_case.get("lookup_key", "")
    date_cat = active_case.get("date_cataloged", "Unknown")[:10]
    src_url = active_case.get("url", "")
    source_link_html = (
        f"<a class='source-link' href='{html.escape(src_url)}' target='_blank' rel='noopener noreferrer'>🔗 View Original Supreme Court Decision ↗</a>"
        if src_url
        else "<span>📄 Offline Local Digest</span>"
    )

    # Render Mode-Specific Top Aide Banner inside the Reader
    mode_banner_html = ""
    if st.session_state.study_mode == "⚡ Exam & Recit Review":
        trigger_txt = meta.get("factual_trigger", "See Section 2 & Section 3 below for factual trigger.")
        cold_call_txt = meta.get("cold_call", "See Section 6 & Section 6.5 below for recitation synthesis.")
        magic_txt = meta.get("magic_words", "")
        outcome_txt = meta.get("outcome") or meta.get("fallo", "")

        mode_banner_html = f"""
        <div class="recit-callout" style="margin-bottom: 1.75rem;">
            <div style="font-family: var(--font-ui); font-size: 0.8rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--accent-navy); margin-bottom: 0.5rem;">
                ⚡ Rapid Recitation & Exam Review Deck
            </div>
            <p class="digest-p"><strong class="inline-lead">Factual Trigger:</strong> {format_inline_markdown(trigger_txt)}</p>
            <p class="digest-p"><strong class="inline-lead">30-Second Cold-Call Synthesis:</strong> {format_inline_markdown(cold_call_txt)}</p>
            {f'<p class="digest-p"><strong class="inline-lead">Terms of Art ("Magic Words"):</strong> {format_inline_markdown(magic_txt)}</p>' if magic_txt else ''}
            {f'<p class="digest-p" style="margin-bottom:0;"><strong class="inline-lead">Disposition:</strong> {format_inline_markdown(outcome_txt)}</p>' if outcome_txt else ''}
        </div>
        """

    elif st.session_state.study_mode == "🔎 Legal Research":
        posture_txt = meta.get("procedural_posture", "See Section 1.")
        topic_txt = meta.get("primary_topic", cat_label)
        quotes_html = "".join(
            f"<div class='quote-callout'><div class='quote-callout-label'>📜 Verbatim Doctrinal Holding #{i+1}</div><div>{format_inline_markdown(q)}</div></div>"
            for i, q in enumerate(quotes[:3])
        )
        mode_banner_html = f"""
        <div class="fallo-box" style="margin-bottom: 1.75rem;">
            <div class="fallo-title">🔎 Legal Practitioner & Argument Research Brief</div>
            <p class="digest-p"><strong class="inline-lead">Primary Doctrinal Classification:</strong> {format_inline_markdown(topic_txt)}</p>
            <p class="digest-p"><strong class="inline-lead">Procedural Vehicle:</strong> {format_inline_markdown(posture_txt)}</p>
            {quotes_html}
        </div>
        """

    # Build the continuous scrollable legal document HTML
    sections_html_list: List[str] = []
    for sec in sections:
        sec_body_html = render_section_lines_to_html(sec["lines"], sec["num"])
        sections_html_list.append(
            f"""
            <div class="digest-section-block" id="{sec['anchor']}">
                <div class="digest-section-header">
                    <span class="sec-num-badge">{html.escape(sec['num'])}</span>
                    <h3 class="sec-heading-text">{html.escape(sec['short_title'])}</h3>
                </div>
                <div class="legal-prose">
                    {sec_body_html}
                </div>
            </div>
            """
        )

    full_sections_html = "\n".join(sections_html_list)

    st.markdown(
        f"""
        <div class="reader-sheet">
            <div class="breadcrumb-bar">
                <span class="cat-badge">📁 {html.escape(cat_label)}</span>
                {f'<span class="gr-badge">{html.escape(gr_label)}</span>' if gr_label else ''}
            </div>
            <h1 class="case-main-title">{html.escape(active_case.get('citation', 'Untitled Case'))}</h1>
            <div class="case-meta-row">
                <span>🗓️ Cataloged: <strong>{html.escape(date_cat)}</strong></span>
                <span>•</span>
                {source_link_html}
            </div>
            {mode_banner_html}
            {full_sections_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


# --- 10B. RIGHT SIDEBAR: STUDY NAVIGATOR & CITATION TOOLS ---
if nav_container is not None:
    with nav_container:
        toc_items_html = "\n".join(
            f"""
            <a class="toc-link" href="#{sec['anchor']}">
                <span class="toc-num">{html.escape(sec['num'])}</span>
                <span>{html.escape(sec['short_title'][:38] + ('...' if len(sec['short_title']) > 38 else ''))}</span>
            </a>
            """
            for sec in sections
        )

        st.markdown(
            f"""
            <div class="navigator-card">
                <div class="navigator-title">🧭 On This Case</div>
                {toc_items_html if toc_items_html else '<div style="font-size:0.82rem; color:var(--text-muted);">No headings detected.</div>'}
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown("<div style='height: 1rem;'></div>", unsafe_allow_html=True)

        # Interactive Self-Test / Flashcard Accordion for Reviewers
        with st.expander("🧠 Self-Test Recitation Check", expanded=(st.session_state.study_mode == "⚡ Exam & Recit Review")):
            st.caption("Test your recall before looking at the holding:")
            if issues_list:
                st.markdown(f"**Primary Question:**\n{issues_list[0]}")
            else:
                st.markdown(f"**Case:** {active_case.get('citation', '')}")
            if st.checkbox("Reveal Cold-Call Answer & Outcome", key="reveal_recit"):
                ans = meta.get("cold_call") or meta.get("outcome") or "Refer to Section 6 and Section 6.5 in the digest."
                st.success(ans)

        # Practitioner Citation & Copy Box
        with st.expander("📋 Copy Citation & Verbatim Quotes", expanded=(st.session_state.study_mode == "🔎 Legal Research")):
            cite_block = f"{active_case.get('citation', '')}"
            if meta.get("citation_metadata"):
                cite_block += f" ({meta['citation_metadata']})"
            if quotes:
                cite_block += "\n\n" + "\n\n".join(quotes[:2])
            st.code(cite_block, language="markdown")
