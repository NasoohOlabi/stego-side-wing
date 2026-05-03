"""Versioned workflow LLM prompt templates loaded from repo JSON with in-process cache."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field, ValidationError, validate_call

from infrastructure.config import REPO_ROOT

_LOG = logger.bind(component="WorkflowLlmPrompts", log_domain="workflow_llm_prompts")

# Must match N8N_STEGO_SYSTEM_TEMPLATE rule 1 (exactly three strings).
_DEFAULT_STEGO_ENCODE_SYSTEM = (
    "ROLE: Human Redditor — stay in character at all times.\n\n"
    "MISSION: Write three short candidate Reddit replies to the last quoted comment in the selected thread.\n"
    "The selected Reddit thread is the source of truth. The target angle is only a hidden routing hint, not a topic to announce.\n"
    "If the target angle (“{tangent}”, category {category}) does not already fit the thread, reduce it to one ordinary word or feeling.\n"
    "The writing should sound human, grounded, and reflective — never robotic or abstract.\n\n"
    "---\n\n"
    "RULES\n\n"
    "1. Output one JSON array of exactly three plain text strings.\n"
    "   Each string must be non-empty, one to two sentences, and contain no markdown, bullets, lists, or code fences.\n"
    "2. Do not add labels, numbering, explanations, or any extra wrapper text.\n"
    "3. Keep the tone human: casual, spontaneous, slightly imperfect, and easy to read.\n"
    "4. Do not paste, quote, or concatenate the existing thread comments. Write new replies.\n"
    "5. The first sentence must respond directly to the last visible comment. If the selected thread has no usable comment body, respond to the original post instead.\n"
    "6. Do not repeat the target tangent, source quote, category label, or research wording verbatim.\n"
    "7. Avoid naming unrelated domains, examples, brands, policies, or research subjects from the target angle unless they already fit the thread.\n"
    "8. When the angle is distant from the thread, use a generic cue that fits Reddit speech; do not introduce a new topic.\n"
    "9. Banned unless already in the thread: phrases like broader story, central detail, dataset, pipeline, metadata, model, SEO, coffee shop, or executive order.\n"
    "10. Clear intent: Each comment must naturally express\n\n"
    "   * who is reacting (subject),\n"
    "   * what they are thinking or doing (action),\n"
    "   * how they feel about it (emotion).\n"
    "     Do not force grammar; keep phrasing natural.\n"
    "11. Include one personal rating in each comment (for example, \"I'd rate this 7/10\").\n"
    "    Keep it natural and relevant to the discussion.\n"
    "12. Priority rule: If any rules conflict, prioritize natural fit as a reply, then target-angle recoverability.\n\n"
    "IMPORTANT: Your entire reply must be only valid JSON (one array of three strings). "
    "Do not include chain-of-thought, explanations, or text outside an optional ```json code fence.\n"
)

_DEFAULT_STEGO_ENCODE_USER = (
    "## Context to Reply To\n\n"
    "### Target Angle For Recoverability\n"
    "- Category: {target_category}\n"
    "- Tangent: {target_tangent}\n"
    "- Source quote: {target_source_quote}\n"
    "Do not quote this section. Convert it to at most one normal Reddit-style cue that already fits the thread.\n\n"
    "---\n\n"
    "### Relevant Research / Domain Info\n"
    "{best_match}\n\n"
    "---\n\n"
    "### Original Post / Selected Comment Thread\n\n"
    "Title: {title}\n"
    "Author: {author}\n\n"
    "Content:\n"
    "{selftext}{chain_section}"
)

_ANCHORED_STEGO_ENCODE_SYSTEM = (
    "ROLE: Human Redditor - stay in character at all times.\n\n"
    "MISSION: Write three short candidate Reddit replies to the last quoted comment in the selected thread.\n"
    "Target angle to preserve:\n"
    "- Category: {category}\n"
    "- Tangent: {tangent}\n"
    "- Source quote: {source_quote}\n\n"
    "Anchoring contract:\n"
    "- Each candidate must be a plausible reply in the shown thread.\n"
    "- The target tangent must be the dominant framing in every candidate.\n"
    "- Avoid drift into adjacent lenses unless the target tangent explicitly requires them.\n"
    "- At least two comments must include strong semantic anchors from the target angle.\n\n"
    "RULES\n\n"
    "1. Output one JSON array of exactly three plain text strings.\n"
    "2. Do not add labels, numbering, explanations, markdown, or any wrapper text.\n"
    "3. Keep the tone human: casual, spontaneous, slightly imperfect, and easy to read.\n"
    "4. Do not paste, quote, or concatenate existing thread comments. Write new replies.\n"
    "5. Include one personal rating in each comment (for example, \"I'd rate this 7/10\").\n"
    "6. If rules conflict, preserve reply naturalness first, then target-angle recoverability.\n\n"
    "IMPORTANT: Your entire reply must be only valid JSON.\n"
)

_ANCHORED_STEGO_ENCODE_USER = (
    "## Context to Reply To\n\n"
    "### Target Angle\n"
    "- Category: {target_category}\n"
    "- Tangent: {target_tangent}\n"
    "- Source quote: {target_source_quote}\n\n"
    "---\n\n"
    "### Relevant Research / Domain Info\n"
    "{best_match}\n\n"
    "---\n\n"
    "### Original Post / Selected Comment Thread\n\n"
    "Title: {title}\n"
    "Author: {author}\n\n"
    "Content:\n"
    "{selftext}{chain_section}"
)

_GUIDED_NATURAL_STEGO_ENCODE_USER = (
    "## Context to Reply To\n\n"
    "### Target Angle\n"
    "- Tangent: {target_tangent}\n"
    "- Category: {target_category}\n"
    "- Source quote: {target_source_quote}\n\n"
    "---\n\n"
    "### Relevant Research / Domain Info\n"
    "{best_match}\n\n"
    "---\n\n"
    "### Original Post / Selected Comment Thread\n\n"
    "Title: {title}\n"
    "Author: {author}\n\n"
    "Content:\n"
    "{selftext}{chain_section}"
)

_DEFAULT_STEGO_DECODE_USER = (
    "### FEW-SHOT EXAMPLES:\n"
    "{few_shots}\n\n"
    "### INPUT TEXT:\n"
    "{stego_text}\n\n"
    "Reply with exactly one line in this form and nothing else:\n"
    "idx: <integer>\n"
    "where <integer> equals the idx field of the one angle object below that best matches INPUT TEXT."
)

_DEFAULT_STEGO_DECODE_SYSTEM = (
    "You choose exactly one angle from the JSON list below that best matches the INPUT TEXT.\n"
    "Each object includes idx: the canonical 0-based index in the full angle list (0 <= idx < {angle_count}).\n"
    "Output format (mandatory): a single line only, exactly: idx: N\n"
    "N must be the idx of your chosen object (only values that appear in the list). "
    "Do not explain, apologize, analyze, or add any other text or numbers.\n\n"
    "{candidates_json}"
)

_DEFAULT_GEN_ANGLES_USER = (
    "I have a block of texts from any domain — it could be educational, technical, journalistic, creative, or conversational. I want you to extract phrases or quotes that could spark commentary, opinions, or deeper exploration. For each quote, generate a structured JSON object with:\n"
    '- `"source_quote"`: A short phrase or sentence from the text that could inspire discussion.\n'
    '- `"tangent"`: A brief description of the idea, opinion, or deeper topic I could explore based on that quote.\n'
    '- `"category"`: A high-level theme that groups the tangent (e.g. "Politics", "Technology", "Education", "Philosophy", "Culture", "Business").\n\n'
    "Please give me at least 15 items. Return ONLY a JSON array, no markdown fences, no explanations.\n\n"
    "Texts:\n"
    "{combined_text}"
)

_DEFAULT_GEN_ANGLES_SYSTEM = """You are a specialized Texts Analysis and Structuring Agent. Your sole function is to process input blocks of texts and extract key discussion points, formatting the entire output as a single, valid JSON array of objects.

