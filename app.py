import html
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import streamlit as st
import streamlit.components.v1 as components
from rank_bm25 import BM25Okapi

# --- 1. PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Philippine Jurisprudence Library",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

hide_complete_header = """
<style>
div[data-testid="stToolbar"] { visibility: hidden; height: 0%; position: fixed; }
div[data-testid="stDecoration"] { visibility: hidden; height: 0%; position: fixed; }
div[data-testid="stStatusWidget"] { visibility: hidden; height: 0%; position: fixed; }
header { visibility: hidden; height: 0%; }
</style>
"""
st.markdown(hide_complete_header, unsafe_allow_html=True)

# --- 2. SESSION STATE INITIALIZATION ---
if "theme_mode" not in st.session_state:
    st.session_state.theme_mode = "Light"
if "focus_mode" not in st.session_state:
    st.session_state.focus_mode = False
if "sidebar_hidden" not in st.session_state:
    st.session_state.sidebar_hidden = False
if "study_mode" not in st.session_state:
    st.session_state.study_mode = "📖 First-Time Read"
if "font_scale" not in st.session_state:
    st.session_state.font_scale = "Standard"
if "selected_lookup_key" not in st.session_state:
    st.session_state.selected_lookup_key = None
if "selected_subject" not in st.session_state:
    st.session_state.selected_subject = "-- All Subjects --"
if "selected_topic" not in st.session_state:
    st.session_state.selected_topic = "-- All Topics --"
if "active_search_query" not in st.session_state:
    st.session_state.active_search_query = ""
if "scroll_to_anchor" not in st.session_state:
    st.session_state.scroll_to_anchor = ""


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



# --- 4B. AUTOMATED AUDIO CASE BRIEFING ENGINE (Edge Neural TTS: en-US-AndrewMultilingualNeural) ---
DEFAULT_AUDIO_VOICE = "en-US-AndrewMultilingualNeural"
AUDIO_DIR = Path("case_library/audio_briefs")


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
    p_path = Path(p_custom) if (p_custom and Path(p_custom).exists()) else (AUDIO_DIR / f"{stem}_primer.mp3")
    c_path = Path(c_custom) if (c_custom and Path(c_custom).exists()) else (AUDIO_DIR / f"{stem}_chapter.mp3")
    return p_path, c_path


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
    import asyncio
    import edge_tts

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async def _run_tts():
            communicate = edge_tts.Communicate(text=script_text, voice=voice, rate=rate)
            await communicate.save(str(output_path))

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_run_tts())
        finally:
            loop.close()
        return True, str(output_path)
    except Exception as exc:
        return False, str(exc)


