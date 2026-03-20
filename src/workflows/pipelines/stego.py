"""Steganographic encoding pipeline with n8n parity logic."""
import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from workflows.adapters.backend_api import BackendAPIAdapter
from workflows.adapters.llm import LLMAdapter
from workflows.config import get_config
from workflows.pipelines.decode import DecodePipeline
from workflows.utils.text_utils import build_post_text_dictionary, flatten_comments


MAX_LITERAL_LEN = 250
STEGO_WORKFLOW_ID = "27rZrYtywu3k9e7Q"
STEGO_DEFAULT_OFFSET = 1
STEGO_LLM_MODEL = "openai/gpt-oss-20b"
logger = logging.getLogger(__name__)
N8N_STEGO_SYSTEM_TEMPLATE = (
    "ROLE: Human Redditor — stay in character at all times.\n\n"
    "MISSION: Write three short, natural Reddit-style comments reacting to the Original Post.\n"
    "Each comment must explore the perspective derived from “{tangent}”, "
    "and feel emotionally consistent with {category}.\n"
    "The writing should sound human, grounded, and reflective — never robotic or abstract.\n\n"
    "---\n\n"
    "RULES\n\n"
    "1. Output one JSON array of exactly three text strings.\n"
    "   No markdown, no bullets, no lists.\n"
    "2. No labels or explanations. Only the comments themselves.\n"
    "3. Human tone: casual, spontaneous, slightly imperfect.\n"
    "4. Clear intent: Each comment must naturally express\n\n"
    "   * who is reacting (subject),\n"
    "   * what they are thinking or doing (action),\n"
    "   * how they feel about it (emotion).\n"
    "     Do not force grammar; keep phrasing natural.\n"
    "5. Banned words: Do not use specific keywords, names, or jargon found in the provided content. "
    "Paraphrase those ideas in everyday language.\n"
    "6. Priority rule: If any rules conflict, prioritize thematic accuracy and natural human expression.\n\n"
    "IMPORTANT: Your final response must be formatted as valid JSON and returned using the required "
    "response-formatting tool.\n"
)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and len(value) > 0


def _to_binary_utf8(text: str) -> str:
    return "".join(format(b, "08b") for b in text.encode("utf-8"))


def _get_bit_width(max_value: int) -> int:
    return 1 if max_value <= 1 else math.ceil(math.log2(max_value + 1))


def _encode_int(value: int, max_value: int) -> str:
    return format(value, f"0{_get_bit_width(max_value)}b")


def _take_bits(bits: str, count: int) -> Tuple[str, str, bool]:
    if count <= 0:
        return "", bits, False
    if len(bits) >= count:
        return bits[:count], bits[count:], False
    return bits.ljust(count, "0"), "", True


def _flatten_comments(comments: Any) -> List[Dict[str, Any]]:
    return flatten_comments(comments)


def _eq_angle(lhs: Optional[Dict[str, Any]], rhs: Optional[Dict[str, Any]]) -> bool:
    if lhs is None and rhs is None:
        return True
    if lhs is None or rhs is None:
        return False
    return (
        lhs.get("category") == rhs.get("category")
        and lhs.get("tangent") == rhs.get("tangent")
        and lhs.get("source_quote") == rhs.get("source_quote")
    )