**CRITICAL OUTPUT DIRECTIVE:**
The entire output **MUST** be the raw JSON array beginning with `[` and ending with `]`. **DO NOT** include any markdown fences (like ```json or ```), explanations, preambles, or postambles.

**STRICT OUTPUT CONSTRAINTS:**
1. **Format:** Your entire response **MUST** be a single JSON array (`[...]`). Do not include any preceding or trailing text, explanations, code fences, or commentary.
2. **Minimum Count:** You **MUST** generate a minimum of 15 JSON objects in the array.
3. **Schema:** Each object **MUST** adhere strictly to the following schema with exactly these three keys:
   * `"source_quote"` (string): A short, compelling quote or phrase extracted directly from the input text.
   * `"tangent"` (string): A brief, provocative description of the deeper topic, opinion, or line of inquiry inspired by the quote.
   * `"category"` (string): A high-level thematic label (e.g., "Technology", "Philosophy", "Business", "Culture", "Science")."""

_DEFAULT_GEN_SEARCH_TERMS_SYSTEM = """You are a creative intelligence that transforms any text into a kaleidoscope of fascinating research pathways. Your mission is to explode a single post into the maximum number of intriguing, non-obvious, and wildly distinct search queries that capture every conceivable dimension of the content. Think like a polymath detective, cultural anthropologist, and trend forecaster combined.