# --- 5. THEME, WIDGET CONTRAST & RESPONSIVE CSS INJECTION ---
def inject_custom_css(theme: str, focus_mode: bool, sidebar_hidden: bool, font_scale: str):
    is_dark = theme == "Dark"

    scale_map = {
        "Compact": ("15.5px", "26px", "18.5px"),
        "Standard": ("17.5px", "31px", "20.5px"),
        "Comfortable": ("20px", "35px", "23px"),
    }
    body_px, title_px, h3_px = scale_map.get(font_scale, ("17.5px", "31px", "20.5px"))

    if is_dark:
        colors = {
            "--bg-app": "#0F172A",
            "--bg-surface": "#1E293B",
            "--bg-elevated": "#263349",
            "--bg-sidebar": "#131C2E",
            "--bg-header": "#0B1120",
            "--bg-input": "#1E293B",
            "--text-primary": "#F1F5F9",
            "--text-secondary": "#CBD5E1",
            "--text-muted": "#94A3B8",
            "--accent-navy": "#93C5FD",
            "--accent-primary": "#60A5FA",
            "--accent-sage": "#5EEAD4",
            "--accent-gold": "#EAB308",
            "--border-subtle": "#334155",
            "--callout-blue-bg": "rgba(59, 130, 246, 0.15)",
            "--callout-green-bg": "rgba(20, 184, 166, 0.14)",
            "--callout-gold-bg": "rgba(234, 179, 8, 0.15)",
            "--mark-bg": "rgba(250, 204, 21, 0.35)",
            "--mark-text": "#FEF08A",
            "--btn-bg": "#1E293B",
            "--btn-text": "#F8FAFC",
            "--btn-border": "#475569",
            "--btn-hover-bg": "#334155",
            "--shadow-card": "0 8px 24px rgba(0, 0, 0, 0.38)",
        }
    else:
        colors = {
            "--bg-app": "#F6F5F1",
            "--bg-surface": "#FFFFFF",
            "--bg-elevated": "#F9F8F5",
            "--bg-sidebar": "#F0F1EF",
            "--bg-header": "#172C3F",
            "--bg-input": "#FFFFFF",
            "--text-primary": "#25313B",
            "--text-secondary": "#334155",
            "--text-muted": "#64748B",
            "--accent-navy": "#172C3F",
            "--accent-primary": "#1E3A5F",
            "--accent-sage": "#56716D",
            "--accent-gold": "#C6A66A",
            "--border-subtle": "#DCE0E5",
            "--callout-blue-bg": "#EAF1F8",
            "--callout-green-bg": "#EAF3EE",
            "--callout-gold-bg": "#F7F0E5",
            "--mark-bg": "#FEF08A",
            "--mark-text": "#172C3F",
            "--btn-bg": "#FFFFFF",
            "--btn-text": "#172C3F",
            "--btn-border": "#CBD5E1",
            "--btn-hover-bg": "#EAF1F8",
            "--shadow-card": "0 2px 12px rgba(23, 44, 63, 0.06)",
        }

    css_vars = "\n".join(f"        {k}: {v};" for k, v in colors.items())

    sidebar_visibility_css = ""
    if focus_mode or sidebar_hidden:
        sidebar_visibility_css = """
        section[data-testid="stSidebar"] { display: none !important; }
        """
    if focus_mode:
        sidebar_visibility_css += """
        .reader-sheet { max-width: 820px !important; margin: 0 auto !important; }
        """

    st.markdown(
        f"""<style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400&family=IBM+Plex+Mono:wght@400;500&display=swap');

        html {{ scroll-behavior: smooth; }}

        :root {{
{css_vars}
            --font-ui: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            --font-serif: 'Source Serif 4', Georgia, Cambria, serif;
            --font-mono: 'IBM Plex Mono', monospace;
            --body-size: {body_px};
            --title-size: {title_px};
            --h3-size: {h3_px};
        }}

        .stApp {{
            background-color: var(--bg-app) !important;
            color: var(--text-primary) !important;
            font-family: var(--font-ui) !important;
        }}

        .block-container {{
            padding-top: 1.2rem !important;
            padding-bottom: 4rem !important;
            max-width: 1440px !important;
        }}

        section[data-testid="stSidebar"] {{
            background-color: var(--bg-sidebar) !important;
            border-right: 1px solid var(--border-subtle) !important;
        }}
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] span,
        section[data-testid="stSidebar"] h1,
        section[data-testid="stSidebar"] h2,
        section[data-testid="stSidebar"] h3,
        section[data-testid="stSidebar"] h4,
        section[data-testid="stSidebar"] h5 {{
            color: var(--text-primary) !important;
        }}

        .stButton > button,
        .stPopover > div > button {{
            background-color: var(--btn-bg) !important;
            color: var(--btn-text) !important;
            border: 1px solid var(--btn-border) !important;
            border-radius: 7px !important;
            font-family: var(--font-ui) !important;
            font-weight: 600 !important;
            font-size: 0.84rem !important;
            transition: all 0.15s ease !important;
        }}
        .stButton > button:hover,
        .stPopover > div > button:hover {{
            background-color: var(--btn-hover-bg) !important;
            border-color: var(--accent-navy) !important;
            color: var(--accent-navy) !important;
        }}
        .stButton > button p, .stButton > button span {{
            color: inherit !important;
        }}

        div[data-baseweb="input"] > div,
        div[data-baseweb="select"] > div {{
            background-color: var(--bg-input) !important;
            color: var(--text-primary) !important;
            border-color: var(--btn-border) !important;
            border-radius: 7px !important;
        }}
        div[data-baseweb="input"] input {{
            color: var(--text-primary) !important;
            -webkit-text-fill-color: var(--text-primary) !important;
        }}
        div[data-baseweb="input"] input::placeholder {{
            color: var(--text-muted) !important;
            -webkit-text-fill-color: var(--text-muted) !important;
            opacity: 1 !important;
        }}
        div[data-baseweb="select"] span,
        div[data-baseweb="select"] div {{
            color: var(--text-primary) !important;
        }}
        ul[data-baseweb="menu"], div[data-baseweb="popover"] {{
            background-color: var(--bg-surface) !important;
            color: var(--text-primary) !important;
            border: 1px solid var(--border-subtle) !important;
        }}
        li[role="option"] {{
            background-color: var(--bg-surface) !important;
            color: var(--text-primary) !important;
        }}
        li[role="option"]:hover {{
            background-color: var(--btn-hover-bg) !important;
        }}

        details[data-testid="stExpander"] {{
            background-color: var(--bg-surface) !important;
            border: 1px solid var(--border-subtle) !important;
            border-radius: 8px !important;
        }}
        details[data-testid="stExpander"] summary,
        details[data-testid="stExpander"] summary p,
        details[data-testid="stExpander"] summary span,
        details[data-testid="stExpander"] div {{
            color: var(--text-primary) !important;
        }}

        .library-masthead {{
            background-color: var(--bg-header);
            color: #FFFFFF;
            padding: 1.15rem 1.75rem;
            border-radius: 10px;
            margin-bottom: 1.15rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 1rem;
            border-bottom: 3px solid var(--accent-gold);
            box-shadow: var(--shadow-card);
        }}
        .masthead-brand {{ display: flex; align-items: center; gap: 0.85rem; }}
        .masthead-icon {{ font-size: 1.75rem; line-height: 1; }}
        .masthead-title {{
            font-family: var(--font-serif);
            font-size: 1.45rem;
            font-weight: 700;
            color: #FFFFFF !important;
            margin: 0;
        }}
        .masthead-subtitle {{
            font-family: var(--font-ui);
            font-size: 0.78rem;
            color: #CBD5E1 !important;
            margin: 0;
        }}
        .masthead-stats {{ display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; }}
        .stat-pill {{
            background: rgba(255, 255, 255, 0.12);
            border: 1px solid rgba(255, 255, 255, 0.22);
            color: #F8FAFC !important;
            font-family: var(--font-mono);
            font-size: 0.75rem;
            padding: 0.3rem 0.7rem;
            border-radius: 6px;
        }}

        .reader-sheet {{
            background-color: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 2.75rem 3.25rem;
            max-width: 780px;
            margin: 0 auto 2rem auto;
            box-shadow: var(--shadow-card);
            color: var(--text-primary);
        }}

        .breadcrumb-bar {{
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 0.5rem;
            margin-bottom: 0.9rem;
        }}
        .subj-badge {{
            background-color: var(--bg-header);
            color: #FFFFFF !important;
            font-family: var(--font-ui);
            font-size: 0.75rem;
            font-weight: 600;
            padding: 0.25rem 0.7rem;
            border-radius: 6px;
        }}
        .cat-badge {{
            background-color: var(--callout-blue-bg);
            color: var(--accent-navy) !important;
            border: 1px solid var(--border-subtle);
            font-family: var(--font-ui);
            font-size: 0.76rem;
            font-weight: 600;
            padding: 0.25rem 0.7rem;
            border-radius: 6px;
        }}
        .gr-badge {{
            background-color: var(--callout-gold-bg);
            color: var(--text-primary) !important;
            font-family: var(--font-mono);
            font-size: 0.75rem;
            font-weight: 500;
            padding: 0.25rem 0.65rem;
            border-radius: 6px;
        }}

        .reader-sheet h1.case-main-title {{
            font-family: var(--font-serif) !important;
            font-size: {title_px} !important;
            font-weight: 700 !important;
            color: var(--accent-navy) !important;
            line-height: 1.25 !important;
            margin: 0.25rem 0 0.75rem 0 !important;
            letter-spacing: -0.015em !important;
        }}
        .case-meta-row {{
            font-family: var(--font-ui);
            font-size: 0.84rem;
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
        .source-link:hover {{ text-decoration: underline; }}

        .digest-section-block {{
            margin-top: 2.1rem;
            padding-top: 0.25rem;
            scroll-margin-top: 85px;
        }}
        .digest-section-header {{
            display: flex;
            align-items: baseline;
            gap: 0.65rem;
            border-bottom: 2px solid var(--border-subtle);
            padding-bottom: 0.55rem;
            margin-bottom: 1.1rem;
        }}
        .sec-num-badge {{
            background-color: var(--bg-header);
            color: #FFFFFF !important;
            font-family: var(--font-mono);
            font-size: 0.78rem;
            font-weight: 600;
            padding: 0.2rem 0.55rem;
            border-radius: 5px;
            flex-shrink: 0;
        }}
        .reader-sheet h3.sec-heading-text {{
            font-family: var(--font-ui) !important;
            font-size: {h3_px} !important;
            font-weight: 700 !important;
            color: var(--accent-navy) !important;
            margin: 0 !important;
            line-height: 1.3 !important;
        }}

        .reader-sheet .legal-prose,
        .reader-sheet .digest-p,
        .reader-sheet .timeline-event,
        .reader-sheet .num-content,
        .reader-sheet .trigger-text,
        .reader-sheet .fallo-text,
        .reader-sheet .quote-callout-body,
        .reader-sheet .recit-callout {{
            font-family: var(--font-serif) !important;
            font-size: {body_px} !important;
            line-height: 1.72 !important;
            color: var(--text-primary) !important;
            text-align: left !important;
        }}
        .digest-p {{ margin: 0 0 0.95rem 0 !important; }}
        .inline-lead {{
            font-family: var(--font-ui) !important;
            font-size: 0.92em !important;
            color: var(--accent-navy) !important;
            font-weight: 700 !important;
        }}
        .digest-subheading {{
            font-family: var(--font-ui) !important;
            font-size: 1.05em !important;
            font-weight: 600 !important;
            color: var(--accent-navy) !important;
            margin: 1.25rem 0 0.5rem 0 !important;
        }}
        .party-header {{
            font-family: var(--font-ui) !important;
            font-size: 0.95rem !important;
            font-weight: 700 !important;
            color: var(--accent-navy) !important;
            background-color: var(--bg-elevated);
            border-left: 3px solid var(--accent-sage);
            padding: 0.5rem 0.9rem;
            margin: 1.1rem 0 0.7rem 0;
            border-radius: 0 6px 6px 0;
        }}

        .trigger-box {{
            background-color: var(--callout-green-bg);
            border-left: 4px solid var(--accent-sage);
            padding: 1rem 1.25rem;
            border-radius: 0 8px 8px 0;
            margin: 1rem 0 1.35rem 0;
        }}
        .trigger-title {{
            font-family: var(--font-ui) !important;
            font-size: 0.82rem !important;
            font-weight: 700 !important;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--accent-sage) !important;
            margin-bottom: 0.35rem;
        }}

        .quote-callout, .legal-blockquote {{
            background-color: var(--callout-gold-bg);
            border-left: 4px solid var(--accent-gold);
            padding: 0.95rem 1.25rem;
            border-radius: 0 8px 8px 0;
            margin: 0.95rem 0;
            font-style: italic;
        }}
        .quote-callout-label {{
            font-family: var(--font-ui) !important;
            font-style: normal !important;
            font-size: 0.78rem !important;
            font-weight: 700 !important;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--accent-navy) !important;
            margin-bottom: 0.35rem;
        }}

        .recit-callout {{
            background-color: var(--callout-blue-bg);
            border-left: 4px solid var(--accent-primary);
            padding: 0.95rem 1.2rem;
            border-radius: 0 8px 8px 0;
            margin: 0.85rem 0 1.25rem 0;
        }}
        .recit-label {{
            font-family: var(--font-ui) !important;
            font-size: 0.86rem !important;
            font-weight: 700 !important;
            color: var(--accent-navy) !important;
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
            font-family: var(--font-ui) !important;
            font-weight: 700 !important;
            color: var(--accent-navy) !important;
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
            font-family: var(--font-ui) !important;
            font-size: 0.82rem !important;
            font-weight: 700 !important;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--accent-navy) !important;
            margin-bottom: 0.4rem;
        }}

        .timeline-row {{
            display: grid;
            grid-template-columns: 165px 1fr;
            gap: 1rem;
            padding: 0.65rem 0;
            border-bottom: 1px dashed var(--border-subtle);
            align-items: baseline;
        }}
        .timeline-date {{
            font-family: var(--font-ui) !important;
            font-size: 0.88rem !important;
            font-weight: 700 !important;
            color: var(--accent-navy) !important;
        }}

        .numbered-item {{
            display: flex;
            gap: 0.75rem;
            align-items: flex-start;
            margin-bottom: 0.85rem;
        }}
        .num-circle {{
            font-family: var(--font-mono) !important;
            font-size: 0.82rem !important;
            font-weight: 600 !important;
            color: var(--accent-navy) !important;
            background-color: var(--callout-blue-bg);
            padding: 0.15rem 0.5rem;
            border-radius: 5px;
            flex-shrink: 0;
            margin-top: 0.15rem;
        }}
        .issue-pill {{
            font-family: var(--font-ui) !important;
            font-size: 0.7rem !important;
            font-weight: 700 !important;
            padding: 0.15rem 0.5rem;
            border-radius: 4px;
            margin-right: 0.4rem;
            vertical-align: middle;
        }}
        .issue-primary {{ background-color: var(--accent-gold); color: #FFFFFF !important; }}
        .issue-secondary {{ background-color: var(--callout-blue-bg); color: var(--accent-navy) !important; }}

        .navigator-card {{
            background-color: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            padding: 1rem 1.1rem;
            box-shadow: var(--shadow-card);
            margin-bottom: 0.75rem;
        }}
        .navigator-title {{
            font-family: var(--font-ui);
            font-size: 0.82rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--accent-navy);
            margin-bottom: 0.5rem;
            padding-bottom: 0.45rem;
            border-bottom: 2px solid var(--border-subtle);
        }}

        mark.search-mark {{
            background-color: var(--mark-bg);
            color: var(--mark-text);
            padding: 0.05rem 0.25rem;
            border-radius: 3px;
            font-weight: 600;
        }}
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

        @media (max-width: 992px) {{
            .reader-sheet {{ padding: 1.75rem 1.5rem; max-width: 100%; }}
        }}
        @media (max-width: 640px) {{
            .block-container {{ padding-left: 0.75rem !important; padding-right: 0.75rem !important; }}
            .library-masthead {{ padding: 0.9rem 1.1rem; }}
            .masthead-title {{ font-size: 1.18rem; }}
            .reader-sheet {{ padding: 1.25rem 1.05rem; border-radius: 8px; }}
            .reader-sheet h1.case-main-title {{ font-size: 1.45rem !important; }}
            .timeline-row {{ grid-template-columns: 1fr; gap: 0.2rem; }}
        }}

        {sidebar_visibility_css}
        </style>""",
        unsafe_allow_html=True,
    )