def _angle_summary(angle: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(angle, dict):
        return None
    return {
        "idx": angle.get("idx"),
        "category": angle.get("category"),
        "tangent": angle.get("tangent"),
        "source_quote": angle.get("source_quote"),
    }


def _text_preview(text: Any, max_len: int = 180) -> str:
    if not isinstance(text, str):
        return ""
    stripped = " ".join(text.split())
    return stripped if len(stripped) <= max_len else f"{stripped[:max_len]}..."


class StegoPipeline:
    """Pipeline for steganographic encoding."""

    def __init__(self):
        self.backend = BackendAPIAdapter()
        self.llm = LLMAdapter()
        self.decode_pipeline = DecodePipeline()
        self.config = get_config()

    def _load_default_payload_and_tag(self) -> Tuple[Optional[str], Optional[str]]:
        """Load default payload/tag from the n8n Stego workflow SetSecretData node."""
        workflow_path = (
            Path(__file__).resolve().parents[3] / "workflows" / f"{STEGO_WORKFLOW_ID}.json"
        )
        if not workflow_path.exists():
            return None, None

        try:
            with workflow_path.open("r", encoding="utf-8") as workflow_file:
                workflow = json.load(workflow_file)
        except Exception:
            return None, None

        nodes = workflow.get("nodes", [])
        if not isinstance(nodes, list):
            return None, None

        payload_value: Optional[str] = None
        for node in nodes:
            if not isinstance(node, dict) or node.get("name") != "SetSecretData":
                continue
            assignments = (
                node.get("parameters", {})
                .get("assignments", {})
                .get("assignments", [])
            )
            if not isinstance(assignments, list):
                continue
            for assignment in assignments:
                if (
                    isinstance(assignment, dict)
                    and assignment.get("name") == "payload"
                    and isinstance(assignment.get("value"), str)
                ):
                    payload_value = assignment["value"]
                    break
            if payload_value is not None:
                break

        if not payload_value:
            return None, None

        payload_candidate = payload_value.strip()
        if payload_candidate.startswith("="):
            payload_candidate = payload_candidate[1:].strip()

        try:
            parsed = json.loads(payload_candidate)
        except json.JSONDecodeError:
            return payload_candidate, None

        if isinstance(parsed, dict):
            parsed_payload = parsed.get("payload")
            parsed_tag = parsed.get("tag")
            payload = parsed_payload if isinstance(parsed_payload, str) else None
            tag = parsed_tag if isinstance(parsed_tag, str) else None
            return payload, tag

        if isinstance(parsed, str):
            return parsed, None
        return None, None

    def _build_dictionary(self, post: Dict[str, Any]) -> List[str]:
        dictionary = build_post_text_dictionary(post)
        return [entry for entry in dictionary if _is_non_empty_string(entry)]

    def _compress_payload(self, payload: str, dictionary: List[str]) -> Dict[str, Any]:
        std_binary = _to_binary_utf8(payload)
        std_length = 1 + len(std_binary)

        n = len(payload)
        max_dict_index = len(dictionary)
        global_max_match = 0
        for text in dictionary:
            if len(text) > global_max_match:
                global_max_match = len(text)

        matches: Dict[int, List[Dict[str, int]]] = {}
        if n > 0 and dictionary:
            for i in range(n):
                current_char = payload[i]
                matches_at_i: List[Dict[str, int]] = []
                for doc_idx, dict_text in enumerate(dictionary):
                    start = dict_text.find(current_char)
                    while start != -1:
                        match_len = 1
                        max_len = min(global_max_match, n - i, len(dict_text) - start)
                        while (
                            match_len < max_len
                            and payload[i + match_len] == dict_text[start + match_len]
                        ):
                            match_len += 1
                        if match_len > 2:
                            matches_at_i.append(
                                {"doc": doc_idx, "idx": start, "len": match_len}
                            )
                        start = dict_text.find(current_char, start + 1)
                if matches_at_i:
                    matches[i] = matches_at_i

        dp = [float("inf")] * (n + 1)
        choice: List[Optional[Dict[str, Any]]] = [None] * n
        dp[n] = 0.0

        bw_literal_len = _get_bit_width(MAX_LITERAL_LEN)
        bw_dict_idx = _get_bit_width(max_dict_index)
        bw_match_len = _get_bit_width(global_max_match)

        for i in range(n - 1, -1, -1):
            max_l = min(MAX_LITERAL_LEN, n - i)
            for literal_len in range(1, max_l + 1):
                substring = payload[i : i + literal_len]
                byte_len = len(substring.encode("utf-8"))
                cost = 1 + bw_literal_len + byte_len * 8 + dp[i + literal_len]
                if cost < dp[i]:
                    dp[i] = cost
                    choice[i] = {
                        "kind": "literal",
                        "len": literal_len,
                        "sub_str": substring,
                    }

            for match in matches.get(i, []):
                doc_len_bits = _get_bit_width(len(dictionary[match["doc"]]))
                cost = (
                    1
                    + bw_dict_idx
                    + doc_len_bits
                    + bw_match_len
                    + dp[i + match["len"]]
                )
                if cost < dp[i]:
                    dp[i] = cost
                    choice[i] = {"kind": "dict", **match}

        curr = 0
        dict_binary_parts: List[str] = []
        references: List[Dict[str, Any]] = []

        while curr < n:
            picked = choice[curr] or {
                "kind": "literal",
                "len": 1,
                "sub_str": payload[curr : curr + 1],
            }
            safe_len = max(1, int(picked.get("len", 1)))
            if picked["kind"] == "literal":
                literal = str(picked.get("sub_str", payload[curr : curr + safe_len]))
                bin_value = _to_binary_utf8(literal)
                dict_binary_parts.append("0")
                dict_binary_parts.append(_encode_int(safe_len, MAX_LITERAL_LEN))
                dict_binary_parts.append(bin_value)
                references.append({"doc": None, "idx": curr, "len": safe_len})
            else:
                doc = int(picked["doc"])
                idx = int(picked["idx"])
                dict_binary_parts.append("1")
                dict_binary_parts.append(_encode_int(doc, max_dict_index))
                dict_binary_parts.append(_encode_int(idx, len(dictionary[doc])))
                dict_binary_parts.append(_encode_int(safe_len, global_max_match))
                references.append({"doc": doc, "idx": idx, "len": safe_len})
            curr += safe_len

        dict_binary = "".join(dict_binary_parts)
        dict_length = 1 + len(dict_binary)
        if dict_length >= std_length:
            return {
                "method": "standard",
                "payload": payload,
                "compressed": "0" + std_binary,
                "compressedLength": std_length,
                "originalLength": len(std_binary),
                "ratio": std_length / (len(std_binary) or 1),
                "references": [],
            }

        return {
            "method": "dictionary",
            "payload": payload,
            "compressed": "1" + dict_binary,
            "compressedLength": dict_length,
            "originalLength": len(std_binary),
            "ratio": dict_length / (len(std_binary) or 1),
            "references": references,
        }

    def _embed_in_comment_selection(
        self, bits: str, post: Dict[str, Any]
    ) -> Dict[str, Any]:
        flattened_comments = _flatten_comments(post.get("comments", []))
        n = len(flattened_comments)
        bits_count = _get_bit_width(n)
        bits_used, remaining, insufficient = _take_bits(bits, bits_count)
        selection_index = int(bits_used or "0", 2)
        if selection_index > n:
            selection_index %= (n + 1)

        picked_chain: List[Dict[str, Any]] = []
        if selection_index > 0 and n > 0:
            picked_comment = flattened_comments[selection_index - 1]
            comment_map: Dict[str, Dict[str, Any]] = {}
            for comment in flattened_comments:
                cid = comment.get("id")
                if isinstance(cid, str):
                    comment_map[cid] = comment
                    if "_" in cid:
                        comment_map[cid.split("_", 1)[1]] = comment

            current = picked_comment
            visited: set[str] = set()
            while True:
                current_id = str(current.get("id", ""))
                if current_id in visited:
                    break
                visited.add(current_id)
                picked_chain.insert(
                    0,
                    {
                        "name": (
                            current.get("author")
                            if isinstance(current.get("author"), str)
                            and current.get("author").strip()
                            else "Unknown"
                        ),
                        "body": (
                            current.get("body")
                            if isinstance(current.get("body"), str)
                            else ""
                        ),
                        "id": current.get("id"),
                        "parent_id": current.get("parent_id"),
                        "permalink": current.get("permalink"),
                    },
                )
                parent_id = current.get("parent_id")
                link_id = current.get("link_id")
                if parent_id == link_id:
                    break
                parent = comment_map.get(str(parent_id))
                if parent is None and isinstance(parent_id, str) and "_" in parent_id:
                    parent = comment_map.get(parent_id.split("_", 1)[1])
                if parent is None or parent is current:
                    break
                current = parent

        return {
            "result": {
                "bitsUsed": bits_used,
                "bitsCount": bits_count,
                "targetType": "post" if selection_index == 0 else "comment",
                "context": {
                    "id": post.get("id"),
                    "title": post.get("title"),
                    "author": post.get("author"),
                    "selftext": post.get("selftext", ""),
                    "permalink": post.get("permalink"),
                },
                "pickedCommentChain": picked_chain,
                "insufficientBits": insufficient,
            },
            "remainingBits": remaining,
        }

    def _embed_in_angle_selection(
        self, bits: str, nested_angles: List[List[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        angles: List[Dict[str, Any]] = []
        for angle_group in nested_angles:
            for angle in angle_group:
                with_idx = dict(angle)
                with_idx["idx"] = len(angles)
                angles.append(with_idx)

        if not angles:
            return {
                "bitsUsed": "",
                "bitsCount": 0,
                "remainingBits": bits,
                "selectedAngle": {},
                "remainingAngles": [],
                "totalAnglesSelectedFirst": [],
                "TangentsDB": [],
                "insufficientBits": False,
            }

        bits_count = _get_bit_width(len(angles) - 1)
        bits_used, remaining, insufficient = _take_bits(bits, bits_count)
        idx = int(bits_used or "0", 2)
        if idx >= len(angles):
            idx %= len(angles)

        selected_angle = angles[idx]
        remaining_angles = [a for i, a in enumerate(angles) if i != idx]
        return {
            "bitsUsed": bits_used,
            "bitsCount": bits_count,
            "remainingBits": remaining,
            "selectedAngle": selected_angle,
            "remainingAngles": remaining_angles,
            "totalAnglesSelectedFirst": [selected_angle, *remaining_angles],
            "TangentsDB": angles,
            "insufficientBits": insufficient,
        }

    def _augment_post(self, payload: str, post: Dict[str, Any]) -> Dict[str, Any]:
        nested_angles = [
            x if isinstance(x, list) else [x]
            for x in post.get("angles", [])
            if x is not None
        ]
        dictionary = self._build_dictionary(post)
        compression = self._compress_payload(payload, dictionary)
        warnings: List[str] = []
        if compression.get("method") == "standard":
            warnings.append("Dictionary compression inefficient; used standard encoding.")

        comment_emb = self._embed_in_comment_selection(compression["compressed"], post)
        if comment_emb["result"].get("insufficientBits"):
            warnings.append("Padding used in Comment Selection.")

        angle_emb = self._embed_in_angle_selection(
            comment_emb["remainingBits"], nested_angles
        )
        if angle_emb.get("insufficientBits"):
            warnings.append("Padding used in Angle Selection.")

        return {
            "compression": compression,
            "commentEmbedding": comment_emb["result"],
            "angleEmbedding": angle_emb,
            "totalBitsEmbedded": comment_emb["result"]["bitsCount"]
            + angle_emb["bitsCount"],
            "fullEncodedBits": comment_emb["result"]["bitsUsed"] + angle_emb["bitsUsed"],
            "warnings": warnings,
        }

    def _build_samples(
        self, post_augmentation: Dict[str, Any], post: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        angle_embedding = post_augmentation.get("angleEmbedding", {})
        candidate_angles = angle_embedding.get("totalAnglesSelectedFirst", [])[:4]
        needles = [
            str(a.get("source_quote", ""))
            for a in candidate_angles
            if isinstance(a, dict)
        ]
        haystack = post.get("search_results", [])
        if not isinstance(haystack, list):
            haystack = []

        source_response = self.backend.needle_finder_batch(needles=needles, haystack=haystack)
        source_results = source_response.get("results", [])

        samples: List[Dict[str, Any]] = []
        for idx, angle in enumerate(candidate_angles):
            if not isinstance(angle, dict):
                continue
            match_data = source_results[idx] if idx < len(source_results) else {}
            best_match = (
                match_data.get("best_match", "")
                if isinstance(match_data, dict)
                else ""
            )
            sample = dict(angle)
            sample["best_match"] = best_match
            samples.append(sample)

        tangents_db = angle_embedding.get("TangentsDB", [])
        if not isinstance(tangents_db, list):
            tangents_db = []
        return samples, tangents_db

    def _build_prompt(
        self, sample: Dict[str, Any], comment_embedding: Dict[str, Any]
    ) -> Tuple[str, str]:
        context = comment_embedding.get("context", {})
        title = context.get("title", "")
        author = context.get("author", "")
        selftext = context.get("selftext", "")
        title = title if isinstance(title, str) else ""
        author = author if isinstance(author, str) else ""
        selftext = selftext if isinstance(selftext, str) else ""

        picked_chain = comment_embedding.get("pickedCommentChain", [])
        chain_section = ""
        if isinstance(picked_chain, list) and picked_chain:
            rendered: List[str] = []
            for comment in picked_chain:
                if not isinstance(comment, dict):
                    continue
                raw_name = comment.get("name")
                raw_body = comment.get("body")
                body = raw_body.strip() if isinstance(raw_body, str) else ""
                if not body:
                    continue
                name = raw_name.strip() if isinstance(raw_name, str) else ""
                if not name:
                    name = "Unknown"
                label = "commented" if not rendered else "replyed"
                rendered.append(f"{name} {label}:\n{body}")
            if rendered:
                chain_section = "\n---\n" + "\n---\n".join(rendered)

        prompt = (
            "## Context to React To\n\n"
            "### Relevant Research / Domain Info\n"
            f"{sample.get('best_match', '')}\n\n"
            "---\n\n"
            "### Original Post / Comments\n\n"
            f"Title: {title}\n"
            f"Author: {author}\n\n"
            "Content:\n"
            f"{selftext}{chain_section}"
        )

        system_message = N8N_STEGO_SYSTEM_TEMPLATE.format(
            tangent=sample.get("tangent", ""),
            category=sample.get("category", ""),
        )
        return prompt, system_message

    def _generate_stego_texts(
        self,
        sample: Dict[str, Any],
        comment_embedding: Dict[str, Any],
    ) -> List[str]:
        def _clean_text_list(items: Any) -> List[str]:
            if not isinstance(items, list):
                return []
            cleaned = [str(x).strip() for x in items if isinstance(x, str) and str(x).strip()]
            return cleaned

        def _extract_json_block(raw: str) -> str:
            stripped = raw.strip()
            if not stripped.startswith("```"):
                return stripped
            lines = stripped.splitlines()
            if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].startswith("```"):
                return "\n".join(lines[1:-1]).strip()
            return stripped

        prompt, system_message = self._build_prompt(sample, comment_embedding)
        logger.info(
            "[STEGO][PROMPT][ENCODE] category=%s tangent=%s source_quote=%s",
            sample.get("category"),
            _text_preview(sample.get("tangent", ""), max_len=120),
            _text_preview(sample.get("source_quote", ""), max_len=120),
        )
        logger.info("[STEGO][PROMPT][ENCODE][SYSTEM]\n%s", system_message)
        logger.info("[STEGO][PROMPT][ENCODE][USER]\n%s", prompt)
        response = self.llm.call_llm(
            prompt=prompt,
            system_message=system_message,
            model=STEGO_LLM_MODEL,
            provider="lm_studio",
            temperature=0.7,
        )
        text = response.strip()
        logger.info("[STEGO][LLM][ENCODE][RAW]\n%s", text)

        # Accept plain JSON and markdown-fenced JSON payloads.
        json_candidates = [text, _extract_json_block(text)]
        for payload in json_candidates:
            if not payload:
                continue
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                continue

            direct = _clean_text_list(parsed)
            if direct:
                logger.info(
                    "[STEGO][LLM][ENCODE][PARSED] extracted=%s mode=array",
                    len(direct),
                )
                return direct

            if isinstance(parsed, dict):
                for key in ("texts", "comments", "items", "output"):
                    clean = _clean_text_list(parsed.get(key))
                    if clean:
                        logger.info(
                            "[STEGO][LLM][ENCODE][PARSED] extracted=%s mode=%s",
                            len(clean),
                            key,
                        )
                        return clean

        logger.warning(
            "[STEGO][LLM][ENCODE][PARSE] Falling back to raw text payload for sample tangent=%s",
            _text_preview(sample.get("tangent", ""), max_len=120),
        )
        return [text] if text else []

    def _cross_validate(
        self,
        candidate_texts: List[str],
        few_shots: List[Dict[str, Any]],
        tangents_db: List[Dict[str, Any]],
        selected_angle: Dict[str, Any],
    ) -> Dict[str, Any]:
        decoded_indices: List[Optional[int]] = []
        decodeds: List[Optional[Dict[str, Any]]] = []
        for text in candidate_texts:
            decoded_idx = self.decode_pipeline.decode(
                stego_text=text,
                angles=tangents_db,
                few_shots=few_shots,
            )
            decoded_indices.append(decoded_idx)
            if isinstance(decoded_idx, int) and 0 <= decoded_idx < len(tangents_db):
                decoded = tangents_db[decoded_idx]
                decodeds.append(decoded)
            else:
                decodeds.append(None)

        validation_candidates: List[Dict[str, Any]] = []
        selected_summary = _angle_summary(selected_angle)
        for idx, text in enumerate(candidate_texts):
            decoded_obj = decodeds[idx] if idx < len(decodeds) else None
            decoded_idx = decoded_indices[idx] if idx < len(decoded_indices) else None
            validation_candidates.append(
                {
                    "candidate_index": idx,
                    "decoded_index": decoded_idx,
                    "decoded_angle": _angle_summary(decoded_obj),
                    "matches_selected_angle": _eq_angle(decoded_obj, selected_angle),
                    "text_preview": _text_preview(text),
                }
            )

        success_idx = -1
        for idx, decoded_obj in enumerate(decodeds):
            if _eq_angle(decoded_obj, selected_angle):
                success_idx = idx
                break

        if success_idx != -1:
            return {
                "succeeded": True,
                "stegoText": candidate_texts[success_idx],
                "successIdx": success_idx,
                "decodedIndices": decoded_indices,
                "validationDetails": {
                    "selected_angle": selected_summary,
                    "candidates": validation_candidates,
                },
            }

        breakdown: Dict[str, Any] = {}
        for idx, text in enumerate(candidate_texts):
            breakdown[text] = decodeds[idx]
        return {
            "succeeded": False,
            "breakDown": breakdown,
            "decodedIndices": decoded_indices,
            "validationDetails": {
                "selected_angle": selected_summary,
                "candidates": validation_candidates,
            },
        }

    def encode(
        self,
        payload: str,
        post: Dict[str, Any],
        tag: Optional[str] = None,
        max_retries: int = 4,
    ) -> Dict[str, Any]:
        """
        Encode payload into post using steganography.

        This implementation mirrors the n8n Stego workflow:
        1) Post augmentation (compression + comment/angle embedding)
        2) Source matching and sample construction
        3) Candidate generation + decode cross-validation
        4) Retry loop when validation fails
        """
        angles = post.get("angles", [])
        if not isinstance(angles, list) or not angles:
            raise ValueError("Post must have angles")

        post_id = post.get("id")
        logger.info(
            "[STEGO][START] post_id=%s payload_len=%s max_retries=%s",
            post_id,
            len(payload),
            max_retries,
        )

        post_augmentation = self._augment_post(payload, post)
        samples, tangents_db = self._build_samples(post_augmentation, post)
        if not samples:
            logger.error(
                "[STEGO][PREP] No samples generated from angle embedding for post_id=%s",
                post_id,
            )
            return {
                "stego_text": "",
                "post": post,
                "succeeded": False,
                "retry_count": 0,
                "tag": tag,
                "error": "No samples generated from angle embedding",
                "error_details": {
                    "reason": "Angle embedding produced zero sample prompts for generation.",
                    "selected_angle": _angle_summary(
                        post_augmentation.get("angleEmbedding", {}).get("selectedAngle")
                    ),
                },
                "embedding": post_augmentation,
            }

        selected_angle = post_augmentation["angleEmbedding"].get("selectedAngle", {})
        selected_idx = selected_angle.get("idx")
        retry_count = 0
        last_breakdown: Dict[str, Any] = {}

        while retry_count <= max_retries:
            try:
                logger.info(
                    "[STEGO][ATTEMPT] post_id=%s attempt=%s/%s selected_idx=%s",
                    post_id,
                    retry_count + 1,
                    max_retries + 1,
                    selected_idx,
                )
                encoded_results: List[Dict[str, Any]] = []
                for sample in samples:
                    texts = self._generate_stego_texts(
                        sample=sample,
                        comment_embedding=post_augmentation["commentEmbedding"],
                    )
                    encoded_results.append(
                        {
                            "category": sample.get("category"),
                            "source_quote": sample.get("source_quote"),
                            "tangent": sample.get("tangent"),
                            "texts": texts,
                        }
                    )

                primary_texts = encoded_results[0].get("texts", []) if encoded_results else []
                few_shots = encoded_results[1:]
                if not primary_texts:
                    raise RuntimeError("Encoder did not return candidate texts")

                logger.info(
                    "[STEGO][GENERATE] post_id=%s attempt=%s primary_candidates=%s few_shot_groups=%s",
                    post_id,
                    retry_count + 1,
                    len(primary_texts),
                    len(few_shots),
                )
                validation = self._cross_validate(
                    candidate_texts=primary_texts,
                    few_shots=few_shots,
                    tangents_db=tangents_db,
                    selected_angle=selected_angle,
                )
                if validation.get("succeeded"):
                    logger.info(
                        "[STEGO][SUCCESS] post_id=%s attempt=%s success_candidate=%s decoded_indices=%s",
                        post_id,
                        retry_count + 1,
                        validation.get("successIdx"),
                        validation.get("decodedIndices", []),
                    )
                    return {
                        "stego_text": validation["stegoText"],
                        "post": post,
                        "selected_angle": selected_angle,
                        "angle_index": selected_idx,
                        "succeeded": True,
                        "retry_count": retry_count,
                        "tag": tag,
                        "embedding": post_augmentation,
                        "encoded_samples": encoded_results,
                        "decoded_indices": validation.get("decodedIndices", []),
                        "validation_details": validation.get("validationDetails"),
                    }

                last_breakdown = validation.get("breakDown", {})
                validation_details = validation.get("validationDetails", {})
                logger.warning(
                    "[STEGO][VALIDATION] post_id=%s attempt=%s failed selected_idx=%s decoded_indices=%s",
                    post_id,
                    retry_count + 1,
                    selected_idx,
                    validation.get("decodedIndices", []),
                )
                if retry_count >= max_retries:
                    error_details = {
                        "reason": (
                            "None of the generated primary candidate texts decoded to the selected angle."
                        ),
                        "selected_angle": _angle_summary(selected_angle),
                        "decoded_indices": validation.get("decodedIndices", []),
                        "candidate_results": validation_details.get("candidates", []),
                    }
                    logger.error(
                        "[STEGO][FAILED] post_id=%s reason=%s",
                        post_id,
                        error_details["reason"],
                    )
                    return {
                        "stego_text": primary_texts[0] if primary_texts else "",
                        "post": post,
                        "selected_angle": selected_angle,
                        "angle_index": selected_idx,
                        "succeeded": False,
                        "retry_count": retry_count,
                        "tag": tag,
                        "error": "Decoding validation failed",
                        "error_details": error_details,
                        "breakdown": last_breakdown,
                        "validation_details": validation_details,
                        "embedding": post_augmentation,
                        "encoded_samples": encoded_results,
                    }
                retry_count += 1
            except Exception as exc:
                logger.error(
                    "[STEGO][ERROR] post_id=%s attempt=%s exception=%s: %s",
                    post_id,
                    retry_count + 1,
                    type(exc).__name__,
                    exc,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )
                if retry_count >= max_retries:
                    return {
                        "stego_text": "",
                        "post": post,
                        "selected_angle": selected_angle,
                        "angle_index": selected_idx,
                        "succeeded": False,
                        "retry_count": retry_count,
                        "tag": tag,
                        "error": str(exc),
                        "error_details": {
                            "reason": "Unexpected exception during stego encoding.",
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                            "selected_angle": _angle_summary(selected_angle),
                        },
                        "breakdown": last_breakdown,
                        "embedding": post_augmentation,
                    }
                retry_count += 1

        logger.error("[STEGO][FAILED] post_id=%s max retries exceeded", post_id)
        return {
            "stego_text": "",
            "post": post,
            "selected_angle": selected_angle,
            "angle_index": selected_idx,
            "succeeded": False,
            "retry_count": retry_count,
            "tag": tag,
            "error": "Max retries exceeded",
            "breakdown": last_breakdown,
            "embedding": post_augmentation,
        }

    def process_post(
        self,
        post_id: Optional[str] = None,
        payload: Optional[str] = None,
        tag: Optional[str] = None,
        step: str = "final-step",
        list_offset: int = STEGO_DEFAULT_OFFSET,
    ) -> Dict[str, Any]:
        """Process one post and persist output on success.

        If post_id is not provided, select one unprocessed final-step post using tag.
        If payload is not provided, load default payload/tag from Stego workflow JSON.
        """
        def _select_next_post_id() -> str:
            posts_list = self.backend.posts_list(
                step="final-step",
                count=1,
                offset=max(0, int(list_offset)),
                tag=resolved_tag,
            )
            file_names = posts_list.get("fileNames", [])
            if not file_names:
                raise ValueError(
                    f"No unprocessed posts found for step='final-step' and tag='{resolved_tag}'."
                )
            first_file = file_names[0]
            next_post_id = first_file[:-5] if first_file.endswith(".json") else first_file
            logger.info(
                "[STEGO][PROCESS] auto-selected post_id=%s for tag=%s",
                next_post_id,
                resolved_tag,
            )
            return next_post_id

        logger.info(
            "[STEGO][PROCESS] start post_id=%s list_offset=%s",
            post_id,
            list_offset,
        )
        workflow_payload, workflow_tag = self._load_default_payload_and_tag()
        using_workflow_payload = not (isinstance(payload, str) and payload)
        resolved_payload = payload if isinstance(payload, str) and payload else workflow_payload
        resolved_tag = tag if tag is not None else (workflow_tag if using_workflow_payload else None)

        if not resolved_payload:
            raise ValueError(
                "Payload is required. Provide payload or configure SetSecretData payload in workflows/27rZrYtywu3k9e7Q.json."
            )

        resolved_post_id = post_id
        if not resolved_post_id:
            resolved_post_id = _select_next_post_id()

        # n8n Stego reads post data from final-step; keep fallback for local compatibility.
        try:
            post = self.backend.get_post_local(f"{resolved_post_id}.json", step="final-step")
        except FileNotFoundError:
            try:
                post = self.backend.get_post_local(f"{resolved_post_id}.json", step="angles-step")
            except FileNotFoundError:
                # If caller passed an outdated/nonexistent post_id, keep API parity with n8n:
                # pick next unprocessed post for the same tag instead of hard-failing.
                if post_id:
                    logger.warning(
                        "[STEGO][PROCESS] post_id=%s not found; falling back to next unprocessed for tag=%s",
                        resolved_post_id,
                        resolved_tag,
                    )
                    resolved_post_id = _select_next_post_id()
                    try:
                        post = self.backend.get_post_local(
                            f"{resolved_post_id}.json", step="final-step"
                        )
                    except FileNotFoundError:
                        post = self.backend.get_post_local(
                            f"{resolved_post_id}.json", step="angles-step"
                        )
                else:
                    raise

        result = self.encode(payload=resolved_payload, post=post, tag=resolved_tag)
        result_post_id = str(post.get("id") or resolved_post_id)
        filename = (
            f"{result_post_id}_{resolved_tag}.json"
            if resolved_tag
            else f"{result_post_id}.json"
        )
        # Keep parity with n8n workflow: write final output artifact into ./output-results.
        self.backend.save_object_local(result, step="final-step", filename=filename)
        logger.info(
            "[STEGO][PROCESS] saved result post_id=%s step=%s filename=%s",
            result_post_id,
            "final-step",
            filename,
        )

        if not result.get("succeeded"):
            logger.error(
                "[STEGO][PROCESS] failed post_id=%s error=%s",
                resolved_post_id,
                result.get("error"),
            )
        return result