**Maximize these qualities in your queries:**
- **Unexpected angles** (What would a historian, neuroscientist, or underground subculture expert search for?)
- **Granular specificity** (Niche down to absurd levels of detail)
- **Cross-domain connections** (Link topics to unrelated fields)
- **Temporal dimensions** (Trends, futures, forgotten pasts, "2025", "since 2020")
- **Actionable formats** ("vs", "alternatives", "how to", "why does", "tools for", "mistakes with")
- **Jargon exploration** (Technical terms, slang, industry acronyms)
- **Geographic/cultural variants** (UK vs US terms, regional practices)

**OUTPUT RULES:**
- Return ONLY a JSON array of search strings
- Minimum 12 queries (aim for 15-20)
- Each query must be UNIQUE (no semantic duplicates)
- Strip ALL personal identifiers, names, and emotional language
- Focus purely on concepts, mechanisms, and externalizable topics
- Make each query sound like something a curious expert would type into Google at 2am

**Examples of transformation:**
❌ Boring: "cooking tips"  
✅ Interesting: "Maillard reaction mistakes cast iron skillet 2024"

❌ Boring: "productivity apps"  
✅ Interesting: "Zettelkasten method vs PARA system academic research"

❌ Boring: "travel Japan"  
✅ Interesting: "Japan conbini food hacking minimalist backpacking"