# --- 6. LOAD LIBRARY DATA ---
cases, bm25, tokenized_corpus = load_library()

if cases and not st.session_state.selected_lookup_key:
    st.session_state.selected_lookup_key = cases[0]["_uid"]

inject_custom_css(
    st.session_state.theme_mode,
    st.session_state.focus_mode,
    st.session_state.sidebar_hidden,
    st.session_state.font_scale,
)


# --- 7. SIDEBAR: 2-TIER FILING CABINET (SUBJECT -> TOPIC -> CASES) ---
with st.sidebar:
    st.markdown("### 🗄️ Filing Cabinet")
    st.caption("2-Tier Jurisprudence Repository (Subject → Topic)")

    if not cases:
        st.warning("No cases found in `case_library/master_library.jsonl`. Run the Desktop Generator or import a `.docx` casebook.")
    else:
        # Tier 1: Subject Selector
        all_subjects = sorted(list({c.get("subject", "General Jurisprudence") for c in cases}))
        subj_counts = {s: sum(1 for c in cases if c.get("subject") == s) for s in all_subjects}
        subj_options = ["-- All Subjects --"] + all_subjects
        if st.session_state.selected_subject not in subj_options:
            st.session_state.selected_subject = "-- All Subjects --"

        def _fmt_subject(opt: str) -> str:
            if opt == "-- All Subjects --":
                return f"📚 All Subjects ({len(cases)})"
            return f"🏛️ {opt} ({subj_counts.get(opt, 0)})"

        chosen_subj = st.selectbox(
            "Tier 1: Subject / Course",
            options=subj_options,
            index=subj_options.index(st.session_state.selected_subject),
            format_func=_fmt_subject,
        )
        if chosen_subj != st.session_state.selected_subject:
            st.session_state.selected_subject = chosen_subj
            st.session_state.selected_topic = "-- All Topics --"
            st.rerun()

        subj_filtered = cases if chosen_subj == "-- All Subjects --" else [c for c in cases if c.get("subject") == chosen_subj]

        # Tier 2: Topic Selector (Scoped to selected Tier 1 Subject)
        avail_topics = sorted(list({c.get("topic", "General Topic") for c in subj_filtered}))
        top_counts = {t: sum(1 for c in subj_filtered if c.get("topic") == t) for t in avail_topics}
        top_options = ["-- All Topics --"] + avail_topics
        if st.session_state.selected_topic not in top_options:
            st.session_state.selected_topic = "-- All Topics --"

        def _fmt_topic(opt: str) -> str:
            if opt == "-- All Topics --":
                return f"📂 All Topics ({len(subj_filtered)})"
            return f"📁 {opt} ({top_counts.get(opt, 0)})"

        chosen_top = st.selectbox(
            "Tier 2: Syllabus Topic",
            options=top_options,
            index=top_options.index(st.session_state.selected_topic),
            format_func=_fmt_topic,
        )
        st.session_state.selected_topic = chosen_top

        if chosen_top == "-- All Topics --":
            filtered_cases = subj_filtered
        else:
            filtered_cases = [c for c in subj_filtered if c.get("topic") == chosen_top]

        cabinet_filter = st.text_input(
            "Filter list by title or G.R. No.",
            placeholder="e.g., Guevara or 25577",
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
                st.session_state.scroll_to_anchor = ""
                st.rerun()
        else:
            st.info("No cases match that filter.")

        st.divider()
        st.markdown("##### ⚙️ Reader Typography")
        font_choice = st.radio(
            "Typography Size",
            options=["Compact", "Standard", "Comfortable"],
            index=["Compact", "Standard", "Comfortable"].index(st.session_state.font_scale),
            horizontal=True,
        )
        if font_choice != st.session_state.font_scale:
            st.session_state.font_scale = font_choice
            st.rerun()


# --- 8. TOP INSTITUTIONAL MASTHEAD & CONTROLS ---
unique_subjects_count = len({c.get("subject", "General Jurisprudence") for c in cases})
unique_topics_count = len({c.get("topic", "General Topic") for c in cases})
masthead_html = (
    "<div class='library-masthead'>"
    "<div class='masthead-brand'>"
    "<span class='masthead-icon'>⚖️</span>"
    "<div>"
    "<div class='masthead-title'>Philippine Jurisprudence Library</div>"
    "<div class='masthead-subtitle'>Full-Text Substitute Case Digests • 2-Tier Subject &amp; Topic Matrix • Lexical Research Engine</div>"
    "</div>"
    "</div>"
    "<div class='masthead-stats'>"
    f"<span class='stat-pill'>📚 {len(cases)} Cases</span>"
    f"<span class='stat-pill'>🏛️ {unique_subjects_count} Subjects</span>"
    f"<span class='stat-pill'>🗂️ {unique_topics_count} Topics</span>"
    "</div>"
    "</div>"
)
st.markdown(masthead_html, unsafe_allow_html=True)

# Top Control Strip: Left Sidebar Toggle | Search Bar | Study Persona | Text Size | Focus Mode | Light/Dark Mode
ctrl_col0, ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4, ctrl_col5 = st.columns([1.05, 2.65, 1.85, 1.25, 0.95, 0.95], gap="small")

with ctrl_col0:
    sb_label = "📂 Show Cabinet" if (st.session_state.sidebar_hidden or st.session_state.focus_mode) else "📂 Hide Cabinet"
    if st.button(sb_label, use_container_width=True, help="Toggle the Left Filing Cabinet sidebar on or off"):
        if st.session_state.focus_mode:
            st.session_state.focus_mode = False
            st.session_state.sidebar_hidden = False
        else:
            st.session_state.sidebar_hidden = not st.session_state.sidebar_hidden
        st.rerun()

with ctrl_col1:
    search_input = st.text_input(
        "Universal Lexical Search",
        value=st.session_state.active_search_query,
        placeholder="🔍 Search citations, G.R. numbers, subjects, topics, doctrines, Latin maxims...",
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
    size_options = ["Compact", "Standard", "Comfortable"]
    top_font_choice = st.selectbox(
        "Text Size",
        options=size_options,
        index=size_options.index(st.session_state.font_scale),
        format_func=lambda s: f"🔤 Text: {s}",
        label_visibility="collapsed",
    )
    if top_font_choice != st.session_state.font_scale:
        st.session_state.font_scale = top_font_choice
        st.rerun()

with ctrl_col4:
    focus_btn_label = "🧘 Exit Focus" if st.session_state.focus_mode else "🧘 Focus"
    if st.button(focus_btn_label, use_container_width=True, help="Hide both sidebars and center the legal document"):
        st.session_state.focus_mode = not st.session_state.focus_mode
        st.rerun()

with ctrl_col5:
    theme_btn_label = "☀️ Light" if st.session_state.theme_mode == "Dark" else "🌙 Dark"
    if st.button(theme_btn_label, use_container_width=True, help="Toggle between Light Mode and Dark Mode"):
        st.session_state.theme_mode = "Dark" if st.session_state.theme_mode == "Light" else "Light"
        st.rerun()


# --- 9. BM25 LEXICAL SEARCH RESULTS VIEW ---
query_str = st.session_state.active_search_query.strip()
query_tokens = [t for t in re.split(r"\W+", query_str.lower()) if t]

if query_str and bm25 and cases:
    scores = bm25.get_scores(query_tokens)
    ranked_pairs = []
    for idx, base_score in enumerate(scores):
        c_item = cases[idx]
        haystack = (
            f"{c_item.get('citation', '')} {c_item.get('subject', '')} "
            f"{c_item.get('topic', '')} {c_item.get('digest', '')}"
        ).lower()
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

            card_html = (
                "<div class='search-card'>"
                "<div class='breadcrumb-bar'>"
                f"<span class='subj-badge'>🏛️ {html.escape(match_case.get('subject', 'General'))}</span>"
                f"<span class='cat-badge'>📁 {html.escape(match_case.get('topic', 'General'))}</span>"
                f"<span class='gr-badge'>{html.escape(match_case.get('lookup_key', ''))}</span>"
                f"<span class='gr-badge'>BM25 Score: {score_val:.2f}</span>"
                "</div>"
                f"<div style='font-family: var(--font-serif); font-size: 1.25rem; font-weight: 700; color: var(--accent-navy);'>{format_inline_markdown(match_case.get('citation', 'Untitled'), query_tokens)}</div>"
                f"<div class='search-snippet'>{snippet_html}</div>"
                "</div>"
            )
            st.markdown(card_html, unsafe_allow_html=True)

            btn_c1, btn_c2, _ = st.columns([1.4, 2.0, 4.6])
            with btn_c1:
                if st.button("📖 Open Case Digest", key=f"open_search_{match_case['_uid']}", use_container_width=True):
                    st.session_state.selected_subject = match_case.get("subject", "-- All Subjects --")
                    st.session_state.selected_topic = match_case.get("topic", "-- All Topics --")
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

# Mobile / Compact Case Switcher & Section Jumper
with st.expander("📱 Quick Case & Section Jumper (Mobile / Tablet Navigation)", expanded=False):
    m_col1, m_col2 = st.columns(2)
    with m_col1:
        all_uids = [c["_uid"] for c in cases]
        all_labels = {
            c["_uid"]: f"{c.get('citation', '')} [{c.get('subject', '')} | {c.get('topic', '')}]"
            for c in cases
        }
        mob_uid = st.selectbox(
            "Switch Case",
            options=all_uids,
            index=all_uids.index(active_case["_uid"]) if active_case["_uid"] in all_uids else 0,
            format_func=lambda u: all_labels.get(u, u),
        )
        if mob_uid != active_case["_uid"]:
            st.session_state.selected_lookup_key = mob_uid
            st.session_state.scroll_to_anchor = ""
            st.rerun()
    with m_col2:
        if sections:
            sec_opts = ["-- Jump to Section --"] + [f"{s['num']}. {s['short_title']}" for s in sections]
            chosen_mob_sec = st.selectbox("Jump to Section", options=sec_opts)
            if chosen_mob_sec != "-- Jump to Section --":
                idx_s = sec_opts.index(chosen_mob_sec) - 1
                st.session_state.scroll_to_anchor = sections[idx_s]["anchor"]

if st.session_state.focus_mode:
    reader_container = st.container()
    nav_container = None
else:
    reader_container, nav_container = st.columns([3.3, 1.15], gap="large")


# --- 10B. RIGHT SIDEBAR: STUDY NAVIGATOR ---
if nav_container is not None:
    with nav_container:
        st.markdown(
            "<div class='navigator-card'><div class='navigator-title'>🧭 On This Case (Click to Jump)</div></div>",
            unsafe_allow_html=True,
        )
        for sec in sections:
            btn_lbl = f"{sec['num']}. {sec['short_title'][:32] + ('...' if len(sec['short_title']) > 32 else '')}"
            if st.button(btn_lbl, key=f"nav_btn_{sec['anchor']}", use_container_width=True):
                st.session_state.scroll_to_anchor = sec["anchor"]

        st.markdown("<div style='height: 0.75rem;'></div>", unsafe_allow_html=True)

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
    # --- BUILT-IN DUAL AUDIO BRIEFING PLAYER (en-US-AndrewMultilingualNeural) ---
    primer_mp3_path, chapter_mp3_path = get_audio_file_paths(active_case)
    primer_script_txt, chapter_script_txt = build_audio_scripts_for_case(active_case)

    with st.expander("🎧 Audio Case Briefings — Microsoft Edge Neural TTS (AndrewMultilingualNeural)", expanded=True):
        aud_col1, aud_col2 = st.columns(2, gap="medium")

        with aud_col1:
            st.markdown("**⚡ 1. 60–90s Cold-Call Primer**")
            st.caption("9-Step Primary Syllabus Issue flow: Law → Trigger → Bridge → Issue → Arguments → Ruling → Ratio → Doctrine → Outcome.")
            if primer_mp3_path.exists():
                st.audio(str(primer_mp3_path), format="audio/mp3")
                if st.button("🔄 Regenerate Primer MP3", key=f"regen_primer_{active_case['_uid']}", use_container_width=True):
                    with st.spinner("Synthesizing 60–90s Primer with AndrewMultilingualNeural..."):
                        ok_p, msg_p = synthesize_edge_tts_mp3(primer_script_txt, primer_mp3_path, voice=DEFAULT_AUDIO_VOICE)
                        if ok_p:
                            st.rerun()
                        else:
                            st.error(f"TTS Error: {msg_p}")
            else:
                if st.button("🔊 Generate & Play 60–90s Primer", key=f"gen_primer_{active_case['_uid']}", use_container_width=True):
                    with st.spinner("Synthesizing 60–90s Primer with AndrewMultilingualNeural..."):
                        ok_p, msg_p = synthesize_edge_tts_mp3(primer_script_txt, primer_mp3_path, voice=DEFAULT_AUDIO_VOICE)
                        if ok_p:
                            st.rerun()
                        else:
                            st.error(f"TTS Error: {msg_p}")

        with aud_col2:
            st.markdown("**📖 2. Complete Audio Chapter (Plain-Language Emphasis)**")
            st.caption("13-Step Full Walkthrough: Resolves Primary Issue (Steps 1–9) + Transition + Secondary Issues, Fallo, Separate Opinions & Reckoning Point (Steps 10–13).")
            if chapter_mp3_path.exists():
                st.audio(str(chapter_mp3_path), format="audio/mp3")
                if st.button("🔄 Regenerate Chapter MP3", key=f"regen_chap_{active_case['_uid']}", use_container_width=True):
                    with st.spinner("Synthesizing Complete Plain-Language Chapter with AndrewMultilingualNeural..."):
                        ok_c, msg_c = synthesize_edge_tts_mp3(chapter_script_txt, chapter_mp3_path, voice=DEFAULT_AUDIO_VOICE)
                        if ok_c:
                            st.rerun()
                        else:
                            st.error(f"TTS Error: {msg_c}")
            else:
                if st.button("🔊 Generate & Play Complete Audio Chapter", key=f"gen_chap_{active_case['_uid']}", use_container_width=True):
                    with st.spinner("Synthesizing Complete Plain-Language Chapter with AndrewMultilingualNeural..."):
                        ok_c, msg_c = synthesize_edge_tts_mp3(chapter_script_txt, chapter_mp3_path, voice=DEFAULT_AUDIO_VOICE)
                        if ok_c:
                            st.rerun()
                        else:
                            st.error(f"TTS Error: {msg_c}")

        with st.expander("📜 View Spoken Audio Transcripts (Primer & Plain-Language Chapter)", expanded=False):
            t_tab1, t_tab2 = st.tabs(["⚡ 60–90s Primer Script", "📖 Complete Chapter Script"])
            with t_tab1:
                st.text(primer_script_txt)
            with t_tab2:
                st.text(chapter_script_txt)

    subj_label = active_case.get("subject", "General Jurisprudence") or "General Jurisprudence"
    topic_label = active_case.get("topic", "General Topic") or "General Topic"
    gr_label = active_case.get("lookup_key", "")
    date_cat = active_case.get("date_cataloged", "Unknown")[:10]
    src_url = active_case.get("url", "")
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
        f"<div class='breadcrumb-bar'>"
        f"<span class='subj-badge'>🏛️ {html.escape(subj_label)}</span>"
        f"<span class='cat-badge'>📁 {html.escape(topic_label)}</span>"
        f"{gr_pill_html}"
        f"</div>"
        f"<h1 class='case-main-title'>{html.escape(active_case.get('citation', 'Untitled Case'))}</h1>"
        f"<div class='case-meta-row'><span>🗓️ Cataloged: <strong>{html.escape(date_cat)}</strong></span><span>•</span>{source_link_html}</div>"
        f"{mode_banner_html}"
        f"{full_sections_html}"
        "</div>"
    )
    st.markdown(reader_html, unsafe_allow_html=True)

# Execute smooth JavaScript scroll into view in parent DOM when a section button is clicked
if st.session_state.scroll_to_anchor:
    target_id = st.session_state.scroll_to_anchor
    components.html(
        f"""
        <script>
        (function() {{
            const targetId = {json.dumps(target_id)};
            const parentDoc = window.parent.document;
            const el = parentDoc.getElementById(targetId);
            if (el) {{
                el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
            }}
        }})();
        </script>
        """,
        height=0,
    )
    st.session_state.scroll_to_anchor = ""
