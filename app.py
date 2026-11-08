import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse
import streamlit as st
from rank_bm25 import BM25Okapi

APP_DIR = Path(__file__).resolve().parent

# --- 1. PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Philippine Jurisprudence Library",
    page_icon="⚖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- 2. SESSION STATE INITIALIZATION & SAFE CALLBACKS ---
_DEFAULT_STATE = {
    "theme_mode": "Light",
    "focus_mode": False,
    "cabinet_open": True,
    "study_mode": "📖 First-Time Read",
    "font_scale": "Standard",
    "selected_lookup_key": None,
    "selected_subject": "-- All Subjects --",
    "selected_topic": "-- All Topics --",
    "active_search_query": "",
    "scroll_to_anchor": "",
}
for _state_key, _default_value in _DEFAULT_STATE.items():
    if _state_key not in st.session_state:
        st.session_state[_state_key] = _default_value


if st.session_state.theme_mode not in {"Light", "Dark"}:
    st.session_state.theme_mode = "Light"
if st.session_state.font_scale not in {"Compact", "Standard", "Comfortable"}:
    st.session_state.font_scale = "Standard"
if st.session_state.study_mode not in {
    "📖 First-Time Read", "⚡ Exam & Recit Review", "🔎 Legal Research"
}:
    st.session_state.study_mode = "📖 First-Time Read"


def _cb_select_sidebar_case():
    """Open the case selected from the persistent, clickable sidebar list."""
    chosen = st.session_state.get("_sidebar_case_radio")
    if not chosen:
        return

    st.session_state.selected_lookup_key = chosen
    st.session_state.active_search_query = ""
    st.session_state.scroll_to_anchor = "reader-top-anchor"

    # Search results are global, so selecting one also updates the folder filters.
    chosen_case = next((case for case in cases if case.get("_uid") == chosen), None)
    if chosen_case:
        st.session_state.selected_subject = chosen_case.get("subject") or "-- All Subjects --"
        st.session_state.selected_topic = chosen_case.get("topic") or "-- All Topics --"
        st.session_state["_sidebar_subj_select"] = st.session_state.selected_subject
        st.session_state["_sidebar_top_select"] = st.session_state.selected_topic


def _cb_on_subject_change():
    chosen = st.session_state.get("_sidebar_subj_select")
    if chosen:
        st.session_state.selected_subject = chosen
        st.session_state.selected_topic = "-- All Topics --"


def _cb_on_topic_change():
    chosen = st.session_state.get("_sidebar_top_select")
    if chosen:
        st.session_state.selected_topic = chosen


def _cb_toggle_cabinet():
    """Toggle the left filing cabinet using an app-owned, always-visible control."""
    st.session_state.cabinet_open = not st.session_state.get("cabinet_open", True)


def _cb_cycle_view_mode():
    """Cycle through Light → Dark → Focus Light → Focus Dark → Light."""
    sequence = [
        ("Light", False),
        ("Dark", False),
        ("Light", True),
        ("Dark", True),
    ]
    current = (st.session_state.theme_mode, st.session_state.focus_mode)
    try:
        next_index = (sequence.index(current) + 1) % len(sequence)
    except ValueError:
        next_index = 0
    st.session_state.theme_mode, st.session_state.focus_mode = sequence[next_index]


def safe_external_url(value: Any) -> str:
    """Only allow absolute HTTP(S) links in generated HTML."""
    candidate = str(value or "").strip()
    try:
        parsed_url = urlparse(candidate)
    except ValueError:
        return ""
    if parsed_url.scheme.lower() in {"http", "https"} and parsed_url.netloc:
        return candidate
    return ""


def resolve_two_tier_fields(obj: Dict[str, Any]) -> Tuple[str, str, str]:
    """Resolves (Tier 1: Subject, Tier 2: Topic, Combined Category) for both new 2-tier and legacy 1-tier records."""
    subj = (obj.get("subject") or obj.get("tier1_subject") or "").strip()
    top = (obj.get("topic") or obj.get("tier2_topic") or "").strip()
    raw_cat = (obj.get("category") or "").strip()

    if not subj or not top:
        for delim in ("|", "—", "->", "→", "::"):
            if delim in raw_cat:
                parts = [p.strip() for p in raw_cat.split(delim, 1) if p.strip()]
                if len(parts) == 2:
                    subj = subj or parts[0]
                    top = top or parts[1]
                    break
        if not subj:
            subj = "General Jurisprudence"
        if not top:
            top = raw_cat or "General Topic"

    combined = f"{subj} | {top}" if subj.lower() != top.lower() else subj
    return subj, top, combined