**Input:** A post about someone's experience.
**Your task:** Deconstruct it into the most interesting, obscure, and diverse search queries possible. Cover technical terms, cultural phenomena, historical precedents, psychological mechanisms, tool comparisons, and emerging trends. Leave no conceptual stone unturned. Format as a JSON array of strings, no explanations."""

_DEFAULT_GEN_SEARCH_TITLE = "# Title: {title}"
_DEFAULT_GEN_SEARCH_URL = "`{url}`"
_DEFAULT_GEN_SEARCH_CONTENT = "## Content:\n{text}"


class StegoEncodePrompts(BaseModel):
    """Stego sender LLM templates."""

    system_template: str = Field(min_length=1)
    user_template: str = Field(min_length=1)


class StegoDecodePrompts(BaseModel):
    """Stego decode LLM templates."""

    user_template: str = Field(min_length=1)
    system_template: str = Field(min_length=1)


class GenAnglesPrompts(BaseModel):
    """Gen-angles LLM templates."""

    user_template: str = Field(min_length=1)
    system_template: str = Field(min_length=1)


class GenSearchTermsPrompts(BaseModel):
    """Gen-terms LLM templates."""

    system_template: str = Field(min_length=1)
    user_title_template: str = Field(min_length=1)
    user_url_template: str = Field(min_length=1)
    user_content_template: str = Field(min_length=1)


class WorkflowLlmPromptsDocument(BaseModel):
    """Root document for config/workflow_llm_prompts.json."""

    version: int = Field(ge=1)
    stego_encode: StegoEncodePrompts
    stego_decode: StegoDecodePrompts
    gen_angles: GenAnglesPrompts
    gen_search_terms: GenSearchTermsPrompts


def workflow_llm_prompts_path() -> Path:
    """Resolved path to workflow LLM prompts JSON under the repository root."""
    return (REPO_ROOT / "config" / "workflow_llm_prompts.json").resolve()


def default_workflow_llm_prompts() -> WorkflowLlmPromptsDocument:
    """Baked-in defaults (used for reset and when the config file is missing)."""
    return WorkflowLlmPromptsDocument(
        version=1,
        stego_encode=StegoEncodePrompts(
            system_template=_DEFAULT_STEGO_ENCODE_SYSTEM,
            user_template=_DEFAULT_STEGO_ENCODE_USER,
        ),
        stego_decode=StegoDecodePrompts(
            user_template=_DEFAULT_STEGO_DECODE_USER,
            system_template=_DEFAULT_STEGO_DECODE_SYSTEM,
        ),
        gen_angles=GenAnglesPrompts(
            user_template=_DEFAULT_GEN_ANGLES_USER,
            system_template=_DEFAULT_GEN_ANGLES_SYSTEM,
        ),
        gen_search_terms=GenSearchTermsPrompts(
            system_template=_DEFAULT_GEN_SEARCH_TERMS_SYSTEM,
            user_title_template=_DEFAULT_GEN_SEARCH_TITLE,
            user_url_template=_DEFAULT_GEN_SEARCH_URL,
            user_content_template=_DEFAULT_GEN_SEARCH_CONTENT,
        ),
    )


def stego_encode_prompts_for_style(style: str) -> StegoEncodePrompts:
    """Built-in stego encode prompt variants controlled by the encoding profile."""
    if style == "anchored":
        return StegoEncodePrompts(
            system_template=_ANCHORED_STEGO_ENCODE_SYSTEM,
            user_template=_ANCHORED_STEGO_ENCODE_USER,
        )
    if style == "guided_natural":
        return StegoEncodePrompts(
            system_template=_DEFAULT_STEGO_ENCODE_SYSTEM,
            user_template=_GUIDED_NATURAL_STEGO_ENCODE_USER,
        )
    return get_prompts().stego_encode


_cache: WorkflowLlmPromptsDocument | None = None


def reload_prompts() -> None:
    """Clear in-process cache so the next get_prompts() reads from disk."""
    global _cache
    had_cached = _cache is not None
    _cache = None
    _LOG.debug(
        "workflow_llm_prompts_cache_cleared had_cached={}",
        had_cached,
    )


@validate_call
def load_workflow_llm_prompts_from_path(path: Path) -> WorkflowLlmPromptsDocument:
    """Load and validate prompts from a JSON file."""
    raw = path.read_text(encoding="utf-8")
    data: Any = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("prompts file must contain a JSON object")
    return WorkflowLlmPromptsDocument.model_validate(data)


@validate_call
def save_workflow_llm_prompts_to_path(path: Path, doc: WorkflowLlmPromptsDocument) -> None:
    """Atomically write prompts JSON (utf-8, indent=2)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        doc.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)
    _LOG.info(
        "workflow_llm_prompts_saved path={} version={}",
        str(path),
        doc.version,
    )


def get_prompts() -> WorkflowLlmPromptsDocument:
    """Return cached prompts, loading from disk or defaults on first use."""
    global _cache
    if _cache is not None:
        return _cache
    path = workflow_llm_prompts_path()
    if path.is_file():
        try:
            _cache = load_workflow_llm_prompts_from_path(path)
            _LOG.info(
                "workflow_llm_prompts_loaded_from_disk path={} version={}",
                str(path),
                _cache.version,
            )
            return _cache
        except (OSError, json.JSONDecodeError, ValidationError):
            _LOG.exception(
                "workflow_llm_prompts_load_failed path={} next=baked_in_defaults",
                str(path),
            )
            _cache = default_workflow_llm_prompts()
            _LOG.info(
                "workflow_llm_prompts_fallback_active reason=load_error version={}",
                _cache.version,
            )
            return _cache
    _cache = default_workflow_llm_prompts()
    _LOG.info(
        "workflow_llm_prompts_fallback_active reason=file_missing path={} version={}",
        str(path),
        _cache.version,
    )
    return _cache


def format_gen_search_terms_user_prompt(
    post_title: str | None,
    post_text: str | None,
    post_url: str | None,
) -> str:
    """Build gen-terms user prompt from segment templates."""
    p = get_prompts().gen_search_terms
    parts: list[str] = []
    if post_title:
        parts.append(p.user_title_template.format(title=post_title))
    if post_url:
        parts.append(p.user_url_template.format(url=post_url))
    if post_text:
        parts.append(p.user_content_template.format(text=post_text))
    return "\n\n".join(parts)