# --- 3. DATA LOADING & BM25 LEXICAL INDEXING ---
@st.cache_data(show_spinner=False)
def load_library() -> Tuple[List[Dict[str, Any]], Optional[BM25Okapi], List[List[str]]]:
    """Loads all records from case_library/master_library.jsonl and builds BM25 index with 2-Tier metadata."""
    candidates = [
        APP_DIR / "case_library" / "master_library.jsonl",
        APP_DIR / "master_library.jsonl",
        Path.cwd() / "case_library" / "master_library.jsonl",
        Path.cwd() / "master_library.jsonl",
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
                    subj, top, combined_cat = resolve_two_tier_fields(obj)
                    obj["subject"] = subj
                    obj["topic"] = top
                    obj["category"] = combined_cat
                    obj["_uid"] = f"{obj.get('lookup_key', idx)}::{subj}::{top}::{idx}"
                    cases.append(obj)
                except Exception:
                    continue

    tokenized_corpus: List[List[str]] = []
    for c in cases:
        raw_blob = (
            f"{c.get('citation', '')} {c.get('lookup_key', '')} "
            f"{c.get('subject', '')} {c.get('topic', '')} {c.get('digest', '')}"
        ).lower()
        tokens = [t for t in re.split(r"\W+", raw_blob) if t]
        tokenized_corpus.append(tokens)

    bm25 = BM25Okapi(tokenized_corpus) if tokenized_corpus else None
    return cases, bm25, tokenized_corpus


# --- 4. MARKDOWN DIGEST PARSER & UNIVERSAL SECTION CLASSIFIER (v1-v8 Compatible) ---
CANONICAL_SEC_KEYWORDS = {
    "1": ("case header", "cast of characters", "procedural posture", "case title", "caption"),
    "2": ("syllabus topic", "codal provision", "factual trigger", "nature of the action", "nature of action"),
    "3": ("chronological facts", "facts storyboard", "factual background", "facts", "antecedent facts", "statement of facts"),
    "4": ("party arguments", "arguments matrix", "exhaustive party", "issues", "statement of issues"),
    "5": ("inventory of issues", "complete inventory", "issues found", "ruling", "ratio decidendi", "held"),
    "6": ("deep-dive verdict", "ratio decidendi", "verdict & ratio", "doctrinal takeaway", "doctrine", "principle"),
    "6.5": ("subject-specific", "doctrinal & recitation", "recitation spotlight"),
    "7": ("separate opinions", "overall verdict", "fallo", "dispositive"),
}

MAJOR_SEC_CANDIDATE_RE = re.compile(
    r"^(?:#{1,4}\s*)?(?:\*\*)?([1-7](?:\.5)?|[IVXLCDM]{1,6})[.)]?\s+(.+?)(?:\*\*)?\s*$"
)

STANDALONE_HEADING_RE = re.compile(
    r"^(?:#{1,4}\s+)(?:\*\*)?(.+?)(?:\*\*)?\s*$|"
    r"^(?:\*\*)?(CASE\s+TITLE\s*&\s*CAPTION|NATURE\s+OF\s+THE\s+ACTION|STATEMENT\s+OF\s+FACTS|"
    r"ANTECEDENT\s+FACTS|CHRONOLOGICAL\s+FACTS|FACTS|ISSUES|STATEMENT\s+OF\s+ISSUES|"
    r"RULING\s*&\s*RATIO\s+DECIDENDI|RULING|RATIO\s+DECIDENDI|"
    r"DOCTRINAL\s+TAKEAWAY(?:\s*/\s*PRINCIPLE)?|DOCTRINE|FALLO|DISPOSITIVE\s+PORTION)(?:\*\*)?\s*:?\s*$",
    re.IGNORECASE,
)

INLINE_LABEL_PATTERN = re.compile(
    r"^\s*(?:[-•*]\s*)?(?:\*\*)?("
    r"Concise Recognizable Title|Full Official Caption|Citation & Metadata|"
    r"Key Parties & Roles(?:\s*\(The Cast\))?|How the Case Reached the Supreme Court|"
    r"Primary Topic Hierarchy|Secondary Topic Hierarchy(?:\s*\(if any\))?|"
    r"Full Text of Governing[^:*]*|Historical Landmark Rule|"
    r"Factual Trigger(?:\s*\(Application to the Story\))?|"
    r"Main Thesis|Specific Contentions|Recitation-Ready Question|"
    r"Assigned Course & Syllabus Focus|Doctrinal Requisites[^:*]*|"
    r"Subject-Specific Terms of Art[^:*]*|Cold-Call Synthesis[^:*]*|"
    r"Separate Opinions Summary|Verbatim/Near-Verbatim Fallo|Plain-Language Outcome|"
    r"Title|Case Title|Court|Date Promulgated|Ponente|Vote|Voting|Held|Doctrine"
    r")(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.*)$",
    re.IGNORECASE,
)


def is_canonical_major_section(sec_num: str, sec_title: str, raw_line: str) -> bool:
    t_lower = sec_title.lower()
    if t_lower.startswith("whether ") or t_lower.startswith("[primary") or t_lower.startswith("[procedural"):
        return False
    expected = CANONICAL_SEC_KEYWORDS.get(sec_num, ())
    if any(k in t_lower for k in expected):
        return True
    if re.match(r"^[IVXLCDM]+$", sec_num, re.IGNORECASE) and len(sec_title) < 95:
        return True
    if raw_line.strip().startswith("#") and len(sec_title) < 100:
        return True
    return False


def slugify(text: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return clean or "section"


def clean_stray_asterisks(text: str) -> str:
    s = text.strip()
    s = re.sub(r"^\*+\s*", "", s)
    s = re.sub(r"\s*\*+$", "", s)
    if s.count("**") % 2 == 1:
        s = s.replace("**", "")
    return s.strip()


HIDDEN_AUDIO_SECTION_RE = re.compile(
    r"^(?:#{1,4}\s*)?(?:\*\*)?(?:(?:8[.)]\s*|Section\s+8\s*[:.-]?\s*)?(?:Hidden\s+Audio|Audio\s+Synthesis|Audio\s+Briefing)|Spoken\s+Relevant\s+Law\s+Summary\s*:).*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_hidden_audio_section(digest_text: str) -> str:
    """Removes Section 8 (Hidden Audio Synthesis Metadata) so it never appears in printed DOCX casebooks or visual reader panes."""
    if not digest_text:
        return ""
    m = HIDDEN_AUDIO_SECTION_RE.search(digest_text)
    if m:
        return digest_text[: m.start()].rstrip()
    return digest_text


def parse_digest_structure(digest_text: str) -> Dict[str, Any]:
    printable_digest = strip_hidden_audio_section(digest_text)
    lines = printable_digest.splitlines()
    sections: List[Dict[str, Any]] = []
    metadata_map: Dict[str, str] = {}
    quotes: List[str] = []
    issues: List[str] = []

    current_sec: Optional[Dict[str, Any]] = None
    auto_sec_counter = 1

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or re.fullmatch(r"(?:-{3,}|—{3,}|–{3,})", stripped):
            if current_sec is not None:
                current_sec["lines"].append("")
            continue

        heading_candidate = re.sub(r"^#{1,4}\s*", "", stripped)
        heading_candidate_clean = re.sub(r"^\*\*|\*\*$", "", heading_candidate).strip()
        m_sec = MAJOR_SEC_CANDIDATE_RE.match(heading_candidate_clean)

        if m_sec and is_canonical_major_section(m_sec.group(1), m_sec.group(2), stripped):
            sec_num = m_sec.group(1).upper()
            sec_title = clean_stray_asterisks(m_sec.group(2))
            anchor_id = f"sec-{sec_num.replace('.', '-')}-{slugify(sec_title)[:28]}-{len(sections)}"
            current_sec = {
                "num": sec_num,
                "title": f"{sec_num}. {sec_title}",
                "short_title": sec_title,
                "anchor": anchor_id,
                "lines": [],
            }
            sections.append(current_sec)
            continue

        m_standalone = STANDALONE_HEADING_RE.match(stripped)
        if m_standalone:
            raw_h = m_standalone.group(1) or m_standalone.group(2) or ""
            sec_title = clean_stray_asterisks(raw_h.rstrip(":"))
            if sec_title and len(sec_title) < 95:
                sec_num = str(auto_sec_counter)
                auto_sec_counter += 1
                anchor_id = f"sec-{sec_num}-{slugify(sec_title)[:28]}-{len(sections)}"
                current_sec = {
                    "num": sec_num,
                    "title": sec_title,
                    "short_title": sec_title,
                    "anchor": anchor_id,
                    "lines": [],
                }
                sections.append(current_sec)
                continue

        if current_sec is None:
            current_sec = {
                "num": "1",
                "title": "1. Case Overview",
                "short_title": "Case Overview",
                "anchor": "sec-1-overview",
                "lines": [],
            }
            sections.append(current_sec)

        current_sec["lines"].append(raw_line)

        m_label = INLINE_LABEL_PATTERN.match(stripped)
        if m_label:
            k_raw = m_label.group(1).strip().lower()
            v_raw = clean_stray_asterisks(m_label.group(2))
            if "concise recognizable title" in k_raw or k_raw in ("title", "case title"):
                metadata_map.setdefault("concise_title", v_raw)
            elif "full official caption" in k_raw:
                metadata_map["official_caption"] = v_raw
            elif "citation & metadata" in k_raw or k_raw in ("court", "ponente"):
                metadata_map["citation_metadata"] = (
                    f"{metadata_map['citation_metadata']} | {v_raw}"
                    if "citation_metadata" in metadata_map
                    else v_raw
                )
            elif "key parties" in k_raw:
                metadata_map["key_parties"] = v_raw
            elif "how the case reached" in k_raw:
                metadata_map["procedural_posture"] = v_raw
            elif "primary topic hierarchy" in k_raw:
                metadata_map["primary_topic"] = v_raw
            elif "factual trigger" in k_raw and v_raw:
                metadata_map["factual_trigger"] = v_raw
            elif ("cold-call synthesis" in k_raw or k_raw == "doctrine") and v_raw:
                metadata_map.setdefault("cold_call", v_raw)
            elif "subject-specific terms" in k_raw and v_raw:
                metadata_map["magic_words"] = v_raw
            elif "doctrinal requisites" in k_raw and v_raw:
                metadata_map["doctrinal_requisites"] = v_raw
            elif "verbatim/near-verbatim fallo" in k_raw and v_raw:
                metadata_map["fallo"] = v_raw
            elif ("plain-language outcome" in k_raw or k_raw == "held") and v_raw:
                metadata_map.setdefault("outcome", v_raw)

        sec_title_lower = current_sec["short_title"].lower()
        if current_sec["num"] == "5" or "issue" in sec_title_lower:
            if re.match(r"^\d+[.)]\s+", stripped) or "whether or not" in stripped.lower() or stripped.endswith("?"):
                issues.append(clean_stray_asterisks(stripped))

        if "golden verbatim quote" in stripped.lower() or stripped.startswith(">"):
            q_clean = re.sub(r"^>\s*", "", stripped)
            q_clean = re.sub(
                r"^(?:\*\*)?\(?c\)?\s*Golden Verbatim Quote(?:s)?(?:\*\*)?\s*:\s*(?:\*\*)?\s*",
                "",
                q_clean,
                flags=re.IGNORECASE,
            )
            q_clean = clean_stray_asterisks(q_clean)
            if len(q_clean) > 25:
                quotes.append(q_clean)

    return {
        "sections": sections,
        "metadata": metadata_map,
        "quotes": quotes,
        "issues": issues,
    }


def format_inline_markdown(text: str, highlight_terms: Optional[List[str]] = None) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    cleaned = re.sub(r"^:\s*\*\*\s*", "", cleaned)
    cleaned = re.sub(r"^\*\*\s+", "", cleaned)
    cleaned = re.sub(r"\s+\*\*$", "", cleaned)

    escaped = html.escape(cleaned)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = escaped.replace("**", "")
    escaped = re.sub(r"(?<!\*)\*([^\*\n]+?)\*(?!\*)", r"<em>\1</em>", escaped)

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
    html_parts: List[str] = []
    for raw in lines:
        s = raw.strip()
        if not s:
            continue

        if s.startswith("###") or s.startswith("####"):
            sub_h = clean_stray_asterisks(s.lstrip("#").strip())
            html_parts.append(f"<h4 class='digest-subheading'>{format_inline_markdown(sub_h, highlight_terms)}</h4>")
            continue

        if re.match(r"^(?:\*\*)?[A-Z]\.[\s]+(?:Petitioner|Respondent|Public|Private|OSG|Accused|Defense)", s):
            clean_sub = clean_stray_asterisks(s)
            html_parts.append(f"<div class='party-header'>{format_inline_markdown(clean_sub, highlight_terms)}</div>")
            continue

        m_sub = re.match(
            r"^(?:\*\*)?(\([a-d]\)\s*[^:*]+)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(.*)$",
            s,
            re.IGNORECASE,
        )
        if m_sub:
            sub_label = clean_stray_asterisks(m_sub.group(1))
            sub_rest = clean_stray_asterisks(m_sub.group(2))
            lower_lbl = sub_label.lower()
            if "(c)" in lower_lbl or "verbatim quote" in lower_lbl:
                html_parts.append(
                    f"<div class='quote-callout'><div class='quote-callout-label'>📜 {html.escape(sub_label)}</div><div class='quote-callout-body'>{format_inline_markdown(sub_rest, highlight_terms)}</div></div>"
                )
            elif "(d)" in lower_lbl or "recit-ready" in lower_lbl or "plain-language" in lower_lbl:
                html_parts.append(
                    f"<div class='recit-callout'><span class='recit-label'>🎯 {html.escape(sub_label)}:</span> {format_inline_markdown(sub_rest, highlight_terms)}</div>"
                )
            elif "(a)" in lower_lbl:
                html_parts.append(
                    f"<div class='issue-verdict-banner'><span class='verdict-tag'>{html.escape(sub_label)}:</span> {format_inline_markdown(sub_rest, highlight_terms)}</div>"
                )
            else:
                html_parts.append(
                    f"<p class='digest-p'><strong class='inline-lead'>{html.escape(sub_label)}:</strong> {format_inline_markdown(sub_rest, highlight_terms)}</p>"
                )
            continue

        if s.startswith(">"):
            q_body = clean_stray_asterisks(s.lstrip(">").strip())
            html_parts.append(f"<blockquote class='legal-blockquote'>{format_inline_markdown(q_body, highlight_terms)}</blockquote>")
            continue

        if sec_num == "3" and s.startswith("**"):
            m_time = re.match(r"^\*\*([^*:]+?)(?::)?\*\*\s*:?\s*(.*)$", s)
            if m_time:
                dt_label = clean_stray_asterisks(m_time.group(1).strip(": "))
                ev_body = clean_stray_asterisks(m_time.group(2))
                html_parts.append(
                    f"<div class='timeline-row'><div class='timeline-date'>{html.escape(dt_label)}</div><div class='timeline-event'>{format_inline_markdown(ev_body, highlight_terms)}</div></div>"
                )
                continue

        m_inline = INLINE_LABEL_PATTERN.match(s)
        if m_inline:
            lbl = clean_stray_asterisks(m_inline.group(1))
            val = clean_stray_asterisks(m_inline.group(2))
            if "factual trigger" in lbl.lower():
                html_parts.append(
                    f"<div class='trigger-box'><div class='trigger-title'>⚡ {html.escape(lbl)}</div><div class='trigger-text'>{format_inline_markdown(val, highlight_terms)}</div></div>"
                )
            elif "verbatim/near-verbatim fallo" in lbl.lower():
                html_parts.append(
                    f"<div class='fallo-box'><div class='fallo-title'>⚖️ {html.escape(lbl)}</div><div class='fallo-text'>{format_inline_markdown(val, highlight_terms)}</div></div>"
                )
            else:
                html_parts.append(
                    f"<p class='digest-p'><strong class='inline-lead'>{html.escape(lbl)}:</strong> {format_inline_markdown(val, highlight_terms)}</p>"
                )
            continue

        m_generic_bold_label = re.match(r"^\*\*([^\*:\n]{2,65}?)(?::)?\*\*\s*:?\s+(.+)$", s)
        if m_generic_bold_label:
            g_lbl = clean_stray_asterisks(m_generic_bold_label.group(1).rstrip(":"))
            g_val = clean_stray_asterisks(m_generic_bold_label.group(2))
            html_parts.append(
                f"<p class='digest-p'><strong class='inline-lead'>{html.escape(g_lbl)}:</strong> {format_inline_markdown(g_val, highlight_terms)}</p>"
            )
            continue

        m_num = re.match(r"^(?:\*\*)?(\d{1,2}[.)])(?:\*\*)?\s+(.+)$", s)
        if m_num:
            num_badge = m_num.group(1)
            rest_txt = clean_stray_asterisks(m_num.group(2))
            html_parts.append(
                f"<div class='numbered-item'><span class='num-circle'>{html.escape(num_badge)}</span><div class='num-content'>{format_inline_markdown(rest_txt, highlight_terms)}</div></div>"
            )
            continue

        html_parts.append(f"<p class='digest-p'>{format_inline_markdown(s, highlight_terms)}</p>")

    return "".join(html_parts)


def get_search_snippet(text: str, query_tokens: List[str], window: int = 220) -> str:
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



def rank_case_matches(query: str, library_cases: List[Dict[str, Any]], search_index: Optional[BM25Okapi]) -> List[Tuple[int, float]]:
    """Return all matching case indexes in the same BM25 order used by the results page."""
    query = (query or "").strip()
    if not query or not search_index or not library_cases:
        return []

    tokens = [token for token in re.split(r"\W+", query.lower()) if token]
    if not tokens:
        return []

    scores = search_index.get_scores(tokens)
    ranked: List[Tuple[int, float]] = []
    query_lower = query.lower()
    for idx, base_score in enumerate(scores):
        case = library_cases[idx]
        haystack = (
            f"{case.get('citation', '')} {case.get('lookup_key', '')} "
            f"{case.get('subject', '')} {case.get('topic', '')} {case.get('digest', '')}"
        ).lower()
        score = float(base_score) + (5.0 if query_lower in haystack else 0.0)
        if score > 0:
            ranked.append((idx, score))

    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked



# --- 4B. AUTOMATED AUDIO CASE BRIEFING ENGINE (Edge Neural TTS: en-US-AndrewMultilingualNeural) ---
DEFAULT_AUDIO_VOICE = "en-US-AndrewMultilingualNeural"
AUDIO_DIR = APP_DIR / "case_library" / "audio_briefs"


def clean_text_for_speech(text: str) -> str:
    """Cleans Markdown formatting and expands Philippine legal abbreviations for natural spoken delivery."""
    if not text:
        return ""
    s = text.strip()
    s = re.sub(r"^#{1,6}\s*", "", s, flags=re.MULTILINE)
    s = re.sub(
        r"\[(PRIMARY SYLLABUS ISSUE|Procedural\s*/\s*Secondary Issue|SECONDARY ISSUE|PROCEDURAL ISSUE)\]",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s = s.replace("**", "").replace("*", "").replace("`", "")
    s = re.sub(r"^>\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"→|->|⟶", ", leading to ", s)

    s = re.sub(r"\bv\.?\s+", " versus ", s)
    s = re.sub(r"\bvs\.?\s+", " versus ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bG\.?\s*R\.?\s*Nos?\.?\s*", "G.R. Number ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bArt\.\s*", "Article ", s)
    s = re.sub(r"\bArts\.\s*", "Articles ", s)
    s = re.sub(r"\bSec\.\s*", "Section ", s)
    s = re.sub(r"\bSecs\.\s*", "Sections ", s)
    s = re.sub(r"\bPar\.\s*", "Paragraph ", s)
    s = re.sub(r"\bNo\.\s*(\d)", r"Number \1", s)
    s = re.sub(r"\bC\.?\s*J\.?\b", "Chief Justice", s)
    s = re.sub(r",\s*J\.?\b", ", Justice", s)
    s = re.sub(r"\bRTC\b", "Regional Trial Court", s)
    s = re.sub(r"\bCA\b", "Court of Appeals", s)
    s = re.sub(r"\bOSG\b", "Office of the Solicitor General", s)
    s = re.sub(r"\bCOA\b", "Commission on Audit", s)
    s = re.sub(r"\bCOMELEC\b", "Commission on Elections", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _strip_md_and_bullets(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^(?:[-•●▪]|\*(?!\*))\s+", "", s)
    s = s.replace("**", "")
    s = re.sub(r"^\*+|\*+$", "", s).strip()
    return s


def get_audio_file_paths(case_entry: Dict[str, Any]) -> Tuple[Path, Path]:
    """Returns deterministic (primer_mp3_path, chapter_mp3_path) inside case_library/audio_briefs/."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    raw_key = case_entry.get("lookup_key") or "case"
    key = re.sub(r"[^A-Za-z0-9_-]", "", str(raw_key))[:28] or "case"
    subj = re.sub(r"[^A-Za-z0-9]", "", str(case_entry.get("subject") or "gen"))[:18].lower() or "gen"
    top = re.sub(r"[^A-Za-z0-9]", "", str(case_entry.get("topic") or "top"))[:18].lower() or "top"
    stem = f"{key}_{subj}_{top}".strip("_")

    p_custom = case_entry.get("audio_primer")
    c_custom = case_entry.get("audio_chapter")

    def _resolve_custom_audio(raw_path: Any) -> Optional[Path]:
        if not raw_path:
            return None
        candidate = Path(str(raw_path)).expanduser()
        if not candidate.is_absolute():
            candidate = APP_DIR / candidate
        return candidate if candidate.is_file() else None

    p_path = _resolve_custom_audio(p_custom) or (AUDIO_DIR / f"{stem}_primer.mp3")
    c_path = _resolve_custom_audio(c_custom) or (AUDIO_DIR / f"{stem}_chapter.mp3")
    return p_path, c_path


def apply_phonetic_pronunciations(text: str, phonetic_guide: str) -> str:
    """Applies semicolon-separated 'Term = Phonetic' replacements from Section 8 before TTS synthesis."""
    if not text or not phonetic_guide or phonetic_guide.strip().lower() in ("none", "n/a", "not expressly stated in the decision."):
        return text
    out = text
    for pair in re.split(r"[;\n]+", phonetic_guide):
        if "=" in pair:
            term, phon = pair.split("=", 1)
            term = term.strip(" `*\"'")
            phon = phon.strip(" `*\"'")
            if len(term) >= 3 and phon:
                out = re.sub(rf"\b{re.escape(term)}\b", phon, out, flags=re.IGNORECASE)
    return out


def build_audio_scripts_for_case(case_entry: Dict[str, Any]) -> Tuple[str, str]:
    """
    Builds two spoken scripts for Microsoft Edge Neural TTS (en-US-AndrewMultilingualNeural)
    following the 9-Step (Cold-Call Primer) and 13-Step (Complete Audio Chapter) architecture:
      Steps 1-9 (Shared by Primer & Chapter):
        1. Introduction of Subject (Tier 1), Topic (Tier 2), and Relevant Law for Primary Syllabus Issue(s)
        2. Factual Trigger related to Primary Syllabus Issue(s)
        3. How the Factual Trigger is related to the Primary Syllabus Issue(s)
        4. Explanation of the Primary Syllabus Issue(s)
        5. Arguments of Petitioner and Respondent in the context of the Primary Syllabus Issue(s)
        6. Ruling of the Court related to the Primary Syllabus Issue(s)
        7. Synthesis of resolving the Primary Syllabus Issue(s) via Step-by-Step Ratio Decidendi
        8. Doctrine Identifier relating to the final caselaw rule established/applied by the Court
        9. Final Outcome of the case in plain language
      Steps 10-13 (Chapter Only, after explicit spoken transition):
        - Audio Transition: States that the main topic of contention is resolved and introduces remaining case details
        10. All other related issues in the case not yet discussed (plus full chronological storyboard context)
        11. Final Disposition (Fallo)
        12. Separate Opinions (if any)
        13. Final Review of the main reckoning point of the case
    """
    citation = case_entry.get("citation", "Untitled Case")
    subject = case_entry.get("subject") or "General Jurisprudence"
    topic = case_entry.get("topic") or case_entry.get("category") or "General Topic"
    digest = case_entry.get("digest", "")

    fields: Dict[str, str] = {}
    timeline_events: List[str] = []
    issues_raw: List[Dict[str, Any]] = []
    issue_blocks: List[Dict[str, str]] = []
    pet_args: List[str] = []
    resp_args: List[str] = []
    spotlight_extra: List[str] = []

    current_sec = "0"
    current_party = ""
    current_issue_block: Dict[str, str] = {}

    sec_re = re.compile(
        r"^(?:#{1,4}\s*)?(?:\*\*)?([1-8](?:\.5)?|[IVXLCDM]{1,6})[.)]?\s+(.+?)(?:\*\*)?\s*$",
        re.IGNORECASE,
    )

    for raw_line in digest.splitlines():
        s = raw_line.strip()
        if not s or re.fullmatch(r"(?:-{3,}|—{3,}|–{3,})", s):
            continue

        s_no_hash = re.sub(r"^#{1,4}\s*", "", s).strip("* ").strip()
        m_sec = sec_re.match(s_no_hash)
        if m_sec and len(s_no_hash) < 120 and not s_no_hash.lower().startswith("whether "):
            sec_id = m_sec.group(1).upper()
            sec_title_low = m_sec.group(2).lower()
            if sec_id == "8" or "hidden audio" in sec_title_low or "socratic bridge" in sec_title_low:
                current_sec = "8"
                continue
            elif "6.5" in sec_id or "spotlight" in sec_title_low or "subject-specific" in sec_title_low:
                current_sec = "6.5"
                continue
            elif "header" in sec_title_low or "caption" in sec_title_low or sec_id in ("1", "I"):
                current_sec = "1"
                continue
            elif "syllabus" in sec_title_low or "trigger" in sec_title_low or "nature" in sec_title_low or sec_id in ("2", "II"):
                current_sec = "2"
                continue
            elif "chronological" in sec_title_low or "storyboard" in sec_title_low or "facts" in sec_title_low or sec_id in ("3", "III"):
                current_sec = "3"
                continue
            elif "argument" in sec_title_low or "party" in sec_title_low:
                current_sec = "4"
                continue
            elif "inventory of issues" in sec_title_low or "issues" in sec_title_low:
                current_sec = "5"
                continue
            elif "verdict & ratio" in sec_title_low or "deep-dive" in sec_title_low or "ruling" in sec_title_low or "ratio decidendi" in sec_title_low:
                current_sec = "6"
                continue
            elif "separate opinion" in sec_title_low or "fallo" in sec_title_low or "doctrinal takeaway" in sec_title_low or sec_id in ("7", "VII"):
                current_sec = "7"
                continue

        s_unbold = _strip_md_and_bullets(s)
        low = s_unbold.lower()

        # Flexible Parsing for Standard Sections & Section 8
        if re.match(r"^(?:concise\s+)?(?:recognizable\s+)?title\s*:", low):
            fields["concise_title"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^(?:full\s+)?(?:official\s+)?caption\s*:", low):
            fields["official_caption"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^citation(?:\s*&\s*metadata)?\s*:", low):
            fields["citation_metadata"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^key\s+parties(?:\s*&\s*roles)?", low):
            fields["key_parties"] = s_unbold.split(":", 1)[1].strip() if ":" in s_unbold else s_unbold
            continue
        elif low.startswith("how the case reached"):
            fields["posture"] = s_unbold.split(":", 1)[1].strip() if ":" in s_unbold else s_unbold
            continue
        elif re.match(r"^primary\s+topic(?:\s+hierarchy)?\s*:", low):
            fields["primary_hierarchy"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^secondary\s+topic(?:\s+hierarchy)?\s*:", low):
            fields["secondary_hierarchy"] = s_unbold.split(":", 1)[1].strip() if ":" in s_unbold else ""
            continue
        elif re.match(r"^full\s+text\s+of\s+governing", low):
            fields["governing_law"] = s_unbold.split(":", 1)[1].strip() if ":" in s_unbold else ""
            continue
        elif re.match(r"^historical\s+landmark\s+rule\s*:", low):
            fields["historical_rule"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^factual\s+trigger", low):
            fields["factual_trigger"] = s_unbold.split(":", 1)[1].strip() if ":" in s_unbold else s_unbold
            continue
        elif re.match(r"^doctrinal\s+requisites", low):
            fields["requisites"] = s_unbold.split(":", 1)[1].strip() if ":" in s_unbold else s_unbold
            continue
        elif re.match(r"^subject-specific\s+terms", low):
            fields["magic_words"] = s_unbold.split(":", 1)[1].strip() if ":" in s_unbold else s_unbold
            continue
        elif re.match(r"^(?:thirty-second\s+)?cold-call\s+synthesis", low):
            fields["cold_call"] = s_unbold.split(":", 1)[1].strip() if ":" in s_unbold else s_unbold
            continue
        elif re.match(r"^separate\s+opinions(?:\s+summary)?\s*:", low):
            fields["separate_opinions"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^(?:verbatim(?:/near-verbatim)?\s+)?fallo\s*:", low):
            fields["fallo"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^(?:plain-?language\s+)?outcome\s*:", low):
            fields["outcome"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^spoken\s+relevant\s+law(?:\s+summary)?\s*:", low):
            fields["spoken_law_summary"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^trigger-to-issue\s+bridge\s*:", low):
            fields["trigger_bridge"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^primary\s+issue\s+spoken\s+explanation\s*:", low):
            fields["primary_issue_explanation"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^primary\s+issue\s+arguments\s+summary\s*:", low):
            fields["primary_args_summary"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^doctrine\s+identifier", low):
            fields["doctrine_identifier"] = s_unbold.split(":", 1)[1].strip() if ":" in s_unbold else s_unbold
            continue
        elif re.match(r"^chapter\s+transition\s+bridge\s*:", low):
            fields["chapter_transition"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^final\s+reckoning\s+point\s*:", low):
            fields["final_reckoning"] = s_unbold.split(":", 1)[1].strip()
            continue
        elif re.match(r"^phonetic\s+pronunciation\s+guide\s*:", low):
            fields["phonetic_guide"] = s_unbold.split(":", 1)[1].strip()
            continue

        if current_sec == "2" and not fields.get("governing_law") and not low.startswith("assigned"):
            fields["governing_law"] = (fields.get("governing_law", "") + " " + s_unbold).strip()
        elif current_sec == "3":
            timeline_events.append(s_unbold)
        elif current_sec == "4":
            if re.match(r"^A\.\s*Petitioner", s_unbold, re.IGNORECASE):
                current_party = "P"
            elif re.match(r"^B\.\s*Respondent", s_unbold, re.IGNORECASE):
                current_party = "R"
            elif current_party == "P":
                pet_args.append(s_unbold)
            elif current_party == "R":
                resp_args.append(s_unbold)
        elif current_sec == "5":
            if low.startswith("recitation-ready question:"):
                q_txt = s_unbold.split(":", 1)[1].strip()
                if issues_raw:
                    issues_raw[-1]["recit_q"] = q_txt
                else:
                    issues_raw.append({"text": q_txt, "recit_q": q_txt, "is_primary": True})
            else:
                is_sec_tag = bool(re.search(r"\[\s*(?:Procedural|Secondary)", s, re.IGNORECASE))
                is_prim_tag = bool(re.search(r"\[\s*PRIMARY\s+SYLLABUS\s+ISSUE\s*\]", s, re.IGNORECASE))
                issues_raw.append({
                    "text": s_unbold,
                    "recit_q": "",
                    "is_primary": True if is_prim_tag else (False if is_sec_tag else (len(issues_raw) == 0)),
                })
        elif current_sec == "6":
            m_sub = re.match(r"^\(?([a-d])\)?\s*[^:]{2,55}:\s*(.*)$", s_unbold, re.IGNORECASE)
            if m_sub:
                sub_letter = m_sub.group(1).lower()
                sub_val = m_sub.group(2).strip()
                if sub_letter == "a":
                    if current_issue_block:
                        issue_blocks.append(current_issue_block)
                    current_issue_block = {"a": sub_val}
                elif sub_letter == "b":
                    current_issue_block["b"] = sub_val
                elif sub_letter == "c":
                    current_issue_block["c"] = sub_val
                elif sub_letter == "d":
                    current_issue_block["d"] = sub_val
            else:
                if "d" in current_issue_block:
                    current_issue_block["d"] += " " + s_unbold
                elif "c" in current_issue_block and "d" not in current_issue_block:
                    current_issue_block["c"] += " " + s_unbold
                elif "b" in current_issue_block:
                    current_issue_block["b"] += " " + s_unbold
                elif "a" in current_issue_block:
                    current_issue_block["a"] += " " + s_unbold
        elif current_sec == "6.5":
            if not low.startswith("assigned course"):
                spotlight_extra.append(s_unbold)

    if current_issue_block:
        issue_blocks.append(current_issue_block)

    # Pair issues from Section 5 with their verdict blocks from Section 6
    max_issues = max(len(issues_raw), len(issue_blocks))
    paired_issues: List[Dict[str, Any]] = []
    for idx in range(max_issues):
        iss_meta = issues_raw[idx] if idx < len(issues_raw) else {
            "text": issue_blocks[idx].get("a", f"Issue {idx + 1}"),
            "recit_q": "",
            "is_primary": (idx == 0),
        }
        blk = issue_blocks[idx] if idx < len(issue_blocks) else {}
        a_str = blk.get("a", "")
        if re.search(r"\[\s*PRIMARY\s+SYLLABUS\s+ISSUE\s*\]", a_str, re.IGNORECASE):
            iss_meta["is_primary"] = True
        elif re.search(r"\[\s*(?:Procedural|Secondary)", a_str, re.IGNORECASE) and idx > 0:
            iss_meta["is_primary"] = False
        paired_issues.append({
            "index": idx + 1,
            "is_primary": iss_meta["is_primary"],
            "question": iss_meta.get("text", ""),
            "recit_q": iss_meta.get("recit_q", ""),
            "a": blk.get("a", ""),
            "b": blk.get("b", ""),
            "c": blk.get("c", ""),
            "d": blk.get("d", ""),
        })

    primary_list = [p for p in paired_issues if p["is_primary"]]
    if not primary_list and paired_issues:
        primary_list = [paired_issues[0]]
        paired_issues[0]["is_primary"] = True
    secondary_list = [p for p in paired_issues if not p["is_primary"]]

    # --- BUILD SHARED CORE ELEMENTS (STEPS 1 TO 9) ---
    title_spoken = clean_text_for_speech(fields.get("concise_title") or citation)
    subj_spoken = clean_text_for_speech(subject)
    top_spoken = clean_text_for_speech(topic)
    hierarchy_spoken = clean_text_for_speech(fields.get("primary_hierarchy", ""))
    gov_law_full = clean_text_for_speech(fields.get("governing_law", ""))
    spoken_law_summary = clean_text_for_speech(
        fields.get("spoken_law_summary")
        or (gov_law_full[:380] if gov_law_full else hierarchy_spoken or f"Philippine jurisprudence under {subj_spoken}, {top_spoken}.")
    )

    # Step 2: Factual Trigger
    trigger_spoken = clean_text_for_speech(
        fields.get("factual_trigger")
        or (" ".join(timeline_events[:2]) if timeline_events else "See the operative facts of the decision.")
    )

    # Step 4: Primary Issue(s) & Explanation
    prim_questions_spoken = []
    for p in primary_list:
        q_base = clean_text_for_speech(p["recit_q"] or p["question"] or p["a"])
        if q_base:
            prim_questions_spoken.append(q_base)
    prim_issue_question_str = " ".join(prim_questions_spoken) or "Whether the challenged act or ruling was valid under the governing law."
    prim_issue_expl_spoken = clean_text_for_speech(fields.get("primary_issue_explanation", ""))

    # Step 3: How the Factual Trigger is related to the Primary Syllabus Issue(s)
    if fields.get("trigger_bridge"):
        trigger_bridge_spoken = clean_text_for_speech(fields["trigger_bridge"])
    else:
        posture_sub = clean_text_for_speech(fields.get("posture", ""))
        if posture_sub:
            trigger_bridge_spoken = (
                f"This factual trigger led directly to the conflict before the Supreme Court via {posture_sub}, "
                f"forcing the Court to resolve the primary syllabus question of {prim_issue_question_str}"
            )
        else:
            trigger_bridge_spoken = (
                f"Because of this factual trigger, the controversy squarely placed before the Supreme Court the primary syllabus issue of {prim_issue_question_str}"
            )

    # Step 5: Arguments of Petitioner & Respondent in context of Primary Syllabus Issue(s)
    pet_full_spoken = " ".join(clean_text_for_speech(x) for x in pet_args if "not expressly stated" not in x.lower())
    resp_full_spoken = " ".join(clean_text_for_speech(x) for x in resp_args if "not expressly stated" not in x.lower())
    if fields.get("primary_args_summary"):
        args_primer_spoken = clean_text_for_speech(fields["primary_args_summary"])
    else:
        p_short = clean_text_for_speech(pet_args[0]) if pet_args else "the challenged act or ruling was invalid"
        r_short = clean_text_for_speech(resp_args[0]) if resp_args else "the challenged act or ruling was valid and lawful"
        args_primer_spoken = f"On this primary issue, the Petitioner argued that {p_short} Conversely, the Respondent countered that {r_short}"

    # Step 6: Ruling of the Court related to Primary Syllabus Issue(s)
    prim_rulings_primer = []
    prim_rulings_chapter = []
    for p in primary_list:
        a_clean = clean_text_for_speech(p["a"])
        d_clean = clean_text_for_speech(p["d"])
        if a_clean and d_clean:
            prim_rulings_primer.append(f"{a_clean} In plain language: {d_clean}")
            prim_rulings_chapter.append(f"{a_clean} Explained in plain, everyday terms: {d_clean}")
        elif a_clean:
            prim_rulings_primer.append(a_clean)
            prim_rulings_chapter.append(a_clean)
        elif d_clean:
            prim_rulings_primer.append(d_clean)
            prim_rulings_chapter.append(d_clean)
    ruling_primer_str = " ".join(prim_rulings_primer) or clean_text_for_speech(fields.get("outcome", ""))
    ruling_chapter_str = " ".join(prim_rulings_chapter) or ruling_primer_str

    # Step 7: Synthesis of resolving the Primary Syllabus Issue(s) using Step-by-Step Ratio Decidendi
    prim_ratios_primer = []
    prim_ratios_chapter = []
    for p in primary_list:
        b_clean = clean_text_for_speech(p["b"])
        c_clean = clean_text_for_speech(p["c"])
        if b_clean:
            prim_ratios_primer.append(b_clean)
            chap_ratio_item = f"Step-by-Step Ratio Decidendi: {b_clean}"
            if c_clean:
                chap_ratio_item += f" In the exact verbatim words of the Supreme Court: {c_clean}"
            prim_ratios_chapter.append(chap_ratio_item)
    ratio_primer_str = " ".join(prim_ratios_primer) or clean_text_for_speech(fields.get("cold_call", ""))
    ratio_chapter_str = " ".join(prim_ratios_chapter) or ratio_primer_str

    # Step 8: Doctrine Identifier relating to the final caselaw rule
    doc_id_spoken = clean_text_for_speech(fields.get("doctrine_identifier", ""))
    req_spoken = clean_text_for_speech(fields.get("requisites", ""))
    magic_spoken = clean_text_for_speech(fields.get("magic_words", ""))
    first_quote_spoken = clean_text_for_speech(primary_list[0]["c"]) if (primary_list and primary_list[0].get("c")) else ""

    doctrine_primer_parts = []
    if doc_id_spoken:
        doctrine_primer_parts.append(doc_id_spoken)
    elif first_quote_spoken:
        doctrine_primer_parts.append(f"The controlling jurisprudential rule states: {first_quote_spoken}")
    elif req_spoken:
        doctrine_primer_parts.append(req_spoken)
    if magic_spoken:
        doctrine_primer_parts.append(f"Key terms of art and magic words to remember: {magic_spoken}.")
    doctrine_primer_str = " ".join(doctrine_primer_parts) or clean_text_for_speech(fields.get("cold_call", ""))

    doctrine_chapter_parts = []
    if doc_id_spoken:
        doctrine_chapter_parts.append(f"Final Caselaw Rule: {doc_id_spoken}")
    if req_spoken:
        doctrine_chapter_parts.append(f"Doctrinal Requisites and Elements Checklist: {req_spoken}")
    if magic_spoken:
        doctrine_chapter_parts.append(f"Subject-Specific Terms of Art and Magic Words: {magic_spoken}.")
    if spotlight_extra:
        doctrine_chapter_parts.append(" ".join(clean_text_for_speech(x) for x in spotlight_extra))
    doctrine_chapter_str = " ".join(doctrine_chapter_parts) or doctrine_primer_str

    # Step 9: Final Outcome of the case in plain language
    outcome_spoken = clean_text_for_speech(
        fields.get("outcome") or fields.get("fallo") or "See the dispositive portion of the decision."
    )

    # =========================================================================
    # ASSEMBLE VERSION 1: COLD-CALL PRIMER (9-STEP STRUCTURE)
    # =========================================================================
    primer_step4 = prim_issue_question_str
    if prim_issue_expl_spoken and prim_issue_expl_spoken.lower() not in prim_issue_question_str.lower():
        primer_step4 = f"{prim_issue_question_str} {prim_issue_expl_spoken}"

    primer_parts = [
        f"Cold-Call Audio Primer: {title_spoken}. "
        f"First-Tier Subject: {subj_spoken}. Second-Tier Syllabus Topic: {top_spoken}. "
        f"Relevant Governing Law: {spoken_law_summary}",
        f"Factual Trigger: {trigger_spoken}",
        f"Connection to the Primary Issue: {trigger_bridge_spoken}",
        f"Primary Syllabus Issue Explained: {primer_step4}",
        f"Competing Arguments on the Primary Issue: {args_primer_spoken}",
        f"Supreme Court Ruling on the Primary Issue: {ruling_primer_str}",
        f"Step-by-Step Ratio Decidendi Synthesis: {ratio_primer_str}",
        f"Doctrine Identifier and Controlling Caselaw Rule: {doctrine_primer_str}",
        f"Final Outcome in Plain Language: {outcome_spoken}",
    ]
    primer_script = "\n\n".join(p for p in primer_parts if p.strip())

    # =========================================================================
    # ASSEMBLE VERSION 2: COMPLETE AUDIO CHAPTER (13-STEP STRUCTURE)
    # =========================================================================
    chap_step1_law = spoken_law_summary
    if gov_law_full and gov_law_full.lower() not in spoken_law_summary.lower():
        chap_step1_law = f"{spoken_law_summary} Specifically, the governing provision reads: {gov_law_full}"
    if fields.get("historical_rule"):
        chap_step1_law += f" Historical Landmark Note: {clean_text_for_speech(fields['historical_rule'])}"

    chap_step2_trigger = trigger_spoken
    cast_posture = []
    if fields.get("key_parties"):
        cast_posture.append(f"The Key Parties: {clean_text_for_speech(fields['key_parties'])}")
    if fields.get("posture"):
        cast_posture.append(f"Procedural Posture: {clean_text_for_speech(fields['posture'])}")
    if cast_posture:
        chap_step2_trigger = " ".join(cast_posture) + f" Here is the Factual Trigger that sparked the dispute: {trigger_spoken}"

    chap_step4_issue = prim_issue_question_str
    if prim_issue_expl_spoken:
        chap_step4_issue = f"{prim_issue_question_str} In plain terms: {prim_issue_expl_spoken}"

    chap_step5_args = args_primer_spoken
    if pet_full_spoken or resp_full_spoken:
        detailed_args = []
        if pet_full_spoken:
            detailed_args.append(f"In detail, the Petitioner contended: {pet_full_spoken}")
        if resp_full_spoken:
            detailed_args.append(f"In response, the Respondent argued: {resp_full_spoken}")
        chap_step5_args = f"{args_primer_spoken} " + " ".join(detailed_args)

    chapter_parts = [
        f"Complete Audio Case Chapter: {clean_text_for_speech(citation)}. "
        f"Step 1. Subject, Syllabus Topic, and Governing Law. "
        f"First-Tier Subject Category: {subj_spoken}. Second-Tier Syllabus Topic: {top_spoken}. "
        f"Main Relevant Law governing the primary syllabus issue: {chap_step1_law}",
        f"Step 2. Factual Trigger Related to the Primary Syllabus Issue. {chap_step2_trigger}",
        f"Step 3. How the Factual Trigger Relates to the Primary Syllabus Issue. {trigger_bridge_spoken}",
        f"Step 4. Explanation of the Primary Syllabus Issue. {chap_step4_issue}",
        f"Step 5. Arguments of the Petitioner and Respondent on the Primary Issue. {chap_step5_args}",
        f"Step 6. Ruling of the Supreme Court on the Primary Syllabus Issue. {ruling_chapter_str}",
        f"Step 7. Synthesis of Resolving the Primary Syllabus Issue via the Step-by-Step Ratio Decidendi. {ratio_chapter_str}",
        f"Step 8. Doctrine Identifier and Final Caselaw Rule. {doctrine_chapter_str}",
        f"Step 9. Final Outcome of the Case in Plain Language. {outcome_spoken}",
    ]

    # Explicit Audio Transition between Step 9 and Step 10
    custom_trans = clean_text_for_speech(fields.get("chapter_transition", ""))
    transition_statement = (
        "The main topic of contention is now resolved. "
        "The next set of audio overviews covers the other details, chronological context, and remaining issues in the case."
    )
    if custom_trans and "main topic of contention" not in custom_trans.lower():
        transition_statement = f"{transition_statement} {custom_trans}"
    elif custom_trans:
        transition_statement = custom_trans
    chapter_parts.append(transition_statement)

    # Step 10: All other related issues in the case not yet discussed (plus chronological facts storyboard)
    step10_segments = ["Step 10. All Other Related Issues and Additional Case Details Not Yet Discussed."]
    if timeline_events:
        timeline_spoken = " ".join(clean_text_for_speech(ev) for ev in timeline_events)
        step10_segments.append(f"First, here is the complete Chronological Facts Storyboard for full procedural context: {timeline_spoken}")

    if secondary_list:
        step10_segments.append(f"Next, the Supreme Court resolved {len(secondary_list)} additional procedural or secondary issue(s):")
        for s_idx, sec_iss in enumerate(secondary_list, start=1):
            q_sec = clean_text_for_speech(sec_iss["recit_q"] or sec_iss["question"] or sec_iss["a"])
            a_sec = clean_text_for_speech(sec_iss["a"])
            d_sec = clean_text_for_speech(sec_iss["d"])
            b_sec = clean_text_for_speech(sec_iss["b"])
            c_sec = clean_text_for_speech(sec_iss["c"])
            sec_narration = [f"Additional Issue {s_idx}: {q_sec}"]
            if a_sec and a_sec != q_sec:
                sec_narration.append(f"Ruling: {a_sec}")
            if d_sec:
                sec_narration.append(f"Plain-language explanation: {d_sec}")
            if b_sec:
                sec_narration.append(f"Ratio Decidendi: {b_sec}")
            if c_sec:
                sec_narration.append(f"Verbatim holding: {c_sec}")
            step10_segments.append(" ".join(sec_narration))
    else:
        step10_segments.append("No separate secondary or collateral issues were left unresolved beyond the primary syllabus issue.")

    chapter_parts.append(" ".join(step10_segments))

    # Step 11: Final Disposition (Fallo)
    fallo_spoken = clean_text_for_speech(fields.get("fallo") or fields.get("outcome") or "Wherefore, the petition is resolved accordingly.")
    chapter_parts.append(f"Step 11. Final Disposition and Official Fallo. {fallo_spoken}")

    # Step 12: Separate Opinions (if any)
    sep_raw = fields.get("separate_opinions", "").strip()
    if sep_raw and "no separate opinions" not in sep_raw.lower():
        sep_spoken = clean_text_for_speech(sep_raw)
    else:
        sep_spoken = "No separate concurring or dissenting opinions were filed in this decision."
    chapter_parts.append(f"Step 12. Separate Opinions. {sep_spoken}")

    # Step 13: Final Review of the Main Reckoning Point of the Case
    reckoning_parts = []
    if fields.get("final_reckoning"):
        reckoning_parts.append(clean_text_for_speech(fields["final_reckoning"]))
    if fields.get("cold_call") and fields.get("cold_call") != fields.get("final_reckoning"):
        reckoning_parts.append(f"Thirty-Second Cold-Call Synthesis: {clean_text_for_speech(fields['cold_call'])}")
    if not reckoning_parts:
        reckoning_parts.append(f"In summary, on the issue of {prim_issue_question_str}, {ruling_primer_str} {outcome_spoken}")
    chapter_parts.append("Step 13. Final Review of the Main Reckoning Point of the Case. " + " ".join(reckoning_parts))

    chapter_script = "\n\n".join(p for p in chapter_parts if p.strip())

    # Apply Phonetic Pronunciation Guide from Section 8 if available
    phonetic_guide = fields.get("phonetic_guide", "")
    if phonetic_guide:
        primer_script = apply_phonetic_pronunciations(primer_script, phonetic_guide)
        chapter_script = apply_phonetic_pronunciations(chapter_script, phonetic_guide)

    return primer_script, chapter_script


def synthesize_edge_tts_mp3(
    script_text: str,
    output_path: Path,
    voice: str = DEFAULT_AUDIO_VOICE,
    rate: str = "+0%",
) -> Tuple[bool, str]:
    """Synthesizes an MP3 file using edge-tts (en-US-AndrewMultilingualNeural)."""
    try:
        import asyncio
        import edge_tts

        output_path.parent.mkdir(parents=True, exist_ok=True)

        async def _run_tts():
            communicate = edge_tts.Communicate(text=script_text, voice=voice, rate=rate)
            await communicate.save(str(output_path))

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run_tts())
        finally:
            loop.close()
        return True, str(output_path)
    except ImportError as exc:
        return False, f"Audio generation requires the optional 'edge-tts' package. Install it with: pip install edge-tts ({exc})"
    except Exception as exc:
        return False, str(exc)


def render_audio_briefing(
    title: str,
    description: str,
    script_text: str,
    output_path: Path,
    widget_key: str,
    spinner_text: str,
) -> None:
    """Render one audio player and one generate/regenerate action."""
    st.markdown(f"**{title}**")
    st.caption(description)
    already_exists = output_path.is_file()
    if already_exists:
        st.audio(str(output_path), format="audio/mp3")

    action = "Regenerate" if already_exists else "Generate"
    if st.button(f"{action} audio", key=widget_key, use_container_width=True):
        with st.spinner(spinner_text):
            ok, message = synthesize_edge_tts_mp3(
                script_text, output_path, voice=DEFAULT_AUDIO_VOICE
            )
        if ok:
            st.rerun()
        else:
            st.error(f"Audio generation failed: {message}")


# --- 5. THEME, STICKY NAVIGATOR & RESPONSIVE CSS ---
def inject_custom_css(theme: str, focus_mode: bool, font_scale: str, cabinet_open: bool = True):
    """Apply the editorial paper-and-ink design system to the Streamlit UI."""
    is_dark = theme == "Dark"
    scale_map = {
        "Compact": ("15.5px", "39px", "23px"),
        "Standard": ("17px", "46px", "25px"),
        "Comfortable": ("18.5px", "50px", "27px"),
    }
    body_px, title_px, h3_px = scale_map.get(font_scale, ("17px", "46px", "25px"))

    if is_dark:
        colors = {
            "BG_APP": "#141314",
            "BG_SIDEBAR": "#1D1B19",
            "BG_SURFACE": "#201F1C",
            "BG_ELEVATED": "#292722",
            "BG_INPUT": "#201F1C",
            "TEXT_PRIMARY": "#FCFCFD",
            "TEXT_SECONDARY": "#E1DED7",
            "TEXT_MUTED": "#B1ACA2",
            "TEXT_INVERSE": "#141314",
            "ACCENT": "#FFC61A",
            "ACCENT_SOFT": "rgba(255,198,26,.16)",
            "BORDER": "#49453D",
            "BUTTON_BG": "#F5F4F0",
            "BUTTON_TEXT": "#141314",
            "BUTTON_HOVER": "#E7E3D9",
            "CALLOUT": "#29261E",
            "SHADOW": "0 8px 26px rgba(0,0,0,.24)",
            "RADIO_HOVER": "#2A2823",
        }
    else:
        colors = {
            "BG_APP": "#F5F4F0",
            "BG_SIDEBAR": "#ECEBE8",
            "BG_SURFACE": "#FFFEFA",
            "BG_ELEVATED": "#ECEBE8",
            "BG_INPUT": "#FFFEFA",
            "TEXT_PRIMARY": "#000000",
            "TEXT_SECONDARY": "#373737",
            "TEXT_MUTED": "#505050",
            "TEXT_INVERSE": "#FCFCFD",
            "ACCENT": "#FFC61A",
            "ACCENT_SOFT": "#FFF1C2",
            "BORDER": "#D6D5D2",
            "BUTTON_BG": "#131313",
            "BUTTON_TEXT": "#FCFCFD",
            "BUTTON_HOVER": "#373737",
            "CALLOUT": "#F3F0E7",
            "SHADOW": "0 4px 18px rgba(20,19,20,.055)",
            "RADIO_HOVER": "#E4E2DC",
        }

    css = r"""
    @import url('https://fonts.googleapis.com/css2?family=EB+Garamond:wght@400;500;600;700&family=Host+Grotesk:wght@400;500;600;700&display=swap');

    :root {
      --bg-app: @@BG_APP@@;
      --bg-sidebar: @@BG_SIDEBAR@@;
      --bg-surface: @@BG_SURFACE@@;
      --bg-elevated: @@BG_ELEVATED@@;
      --bg-input: @@BG_INPUT@@;
      --text-primary: @@TEXT_PRIMARY@@;
      --text-secondary: @@TEXT_SECONDARY@@;
      --text-muted: @@TEXT_MUTED@@;
      --text-inverse: @@TEXT_INVERSE@@;
      --accent: @@ACCENT@@;
      --accent-soft: @@ACCENT_SOFT@@;
      --border: @@BORDER@@;
      --button-bg: @@BUTTON_BG@@;
      --button-text: @@BUTTON_TEXT@@;
      --button-hover: @@BUTTON_HOVER@@;
      --callout: @@CALLOUT@@;
      --shadow: @@SHADOW@@;
      --radio-hover: @@RADIO_HOVER@@;
      --font-ui: 'Host Grotesk', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-display: 'EB Garamond', Georgia, serif;
      --body-size: @@BODY_SIZE@@;
      --title-size: @@TITLE_SIZE@@;
      --heading-size: @@H3_SIZE@@;
    }

    html { scroll-behavior: smooth; }
    body, .stApp, [data-testid="stAppViewContainer"] {
      background: var(--bg-app) !important;
      color: var(--text-primary) !important;
      font-family: var(--font-ui) !important;
    }
    header[data-testid="stHeader"] { display:none !important; }
    div[data-testid="stToolbar"], div[data-testid="stDecoration"],
    div[data-testid="stStatusWidget"] { display:none !important; }
    .block-container {
      max-width: 1600px !important;
      padding: 1.4rem 2rem 5rem !important;
    }
    div[data-testid="stHorizontalBlock"] { gap: 1rem; align-items: flex-start; }
    div[data-testid="stVerticalBlock"] { gap: .72rem; }
    p, li, label, span, div { font-family: var(--font-ui); }

    /* Hide Streamlit's native double-chevron; retain the app-owned drawer toggle. */
    [data-testid="stSidebarCollapseButton"] {
      display: none !important;
    }

    /* App-controlled drawer: the top-left menu button remains in the main page. */
    section[data-testid="stSidebar"] {
      display: @@SIDEBAR_DISPLAY@@ !important;
      width: 292px !important;
      min-width: 292px !important;
      max-width: 292px !important;
      flex: 0 0 292px !important;
      background: var(--bg-sidebar) !important;
      border-right: 1px solid var(--border) !important;
      padding-top: .8rem !important;
    }
    section[data-testid="stSidebar"] > div {
      background: var(--bg-sidebar) !important;
      overflow-y: auto !important;
    }
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] h4 { color: var(--text-primary) !important; }
    section[data-testid="stSidebar"] h3 {
      font-family: var(--font-display) !important;
      font-size: 1.75rem !important;
      font-weight: 600 !important;
      letter-spacing: -.02em;
      margin-bottom: .1rem !important;
    }
    section[data-testid="stSidebar"] [data-testid="stSelectbox"] label {
      font-size: .72rem !important;
      letter-spacing: .07em;
      text-transform: uppercase;
      color: var(--text-muted) !important;
      font-weight: 700 !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] { gap: .16rem !important; }
    section[data-testid="stSidebar"] div[role="radiogroup"] label {
      width: 100% !important;
      align-items: flex-start !important;
      padding: .48rem .52rem !important;
      border-radius: 7px !important;
      border: 1px solid transparent !important;
      line-height: 1.35 !important;
      transition: background .12s ease, border-color .12s ease;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
      background: var(--radio-hover) !important;
      border-color: var(--border) !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
      background: var(--accent-soft) !important;
      border-color: color-mix(in srgb, var(--accent) 55%, var(--border)) !important;
      box-shadow: inset 3px 0 0 var(--accent);
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] input { accent-color: #C99500 !important; }

    /* Unified controls and keyboard focus. */
    .stButton > button, .stPopover > div > button {
      min-height: 2.45rem !important;
      border-radius: 8px !important;
      border: 1px solid var(--border) !important;
      background: var(--bg-surface) !important;
      color: var(--text-primary) !important;
      font-family: var(--font-ui) !important;
      font-size: .84rem !important;
      font-weight: 600 !important;
      transition: background .14s ease, border-color .14s ease, transform .14s ease;
      box-shadow: none !important;
    }
    .stButton > button:hover, .stPopover > div > button:hover {
      background: var(--button-hover) !important;
      color: var(--text-primary) !important;
      border-color: var(--text-primary) !important;
    }
    .stButton > button:focus-visible, .stPopover > div > button:focus-visible,
    input:focus-visible, textarea:focus-visible, a:focus-visible {
      outline: 3px solid var(--accent) !important;
      outline-offset: 2px !important;
    }
    .stButton > button p, .stButton > button span { color: inherit !important; }
    div[data-baseweb="input"] > div, div[data-baseweb="select"] > div,
    div[data-baseweb="textarea"] > div {
      background: var(--bg-input) !important;
      color: var(--text-primary) !important;
      border: 1px solid var(--border) !important;
      border-radius: 8px !important;
      min-height: 2.45rem !important;
    }
    div[data-baseweb="input"] input, div[data-baseweb="textarea"] textarea {
      color: var(--text-primary) !important;
      -webkit-text-fill-color: var(--text-primary) !important;
      font-family: var(--font-ui) !important;
    }
    div[data-baseweb="input"] input::placeholder {
      color: var(--text-muted) !important;
      -webkit-text-fill-color: var(--text-muted) !important;
      opacity: 1 !important;
    }
    div[data-baseweb="select"] span, div[data-baseweb="select"] div { color: var(--text-primary) !important; }
    ul[data-baseweb="menu"], div[data-baseweb="popover"], li[role="option"] {
      background: var(--bg-surface) !important;
      color: var(--text-primary) !important;
    }
    div[data-testid="stExpander"] {
      border: 1px solid var(--border) !important;
      border-radius: 8px !important;
      background: var(--bg-surface) !important;
    }
    div[data-testid="stExpander"] summary { font-weight: 600 !important; }
    div[data-testid="stExpander"] summary, div[data-testid="stExpander"] p,
    div[data-testid="stExpander"] span { color: var(--text-primary) !important; }
    hr { border-color: var(--border) !important; }

    /* Masthead: quiet brand row, editorial type, compact evidence metrics. */
    .masthead-brand { display:flex; align-items:center; gap:.85rem; min-width:0; }
    .masthead-icon {
      width:46px; height:46px; display:grid; place-items:center;
      border:1px solid var(--text-primary); border-radius:50%;
      font-size:1.45rem; flex:none; color:var(--text-primary);
    }
    .masthead-title {
      font-family:var(--font-display) !important; font-size:2rem;
      font-weight:600; line-height:.98; letter-spacing:-.025em;
      color:var(--text-primary) !important; margin:0;
    }
    .masthead-subtitle {
      font-family:var(--font-ui) !important; font-size:.68rem;
      text-transform:uppercase; letter-spacing:.13em; color:var(--text-muted) !important;
      margin-top:.35rem;
    }
    .masthead-stats { display:flex; justify-content:flex-end; gap:1.15rem; align-items:center; flex-wrap:wrap; }
    .stat-item { border-left:1px solid var(--border); padding-left:.8rem; min-width:70px; }
    .stat-value { display:block; font-family:var(--font-display) !important; font-size:1.55rem; line-height:1; font-weight:600; color:var(--text-primary) !important; }
    .stat-label { display:block; font-family:var(--font-ui) !important; font-size:.62rem; letter-spacing:.09em; text-transform:uppercase; color:var(--text-muted) !important; margin-top:.28rem; }
    div[data-testid="stHorizontalBlock"]:has(.masthead-brand) div[data-testid="stColumn"]:first-child div[data-testid="stButton"] > button,
    div[data-testid="stHorizontalBlock"]:has(.masthead-brand) div[data-testid="column"]:first-child div[data-testid="stButton"] > button {
      width:44px !important; min-width:44px !important; height:44px !important;
      padding:0 !important; font-size:1.18rem !important;
      background:var(--bg-surface) !important; border:1px solid var(--border) !important;
    }

    /* Search bar and task controls. */
    .command-label {
      font-family:var(--font-ui); color:var(--text-muted); font-size:.64rem;
      text-transform:uppercase; letter-spacing:.1em; font-weight:700;
      margin:0 0 .35rem .05rem;
    }
    .workspace-rule { border-bottom:1px solid var(--border); margin:.15rem 0 1rem; }

    /* Search result cards. */
    .search-heading {
      font-family:var(--font-display) !important; font-size:2rem;
      font-weight:600; line-height:1.05; color:var(--text-primary) !important;
      margin:.35rem 0 .15rem;
    }
    .search-card {
      background:var(--bg-surface); border:1px solid var(--border);
      border-radius:8px; padding:1.1rem 1.25rem; margin:.65rem 0;
      box-shadow:var(--shadow); border-left:3px solid var(--accent);
    }
    .search-card-link {
      display:block; color:inherit !important; text-decoration:none !important;
      border-radius:8px; outline:none; cursor:pointer;
    }
    .search-card-link:focus-visible {
      outline:3px solid var(--accent) !important; outline-offset:3px !important;
    }
    .search-card-link:hover .search-card {
      border-color:var(--accent) !important;
      transform:translateY(-1px);
      box-shadow:var(--shadow-hover, var(--shadow));
    }
    .search-card-link .search-card { cursor:pointer; transition:transform .14s ease, border-color .14s ease, box-shadow .14s ease; }
    .search-card-open-hint {
      margin-top:.55rem; color:var(--text-muted); font:600 .72rem var(--font-ui);
      letter-spacing:.02em;
    }
    .search-card-title {
      font-family:var(--font-display) !important; font-size:1.42rem;
      font-weight:600; line-height:1.12; color:var(--text-primary) !important;
      margin:.2rem 0 .5rem;
    }
    .search-snippet {
      font-family:var(--font-ui) !important; font-size:.92rem;
      line-height:1.6; color:var(--text-secondary) !important;
    }
    .search-meta-row { display:flex; gap:.45rem; align-items:center; flex-wrap:wrap; margin-bottom:.25rem; }
    .search-meta-chip {
      background:var(--bg-elevated); border:1px solid var(--border);
      color:var(--text-muted) !important; border-radius:4px;
      padding:.16rem .45rem; font:500 .65rem var(--font-ui);
    }

    /* Document surface and legal reading hierarchy. */
    .reader-sheet {
      background:var(--bg-surface); border:1px solid var(--border);
      border-radius:9px; padding:2.25rem 2.55rem 3.5rem;
      max-width:1020px; margin:0 auto 2rem;
      box-shadow:var(--shadow); color:var(--text-primary);
    }
    .reader-eyebrow {
      color:var(--text-muted); font:700 .65rem var(--font-ui);
      text-transform:uppercase; letter-spacing:.14em; margin:.1rem 0 .45rem;
    }
    .breadcrumb-bar {
      display:flex; flex-wrap:wrap; align-items:center; gap:.4rem;
      margin-bottom:.75rem; color:var(--text-muted);
    }
    .subj-badge, .cat-badge, .gr-badge {
      display:inline-flex; align-items:center; padding:.23rem .55rem;
      border:1px solid var(--border); border-radius:5px;
      background:var(--bg-elevated); color:var(--text-secondary) !important;
      font-family:var(--font-ui); font-size:.68rem; font-weight:600;
    }
    .gr-badge { font-variant-numeric:tabular-nums; }
    .reader-sheet h1.case-main-title {
      font-family:var(--font-display) !important; font-size:var(--title-size) !important;
      font-weight:600 !important; line-height:.99 !important;
      letter-spacing:-.025em !important; color:var(--text-primary) !important;
      margin:.45rem 0 .65rem !important;
    }
    .case-meta-row {
      display:flex; align-items:center; flex-wrap:wrap; gap:.65rem;
      color:var(--text-muted); font:500 .77rem var(--font-ui);
      padding-bottom:1.05rem; margin-bottom:1.3rem;
      border-bottom:1px solid var(--border);
    }
    .source-link { color:var(--text-primary) !important; font-weight:600; text-decoration:underline; text-decoration-color:var(--accent); text-underline-offset:3px; }
    .source-link:hover { color:var(--text-secondary) !important; }
    .digest-section-block { margin-top:2.1rem; padding-top:.1rem; scroll-margin-top:24px; }
    .digest-section-block:first-child { margin-top:0; }
    .digest-section-header {
      display:flex; align-items:baseline; gap:.75rem;
      border-bottom:1px solid var(--border); padding-bottom:.52rem; margin-bottom:1rem;
    }
    .sec-num-badge {
      color:var(--text-muted) !important; font:600 .73rem var(--font-ui);
      letter-spacing:.08em; flex:none;
    }
    .reader-sheet h3.sec-heading-text {
      font-family:var(--font-display) !important; font-size:var(--heading-size) !important;
      font-weight:600 !important; line-height:1.12 !important;
      color:var(--text-primary) !important; margin:0 !important;
    }
    .reader-sheet .legal-prose, .reader-sheet .digest-p,
    .reader-sheet .timeline-event, .reader-sheet .num-content,
    .reader-sheet .trigger-text, .reader-sheet .fallo-text,
    .reader-sheet .quote-callout-body, .reader-sheet .recit-callout {
      font-family:var(--font-ui) !important; font-size:var(--body-size) !important;
      line-height:1.76 !important; color:var(--text-secondary) !important;
      text-align:left !important; max-width:76ch;
    }
    .digest-p { margin:0 0 1rem 0 !important; }
    .inline-lead { font-family:var(--font-ui) !important; font-weight:700 !important; color:var(--text-primary) !important; }
    .digest-subheading { font-family:var(--font-ui) !important; font-size:1.02rem !important; font-weight:700 !important; color:var(--text-primary) !important; margin:1.35rem 0 .55rem !important; }
    .party-header {
      font-family:var(--font-ui) !important; font-size:.9rem !important;
      font-weight:700 !important; color:var(--text-primary) !important;
      background:var(--bg-elevated); border-left:3px solid var(--accent);
      padding:.55rem .85rem; margin:1.1rem 0 .7rem; border-radius:0 6px 6px 0;
    }
    .trigger-box, .quote-callout, .legal-blockquote, .recit-callout,
    .issue-verdict-banner, .fallo-box {
      background:var(--callout); border:1px solid var(--border);
      border-left:3px solid var(--accent); border-radius:0 7px 7px 0;
      padding:.95rem 1.15rem; margin:1rem 0 1.25rem;
    }
    .trigger-title, .quote-callout-label, .fallo-title {
      font-family:var(--font-ui) !important; font-size:.7rem !important;
      font-weight:700 !important; letter-spacing:.09em; text-transform:uppercase;
      color:var(--text-muted) !important; margin-bottom:.35rem;
    }
    .recit-label, .verdict-tag { font-family:var(--font-ui) !important; font-weight:700 !important; color:var(--text-primary) !important; }
    .timeline-row { display:grid; grid-template-columns:145px 1fr; gap:1rem; padding:.7rem 0; border-bottom:1px solid var(--border); align-items:baseline; }
    .timeline-date { font:700 .83rem var(--font-ui) !important; color:var(--text-primary) !important; }
    .numbered-item { display:flex; gap:.75rem; align-items:flex-start; margin-bottom:.85rem; }
    .num-circle { font:600 .75rem var(--font-ui) !important; color:var(--text-primary) !important; background:var(--accent-soft); padding:.15rem .45rem; border-radius:4px; flex:none; margin-top:.12rem; }
    .issue-pill { font:700 .67rem var(--font-ui) !important; padding:.14rem .42rem; border-radius:4px; margin-right:.35rem; vertical-align:middle; }
    .issue-primary { background:var(--accent); color:#141314 !important; }
    .issue-secondary { background:var(--bg-elevated); color:var(--text-secondary) !important; border:1px solid var(--border); }
    mark.search-mark { background:#FFE38B; color:#141314; padding:.04rem .18rem; border-radius:2px; font-weight:600; }

    /* Persistent right-side case navigator. */
    div[data-testid="stHorizontalBlock"] { align-items:flex-start !important; }
    div[data-testid="stColumn"]:has(#sticky-right-nav-marker),
    div[data-testid="column"]:has(#sticky-right-nav-marker) {
      position:sticky !important; top:1rem !important; align-self:flex-start !important;
      max-height:calc(100vh - 2rem) !important; overflow-y:auto !important;
      padding-right:.15rem !important; z-index:8 !important;
    }
    .navigator-card {
      background:var(--bg-elevated); border:1px solid var(--border);
      border-radius:8px; padding:1rem .9rem; box-shadow:none; margin-bottom:.7rem;
    }
    .navigator-title {
      font-family:var(--font-display) !important; font-size:1.35rem;
      font-weight:600; line-height:1.05; color:var(--text-primary) !important;
      margin-bottom:.65rem; padding:0 .2rem .65rem; border-bottom:1px solid var(--border);
    }
    .toc-nav-list { display:flex; flex-direction:column; gap:.18rem; margin-top:.35rem; }
    a.toc-jump-link {
      display:flex; align-items:center; gap:.5rem; padding:.43rem .5rem;
      border-radius:5px; color:var(--text-secondary) !important;
      text-decoration:none !important; font:500 .76rem/1.3 var(--font-ui);
      transition:background .12s ease, color .12s ease;
    }
    a.toc-jump-link:hover, a.toc-jump-link.active { background:var(--accent-soft); color:var(--text-primary) !important; }
    a.toc-jump-link.active { font-weight:700; box-shadow:inset 2px 0 0 var(--accent); }
    .toc-sec-badge { color:var(--text-muted) !important; font:600 .65rem var(--font-ui); min-width:1.25rem; }
    .search-mark { background:#FFE38B !important; color:#141314 !important; }

    /* Focus mode: reading canvas narrows; the cabinet and right navigator remain available. */
    @@FOCUS_READING@@

    @media (max-width:1100px) {
      .block-container { padding-left:1.35rem !important; padding-right:1.35rem !important; }
      .reader-sheet { padding:1.8rem 1.6rem 2.7rem; }
      .masthead-title { font-size:1.7rem; }
    }
    @media (max-width:850px) {
      .block-container { padding:1rem .9rem 3rem !important; }
      .masthead-icon { width:39px; height:39px; font-size:1.2rem; }
      .masthead-title { font-size:1.4rem; }
      .masthead-subtitle { font-size:.57rem; }
      .masthead-stats { gap:.55rem; }
      .stat-item { padding-left:.5rem; min-width:54px; }
      .stat-value { font-size:1.15rem; }
      .stat-label { font-size:.53rem; }
      .reader-sheet { padding:1.5rem 1.15rem 2.4rem; }
      div[data-testid="stColumn"]:has(#sticky-right-nav-marker),
      div[data-testid="column"]:has(#sticky-right-nav-marker) {
        position:static !important; max-height:none !important; overflow:visible !important;
      }
      .navigator-card { padding:.8rem; }
      .toc-nav-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); }
      .timeline-row { grid-template-columns:1fr; gap:.2rem; }
    }
    @media (max-width:560px) {
      .block-container { padding-left:.65rem !important; padding-right:.65rem !important; }
      .masthead-subtitle { display:none; }
      .masthead-title { font-size:1.15rem; }
      .masthead-icon { width:34px; height:34px; }
      .masthead-stats { display:none; }
      .reader-sheet h1.case-main-title { font-size:2.25rem !important; }
      .reader-sheet h3.sec-heading-text { font-size:1.35rem !important; }
      .toc-nav-list { grid-template-columns:1fr 1fr; }
      .search-card { padding:.9rem; }
    }
    """

    replacements = {**colors,
        "BODY_SIZE": body_px,
        "TITLE_SIZE": title_px,
        "H3_SIZE": h3_px,
        "SIDEBAR_DISPLAY": "flex" if cabinet_open else "none",
        "FOCUS_READING": ".reader-sheet { max-width: 860px !important; margin-left:auto !important; margin-right:auto !important; }" if focus_mode else "",
    }
    for key, value in replacements.items():
        css = css.replace(f"@@{key}@@", value)

    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


# --- 6. LOAD LIBRARY DATA ---
cases, bm25, tokenized_corpus = load_library()

# Search result cards are full-card links. They pass a one-time case UID through
# the URL so the selection is applied before sidebar widgets are instantiated.
# This avoids inert HTML cards and keeps the existing Streamlit reader workflow.
def _consume_open_case_query(library_cases: List[Dict[str, Any]]) -> None:
    try:
        requested_uid = st.query_params.get("open_case", "")
    except Exception:
        requested_uid = ""

    if not requested_uid:
        return

    target_case = next(
        (case for case in library_cases if str(case.get("_uid", "")) == str(requested_uid)),
        None,
    )
    if target_case:
        st.session_state.selected_lookup_key = target_case["_uid"]
        st.session_state.selected_subject = target_case.get("subject") or "-- All Subjects --"
        st.session_state.selected_topic = target_case.get("topic") or "-- All Topics --"
        st.session_state.active_search_query = ""
        st.session_state.scroll_to_anchor = "reader-top-anchor"
        # Keep widget state aligned before the sidebar selectboxes/radio are built.
        st.session_state["_sidebar_subj_select"] = st.session_state.selected_subject
        st.session_state["_sidebar_top_select"] = st.session_state.selected_topic
        st.session_state["_sidebar_case_radio"] = target_case["_uid"]

    # Remove the navigation token so it cannot re-select the same case on later
    # interactions (for example, when changing the reading mode or font size).
    try:
        st.query_params.pop("open_case", None)
    except Exception:
        pass


_consume_open_case_query(cases)

if cases and not st.session_state.selected_lookup_key:
    st.session_state.selected_lookup_key = cases[0]["_uid"]

# Search state is available before rendering the sidebar, so the left case list can
# immediately reflect the same global search as the main results area.
query_str = st.session_state.get("active_search_query", "").strip()
query_tokens = [token for token in re.split(r"\W+", query_str.lower()) if token]
ranked_pairs = rank_case_matches(query_str, cases, bm25)

inject_custom_css(
    st.session_state.theme_mode,
    st.session_state.focus_mode,
    st.session_state.font_scale,
    st.session_state.cabinet_open,
)


# --- 7. SIDEBAR: 2-TIER FILING CABINET + PERSISTENT CASE LIST ---
with st.sidebar:
    st.markdown("### Filing Cabinet")
    st.caption("Browse the library by subject, topic, or case.")

    if not cases:
        st.warning("No cases found in `case_library/master_library.jsonl`. Run the Desktop Generator or import a `.docx` casebook.")
    else:
        all_subjects = sorted({c.get("subject", "General Jurisprudence") for c in cases})
        subj_counts = {subject: sum(1 for c in cases if c.get("subject") == subject) for subject in all_subjects}
        subj_options = ["-- All Subjects --"] + all_subjects
        if st.session_state.selected_subject not in subj_options:
            st.session_state.selected_subject = "-- All Subjects --"
        if st.session_state.get("_sidebar_subj_select") != st.session_state.selected_subject:
            st.session_state["_sidebar_subj_select"] = st.session_state.selected_subject

        st.selectbox(
            "Subject / Course",
            options=subj_options,
            format_func=lambda opt: (
                f"📚 All Subjects ({len(cases)})" if opt == "-- All Subjects --"
                else f"🏛️ {opt} ({subj_counts.get(opt, 0)})"
            ),
            key="_sidebar_subj_select",
            on_change=_cb_on_subject_change,
        )

        chosen_subj = st.session_state.selected_subject
        subj_filtered = cases if chosen_subj == "-- All Subjects --" else [c for c in cases if c.get("subject") == chosen_subj]

        avail_topics = sorted({c.get("topic", "General Topic") for c in subj_filtered})
        top_counts = {topic: sum(1 for c in subj_filtered if c.get("topic") == topic) for topic in avail_topics}
        top_options = ["-- All Topics --"] + avail_topics
        if st.session_state.selected_topic not in top_options:
            st.session_state.selected_topic = "-- All Topics --"
        if st.session_state.get("_sidebar_top_select") != st.session_state.selected_topic:
            st.session_state["_sidebar_top_select"] = st.session_state.selected_topic

        st.selectbox(
            "Syllabus Topic",
            options=top_options,
            format_func=lambda opt: (
                f"📂 All Topics ({len(subj_filtered)})" if opt == "-- All Topics --"
                else f"📁 {opt} ({top_counts.get(opt, 0)})"
            ),
            key="_sidebar_top_select",
            on_change=_cb_on_topic_change,
        )

        chosen_top = st.session_state.selected_topic
        folder_cases = (
            subj_filtered if chosen_top == "-- All Topics --"
            else [c for c in subj_filtered if c.get("topic") == chosen_top]
        )

        # The main search is global, so the persistent sidebar list mirrors every
        # matching result across the library. With no query, it shows the complete
        # case list for the selected subject/topic folder.
        if query_str:
            filtered_cases = [cases[idx] for idx, _score in ranked_pairs]
            list_heading = f"Search Matches ({len(filtered_cases)})"
        else:
            filtered_cases = folder_cases
            list_heading = f"Cases in Folder ({len(filtered_cases)})"

        st.divider()
        st.markdown(f"**{list_heading}**")
        if query_str:
            st.caption("Global results · ranked across all subjects and topics")

        if filtered_cases:
            uid_list = [case["_uid"] for case in filtered_cases]
            # Keep the radio selection synchronized with the active case whenever
            # that case is present. During a search, the reader selection is not
            # changed until the user actually clicks a result in this list.
            if st.session_state.selected_lookup_key in uid_list:
                radio_default = st.session_state.selected_lookup_key
            else:
                radio_default = uid_list[0]
            if st.session_state.get("_sidebar_case_radio") not in uid_list or st.session_state.get("_sidebar_case_radio") != radio_default:
                st.session_state["_sidebar_case_radio"] = radio_default

            uid_to_label = {
                case["_uid"]: f"📄 {case.get('citation', 'Untitled Case')}"
                for case in filtered_cases
            }
            st.radio(
                "Select a case",
                options=uid_list,
                format_func=lambda uid: uid_to_label.get(uid, uid),
                key="_sidebar_case_radio",
                on_change=_cb_select_sidebar_case,
                label_visibility="collapsed",
            )
        else:
            if query_str:
                st.info("No cases match this search. Try a broader phrase, party name, G.R. number, or doctrine.")
            else:
                st.info("No cases are available in this folder.")


# --- 8. EDITORIAL MASTHEAD & COMMAND BAR ---
unique_subjects_count = len({c.get("subject", "General Jurisprudence") for c in cases})
unique_topics_count = len({c.get("topic", "General Topic") for c in cases})

menu_col, brand_col, stats_col = st.columns([0.48, 4.4, 2.2])
with menu_col:
    st.button(
        "☰",
        key="toggle_filing_cabinet",
        on_click=_cb_toggle_cabinet,
        help=("Hide the filing cabinet" if st.session_state.cabinet_open else "Show the filing cabinet"),
    )
with brand_col:
    st.markdown(
        """
        <div class="masthead-brand">
          <div class="masthead-icon">⚖</div>
          <div>
            <div class="masthead-title">Philippine Jurisprudence Library</div>
            <div class="masthead-subtitle">Research · Read · Understand</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with stats_col:
    st.markdown(
        f"""
        <div class="masthead-stats">
          <div class="stat-item"><span class="stat-value">{len(cases):,}</span><span class="stat-label">Cases indexed</span></div>
          <div class="stat-item"><span class="stat-value">{unique_subjects_count:,}</span><span class="stat-label">Subjects</span></div>
          <div class="stat-item"><span class="stat-value">{unique_topics_count:,}</span><span class="stat-label">Topics</span></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
st.markdown("<div class='workspace-rule'></div>", unsafe_allow_html=True)

# Compact controls: one global search, one study lens, one typography control, one sequential view switch.
mode_options = [
    "📖 First-Time Read",
    "⚡ Exam & Recit Review",
    "🔎 Legal Research",
]
size_options = ["Compact", "Standard", "Comfortable"]
cmd_c1, cmd_c2, cmd_c3, cmd_c4 = st.columns([4.5, 1.65, 1.15, 1.15], gap="small")
with cmd_c1:
    st.markdown("<div class='command-label'>Search the jurisprudence library</div>", unsafe_allow_html=True)
    st.text_input(
        "Search jurisprudence",
        key="active_search_query",
        placeholder="Case title, G.R. number, party, doctrine, or Latin maxim…",
        label_visibility="collapsed",
    )
with cmd_c2:
    st.markdown("<div class='command-label'>Study lens</div>", unsafe_allow_html=True)
    selected_mode = st.selectbox(
        "Reading workflow",
        options=mode_options,
        index=mode_options.index(st.session_state.study_mode),
        label_visibility="collapsed",
    )
    if selected_mode != st.session_state.study_mode:
        st.session_state.study_mode = selected_mode
        st.rerun()
with cmd_c3:
    st.markdown("<div class='command-label'>Text size</div>", unsafe_allow_html=True)
    top_font_choice = st.selectbox(
        "Reading text size",
        options=size_options,
        index=size_options.index(st.session_state.font_scale),
        format_func=lambda value: {"Compact": "Compact", "Standard": "Standard", "Comfortable": "Large"}[value],
        label_visibility="collapsed",
    )
    if top_font_choice != st.session_state.font_scale:
        st.session_state.font_scale = top_font_choice
        st.rerun()
with cmd_c4:
    view_sequence = [
        ("Light", False, "Light"),
        ("Dark", False, "Dark"),
        ("Light", True, "Focus · Light"),
        ("Dark", True, "Focus · Dark"),
    ]
    current_view = (st.session_state.theme_mode, st.session_state.focus_mode)
    current_view_index = next((i for i, view in enumerate(view_sequence) if view[:2] == current_view), 0)
    st.markdown("<div class='command-label'>Appearance</div>", unsafe_allow_html=True)
    st.button(
        f"◐ {view_sequence[current_view_index][2]}",
        key="cycle_view_mode_btn",
        on_click=_cb_cycle_view_mode,
        use_container_width=True,
        help="Cycle: Light → Dark → Focus Light → Focus Dark → Light.",
    )

# --- 9. BM25 LEXICAL SEARCH RESULTS VIEW ---
if query_str and cases:

    st.markdown(
        f"<div class='search-heading'>Search results</div><div class='search-snippet'>“{html.escape(query_str)}” · {len(ranked_pairs):,} matching cases</div>",
        unsafe_allow_html=True,
    )
    st.caption("Select a case tile to open its full digest. Results are ranked by relevance; the Filing Cabinet on the left also mirrors the matching cases.")

    if not bm25:
        st.warning("The lexical search index is unavailable. Check that `rank-bm25` is installed and restart the app.")
    elif not ranked_pairs:
        st.info("No exact lexical matches found. Try searching for a broader doctrinal term, G.R. number, or party surname.")
    else:
        for idx, score_val in ranked_pairs[:15]:
            match_case = cases[idx]
            snippet_html = get_search_snippet(match_case.get("digest", ""), query_tokens)
            case_uid = str(match_case.get("_uid", ""))
            case_href = "?open_case=" + quote(case_uid, safe="")
            case_title = html.escape(str(match_case.get("citation", "Untitled Case")))

            card_html = (
                f"<a class='search-card-link' href='{html.escape(case_href, quote=True)}' "
                f"aria-label='Open case digest: {case_title}' target='_self'>"
                "<div class='search-card'>"
                "<div class='search-meta-row'>"
                f"<span class='search-meta-chip'>{html.escape(str(match_case.get('subject', 'General')))}</span>"
                f"<span class='search-meta-chip'>{html.escape(str(match_case.get('topic', 'General')))}</span>"
                f"<span class='search-meta-chip'>{html.escape(str(match_case.get('lookup_key', '')))}</span>"
                "</div>"
                f"<div class='search-card-title'>{format_inline_markdown(str(match_case.get('citation', 'Untitled Case')), query_tokens)}</div>"
                f"<div class='search-snippet'>{snippet_html}</div>"
                "<div class='search-card-open-hint'>Open case digest ↗</div>"
                "</div></a>"
            )
            st.markdown(card_html, unsafe_allow_html=True)

    st.stop()


# --- 10. MAIN CASE READER & PERSISTENT RIGHT SIDEBAR NAVIGATOR ---
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

reader_container, nav_container = st.columns(
    [3.15, 1.05] if st.session_state.focus_mode else [3.25, 1.15],
    gap="large",
)


# --- 10B. PERSISTENT STICKY RIGHT SIDEBAR: STUDY NAVIGATOR ---
with nav_container:
    # Marker id '#sticky-right-nav-marker' activates CSS position: sticky on this entire column
    toc_links_html = "".join(
        f"<a class='toc-jump-link' href='#{html.escape(sec['anchor'])}' target='_self'>"
        f"<span class='toc-sec-badge'>{html.escape(sec['num'])}</span>"
        f"<span>{html.escape(sec['short_title'][:36] + ('...' if len(sec['short_title']) > 36 else ''))}</span>"
        f"</a>"
        for sec in sections
    )
    top_jump_html = (
        "<a class='toc-jump-link' href='#reader-top-anchor' target='_self' style='margin-top:0.35rem; opacity:0.85;'>"
        "<span class='toc-sec-badge'>↑</span><span>Back to Top of Case</span></a>"
    )
    st.markdown(
        "<div id='sticky-right-nav-marker'></div>"
        "<div class='navigator-card'>"
        "<div class='navigator-title'>🧭 On This Case (Click to Jump)</div>"
        f"<div class='toc-nav-list'>{toc_links_html}{top_jump_html}</div>"
        "</div>",
        unsafe_allow_html=True,
    )

    with st.expander("🧠 Self-Test Recitation Check", expanded=(st.session_state.study_mode == "⚡ Exam & Recit Review")):
        st.caption("Test your recall before looking at the holding:")
        if issues_list:
            st.markdown(f"**Primary Question:**\n{issues_list[0]}")
        else:
            st.markdown(f"**Case:** {active_case.get('citation', '')}")
        if st.checkbox("Reveal Cold-Call Answer & Outcome", key="reveal_recit"):
            ans = meta.get("cold_call") or meta.get("outcome") or "Refer to Section 6 and Section 6.5 in the digest."
            st.success(ans)

    with st.expander("📋 Copy Citation & Verbatim Quotes", expanded=(st.session_state.study_mode == "🔎 Legal Research")):
        cite_block = f"{active_case.get('citation', '')}"
        if meta.get("citation_metadata"):
            cite_block += f" ({meta['citation_metadata']})"
        if quotes:
            cite_block += "\n\n" + "\n\n".join(quotes[:2])
        st.code(cite_block, language="markdown")



# --- 10A. CENTRAL READING PANE ---
with reader_container:
    st.markdown("<div id='reader-top-anchor'></div>", unsafe_allow_html=True)

    # --- BUILT-IN DUAL AUDIO BRIEFING PLAYER (en-US-AndrewMultilingualNeural) ---
    primer_mp3_path, chapter_mp3_path = get_audio_file_paths(active_case)
    primer_script_txt, chapter_script_txt = build_audio_scripts_for_case(active_case)

    with st.expander("🎧 Audio case briefings", expanded=False):
        aud_col1, aud_col2 = st.columns(2, gap="medium")

        with aud_col1:
            render_audio_briefing(
                title="⚡ 1. 60–90s Cold-Call Primer",
                description="9-step primary-issue flow: law, factual trigger, issue, arguments, ruling, ratio, doctrine, and outcome.",
                script_text=primer_script_txt,
                output_path=primer_mp3_path,
                widget_key=f"audio_primer_{active_case['_uid']}",
                spinner_text="Synthesizing the cold-call primer…",
            )

        with aud_col2:
            render_audio_briefing(
                title="📖 2. Complete Audio Chapter",
                description="Full walkthrough of the primary issue, secondary issues, fallo, separate opinions, and final reckoning point.",
                script_text=chapter_script_txt,
                output_path=chapter_mp3_path,
                widget_key=f"audio_chapter_{active_case['_uid']}",
                spinner_text="Synthesizing the complete audio chapter…",
            )

        with st.expander("📜 View Spoken Audio Transcripts (Primer & Plain-Language Chapter)", expanded=False):
            t_tab1, t_tab2 = st.tabs(["⚡ 60–90s Primer Script", "📖 Complete Chapter Script"])
            with t_tab1:
                st.text(primer_script_txt)
            with t_tab2:
                st.text(chapter_script_txt)

    subj_label = active_case.get("subject", "General Jurisprudence") or "General Jurisprudence"
    topic_label = active_case.get("topic", "General Topic") or "General Topic"
    gr_label = active_case.get("lookup_key", "")
    date_cat = str(active_case.get("date_cataloged") or "Unknown")[:10]
    src_url = safe_external_url(active_case.get("url", ""))
    source_link_html = (
        f"<a class='source-link' href='{html.escape(src_url)}' target='_blank' rel='noopener noreferrer'>🔗 View Original Supreme Court Decision ↗</a>"
        if src_url
        else "<span>📄 Offline Local Digest</span>"
    )

    mode_banner_html = ""
    if st.session_state.study_mode == "⚡ Exam & Recit Review":
        trigger_txt = meta.get("factual_trigger", "See Section 2 & Section 3 below for factual trigger.")
        cold_call_txt = meta.get("cold_call", "See Section 6 & Section 6.5 below for recitation synthesis.")
        magic_txt = meta.get("magic_words", "")
        outcome_txt = meta.get("outcome") or meta.get("fallo", "")

        magic_html = f"<p class='digest-p'><strong class='inline-lead'>Terms of Art (Magic Words):</strong> {format_inline_markdown(magic_txt)}</p>" if magic_txt else ""
        disp_html = f"<p class='digest-p' style='margin-bottom:0;'><strong class='inline-lead'>Disposition:</strong> {format_inline_markdown(outcome_txt)}</p>" if outcome_txt else ""

        mode_banner_html = (
            "<div class='recit-callout' style='margin-bottom: 1.75rem;'>"
            "<div style='font-family: var(--font-ui); font-size: 0.8rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--accent-navy); margin-bottom: 0.5rem;'>⚡ Rapid Recitation &amp; Exam Review Deck</div>"
            f"<p class='digest-p'><strong class='inline-lead'>Factual Trigger:</strong> {format_inline_markdown(trigger_txt)}</p>"
            f"<p class='digest-p'><strong class='inline-lead'>30-Second Cold-Call Synthesis:</strong> {format_inline_markdown(cold_call_txt)}</p>"
            f"{magic_html}"
            f"{disp_html}"
            "</div>"
        )

    elif st.session_state.study_mode == "🔎 Legal Research":
        posture_txt = meta.get("procedural_posture", "See Section 1.")
        topic_txt = meta.get("primary_topic", f"{subj_label} → {topic_label}")
        quotes_html = "".join(
            f"<div class='quote-callout'><div class='quote-callout-label'>📜 Verbatim Doctrinal Holding #{i+1}</div><div class='quote-callout-body'>{format_inline_markdown(q)}</div></div>"
            for i, q in enumerate(quotes[:3])
        )
        mode_banner_html = (
            "<div class='fallo-box' style='margin-bottom: 1.75rem;'>"
            "<div class='fallo-title'>🔎 Legal Practitioner &amp; Argument Research Brief</div>"
            f"<p class='digest-p'><strong class='inline-lead'>Primary Doctrinal Classification:</strong> {format_inline_markdown(topic_txt)}</p>"
            f"<p class='digest-p'><strong class='inline-lead'>Procedural Vehicle:</strong> {format_inline_markdown(posture_txt)}</p>"
            f"{quotes_html}"
            "</div>"
        )

    sections_html_list: List[str] = []
    for sec in sections:
        sec_body_html = render_section_lines_to_html(sec["lines"], sec["num"])
        sections_html_list.append(
            f"<div class='digest-section-block' id='{sec['anchor']}'>"
            f"<div class='digest-section-header'><span class='sec-num-badge'>{html.escape(sec['num'])}</span><h3 class='sec-heading-text'>{html.escape(sec['short_title'])}</h3></div>"
            f"<div class='legal-prose'>{sec_body_html}</div>"
            "</div>"
        )

    full_sections_html = "".join(sections_html_list)
    gr_pill_html = f"<span class='gr-badge'>{html.escape(gr_label)}</span>" if gr_label else ""

    reader_html = (
        "<div class='reader-sheet'>"
        "<div class='reader-eyebrow'>Supreme Court decision · Research file</div>"
        f"<div class='breadcrumb-bar'>"
        f"<span class='subj-badge'>🏛️ {html.escape(subj_label)}</span>"
        f"<span class='cat-badge'>📁 {html.escape(topic_label)}</span>"
        f"{gr_pill_html}"
        f"</div>"
        f"<h1 class='case-main-title'>{html.escape(active_case.get('citation', 'Untitled Case'))}</h1>"
        f"<div class='case-meta-row'><span>Cataloged {html.escape(date_cat)}</span><span>·</span>{source_link_html}</div>"
        f"{mode_banner_html}"
        f"{full_sections_html}"
        "</div>"
    )
    st.markdown(reader_html, unsafe_allow_html=True)

# Small same-origin helper: smooth case jumps and keep the right navigator's active
# section synchronized with the reading position. The visible menu control itself is
# a native Streamlit button, so it does not depend on Streamlit's internal DOM selectors.
scroll_target_id = st.session_state.scroll_to_anchor
st.session_state.scroll_to_anchor = ""
import streamlit.components.v1 as components
components.html(
    f"""
    <script>
    (function() {{
      const parentDoc = window.parent.document;
      const targetId = {__import__('json').dumps(scroll_target_id)};
      if (targetId) {{
        const target = parentDoc.getElementById(targetId);
        if (target) target.scrollIntoView({{behavior: 'smooth', block: 'start'}});
      }}

      const links = Array.from(parentDoc.querySelectorAll('a.toc-jump-link[href^="#"]'));
      links.forEach(link => {{
        if (link.dataset.editorialBound === '1') return;
        link.dataset.editorialBound = '1';
        link.addEventListener('click', event => {{
          const id = link.getAttribute('href').slice(1);
          const target = parentDoc.getElementById(id);
          if (target) {{
            event.preventDefault();
            target.scrollIntoView({{behavior: 'smooth', block: 'start'}});
          }}
        }});
      }});

      if ('IntersectionObserver' in window.parent) {{
        const sections = Array.from(parentDoc.querySelectorAll('.digest-section-block[id]'));
        const linkById = new Map(links.map(link => [link.getAttribute('href').slice(1), link]));
        const observer = new window.parent.IntersectionObserver(entries => {{
          const visible = entries.filter(entry => entry.isIntersecting)
            .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
          if (!visible.length) return;
          links.forEach(link => link.classList.remove('active'));
          const activeLink = linkById.get(visible[0].target.id);
          if (activeLink) activeLink.classList.add('active');
        }}, {{root: null, rootMargin: '-12% 0px -72% 0px', threshold: 0}});
        sections.forEach(section => observer.observe(section));
      }}
    }})();
    </script>
    """,
    height=1,
    width=1,
    scrolling=False,
)
