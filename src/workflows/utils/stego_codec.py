"""Shared steganographic bit-layer codec (sender + receiver).

Mirrors logic previously embedded in ``StegoPipeline`` for compression,
comment/angle embedding, and payload recovery after stripping embed prefixes.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from pydantic import validate_call

from workflows.utils.text_utils import build_post_text_dictionary, flatten_comments

MAX_LITERAL_LEN = 250


@validate_call
def is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and len(value) > 0


@validate_call
def to_binary_utf8(text: str) -> str:
    return "".join(format(b, "08b") for b in text.encode("utf-8"))


@validate_call
def get_bit_width(max_value: int) -> int:
    return 1 if max_value <= 1 else math.ceil(math.log2(max_value + 1))


@validate_call
def encode_int(value: int, max_value: int) -> str:
    return format(value, f"0{get_bit_width(max_value)}b")


@validate_call
def take_bits(bits: str, count: int) -> Tuple[str, str, bool]:
    if count <= 0:
        return "", bits, False
    if len(bits) >= count:
        return bits[:count], bits[count:], False
    return bits.ljust(count, "0"), "", True


@validate_call
def build_dictionary(post: Dict[str, Any]) -> List[str]:
    dictionary = build_post_text_dictionary(post)
    return [entry for entry in dictionary if is_non_empty_string(entry)]


@validate_call
def compress_payload(payload: str, dictionary: List[str]) -> Dict[str, Any]:
    """Same semantics as legacy ``StegoPipeline._compress_payload``."""
    std_binary = to_binary_utf8(payload)
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
                        matches_at_i.append({"doc": doc_idx, "idx": start, "len": match_len})
                    start = dict_text.find(current_char, start + 1)
            if matches_at_i:
                matches[i] = matches_at_i

    dp = [float("inf")] * (n + 1)
    choice: List[Optional[Dict[str, Any]]] = [None] * n
    dp[n] = 0.0

    bw_literal_len = get_bit_width(MAX_LITERAL_LEN)
    bw_dict_idx = get_bit_width(max_dict_index)
    bw_match_len = get_bit_width(global_max_match)

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
            doc_len_bits = get_bit_width(len(dictionary[match["doc"]]))
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
            bin_value = to_binary_utf8(literal)
            dict_binary_parts.append("0")
            dict_binary_parts.append(encode_int(safe_len, MAX_LITERAL_LEN))
            dict_binary_parts.append(bin_value)
            references.append({"doc": None, "idx": curr, "len": safe_len})
        else:
            doc = int(picked["doc"])
            idx = int(picked["idx"])
            dict_binary_parts.append("1")
            dict_binary_parts.append(encode_int(doc, max_dict_index))
            dict_binary_parts.append(encode_int(idx, len(dictionary[doc])))
            dict_binary_parts.append(encode_int(safe_len, global_max_match))
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


def embed_in_comment_selection(bits: str, post: Dict[str, Any]) -> Dict[str, Any]:
    flattened_comments = flatten_comments(post.get("comments", []))
    n = len(flattened_comments)
    bits_count = get_bit_width(n)
    bits_used, remaining, insufficient = take_bits(bits, bits_count)
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


def flatten_nested_angles(post: Dict[str, Any]) -> List[Dict[str, Any]]:
    nested = [
        x if isinstance(x, list) else [x]
        for x in post.get("angles", [])
        if x is not None
    ]
    angles: List[Dict[str, Any]] = []
    for angle_group in nested:
        for angle in angle_group:
            with_idx = dict(angle)
            with_idx["idx"] = len(angles)
            angles.append(with_idx)
    return angles


def embed_in_angle_selection(
    bits: str, nested_angles: List[List[Dict[str, Any]]]
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

    bits_count = get_bit_width(len(angles) - 1)
    bits_used, remaining, insufficient = take_bits(bits, bits_count)
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


def augment_post(payload: str, post: Dict[str, Any]) -> Dict[str, Any]:
    nested_angles = [
        x if isinstance(x, list) else [x]
        for x in post.get("angles", [])
        if x is not None
    ]
    dictionary = build_dictionary(post)
    compression = compress_payload(payload, dictionary)
    warnings: List[str] = []
    if compression.get("method") == "standard":
        warnings.append("Dictionary compression inefficient; used standard encoding.")

    comment_emb = embed_in_comment_selection(compression["compressed"], post)
    if comment_emb["result"].get("insufficientBits"):
        warnings.append("Padding used in Comment Selection.")

    angle_emb = embed_in_angle_selection(comment_emb["remainingBits"], nested_angles)
    if angle_emb.get("insufficientBits"):
        warnings.append("Padding used in Angle Selection.")

    return {
        "compression": compression,
        "commentEmbedding": comment_emb["result"],
        "angleEmbedding": angle_emb,
        "totalBitsEmbedded": comment_emb["result"]["bitsCount"] + angle_emb["bitsCount"],
        "fullEncodedBits": comment_emb["result"]["bitsUsed"] + angle_emb["bitsUsed"],
        "warnings": warnings,
    }


def comment_selection_bit_width(post: Dict[str, Any]) -> int:
    n = len(flatten_comments(post.get("comments", [])))
    return get_bit_width(n)


def angle_selection_bit_width(n_angles: int) -> int:
    if n_angles <= 0:
        return 0
    return get_bit_width(n_angles - 1)


def angle_bits_for_index(idx: int, n_angles: int) -> str:
    if n_angles <= 0:
        return ""
    idx = idx % n_angles
    return encode_int(idx, n_angles - 1)


def _decompress_standard_suffix(utf8_bit_suffix: str) -> Optional[str]:
    if len(utf8_bit_suffix) % 8 != 0:
        return None
    try:
        data = bytes(
            int(utf8_bit_suffix[i : i + 8], 2) for i in range(0, len(utf8_bit_suffix), 8)
        )
        return data.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


def decompress_after_embed_prefix(
    compressed_full: str, dictionary: List[str], lc: int, la: int
) -> Optional[str]:
    """Invert ``compress_payload`` given the full ``compressed`` bitstring from encode.

    Comment/angle bits are taken from the front of this string, but they are always
    a **prefix** of the same bitstream ``compress_payload`` produced: the remainder
    after the method flag is still ``compressed_full[1:]`` in full, so payload recovery
    decodes ``compressed_full[1:]`` without splitting on ``lc``/``la``.

    ``lc``/``la`` are kept for API symmetry / future strict checks; callers that verify
    angle bits use ``recover_payload_with_compressed_full``.
    """
    del lc, la
    if not compressed_full:
        return None

    mode = compressed_full[0]
    if mode == "0":
        utf8_all = compressed_full[1:]
        return _decompress_standard_suffix(utf8_all)

    if mode != "1":
        return None

    return _decompress_dictionary_bitstream(compressed_full[1:], dictionary)


def _read_fixed_int(bits: str, pos: int, max_value: int) -> Optional[Tuple[int, int]]:
    """Read integer using same width as ``encode_int(..., max_value)``."""
    w = get_bit_width(max_value)
    if pos + w > len(bits):
        return None
    chunk = bits[pos : pos + w]
    return int(chunk, 2), pos + w


def _read_utf8_n_chars(bits: str, pos: int, n_chars: int) -> Optional[Tuple[str, int]]:
    """Read UTF-8 bytes (8 bits each) until exactly ``n_chars`` Unicode chars decode."""
    if n_chars <= 0:
        return "", pos
    buf = bytearray()
    p = pos
    max_bytes = min(len(bits) - pos, n_chars * 4 * 8) // 8 + 16
    while len(buf) <= max_bytes:
        try:
            text = buf.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        if len(text) == n_chars:
            return text, p
        if len(text) > n_chars:
            return None
        if p + 8 > len(bits):
            return None
        buf.append(int(bits[p : p + 8], 2))
        p += 8
    return None


def _decompress_dictionary_bitstream(rem: str, dictionary: List[str]) -> Optional[str]:
    if not rem:
        return ""
    if not dictionary:
        return None
    max_dict_index = len(dictionary)
    global_max_match = max(len(t) for t in dictionary)
    pos = 0
    out: List[str] = []

    while pos < len(rem):
        kind = rem[pos]
        pos += 1
        if kind == "0":
            lit = _read_fixed_int(rem, pos, MAX_LITERAL_LEN)
            if lit is None:
                return None
            literal_len, pos = lit
            if literal_len <= 0:
                return None
            chunk = _read_utf8_n_chars(rem, pos, literal_len)
            if chunk is None:
                return None
            text, pos = chunk
            out.append(text)
        elif kind == "1":
            doc_t = _read_fixed_int(rem, pos, max_dict_index)
            if doc_t is None:
                return None
            doc, pos = doc_t
            if doc < 0 or doc >= len(dictionary):
                return None
            doc_text = dictionary[doc]
            idx_t = _read_fixed_int(rem, pos, len(doc_text))
            if idx_t is None:
                return None
            start_idx, pos = idx_t
            ml_t = _read_fixed_int(rem, pos, global_max_match)
            if ml_t is None:
                return None
            match_len, pos = ml_t
            if match_len <= 0 or start_idx + match_len > len(doc_text):
                return None
            out.append(doc_text[start_idx : start_idx + match_len])
        else:
            return None

    return "".join(out)


def recover_payload_with_compressed_full(
    compressed_full: str,
    dictionary: List[str],
    pre_sender_post: Dict[str, Any],
    nested_angles: List[List[Dict[str, Any]]],
    decoded_angle_index: int,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Recover payload when the full compressed bitstring from encode is known."""
    lc = comment_selection_bit_width(pre_sender_post)
    angles: List[Dict[str, Any]] = []
    for angle_group in nested_angles:
        for angle in angle_group:
            angles.append(dict(angle))

    la = angle_selection_bit_width(len(angles))
    if len(angles) == 0:
        return None
    expected_angle_bits = angle_bits_for_index(decoded_angle_index, len(angles))
    if len(compressed_full) < lc + la:
        return None
    actual_angle = compressed_full[lc : lc + la]
    if actual_angle != expected_angle_bits:
        return None
    comment_bits = compressed_full[:lc]
    payload = decompress_after_embed_prefix(compressed_full, dictionary, lc, la)
    if payload is None:
        return None
    meta = {
        "comment_bits": comment_bits,
        "angle_bits": actual_angle,
        "lc": lc,
        "la": la,
    }
    return payload, meta


def recover_payload_bruteforce_comment_bits(
    dictionary: List[str],
    pre_sender_post: Dict[str, Any],
    nested_angles: List[List[Dict[str, Any]]],
    decoded_angle_index: int,
    max_padding_bits: int = 256,
    *,
    compressed_full: Optional[str] = None,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Recover payload by brute-forcing the comment-selection prefix (small ``2**lc``).

    When ``compressed_full`` is provided, candidates must match it exactly.
    Otherwise we require ``compress_payload`` output to start with the embed prefix and
    ``decompress_after_embed_prefix`` to round-trip the candidate.
    """
    lc = comment_selection_bit_width(pre_sender_post)
    angles: List[Dict[str, Any]] = []
    for angle_group in nested_angles:
        for angle in angle_group:
            angles.append(dict(angle))

    la = angle_selection_bit_width(len(angles))
    if not angles:
        return None

    angle_bits = angle_bits_for_index(decoded_angle_index, len(angles))
    if compressed_full is not None:
        if len(compressed_full) < lc + la:
            return None
        if compressed_full[lc : lc + la] != angle_bits:
            return None
        payload = decompress_after_embed_prefix(compressed_full, dictionary, lc, la)
        if payload is None:
            return None
        return payload, {
            "comment_bits": compressed_full[:lc],
            "angle_bits": angle_bits,
            "lc": lc,
            "la": la,
            "method": compress_payload(payload, dictionary).get("method"),
        }

    n_comment_guesses = 1 << lc if lc > 0 else 1

    def _accepts(candidate: str, check: Dict[str, Any], b_comment: str) -> bool:
        cfull = check.get("compressed", "")
        if not isinstance(cfull, str):
            return False
        prefix = b_comment + angle_bits
        if not cfull.startswith(prefix):
            return False
        recovered = decompress_after_embed_prefix(cfull, dictionary, lc, la)
        return recovered == candidate

    best: Optional[Tuple[str, Dict[str, Any], int]] = None

    for guess in range(n_comment_guesses):
        b_comment = format(guess, f"0{lc}b") if lc > 0 else ""
        prefix = b_comment + angle_bits

        # Standard mode completion: first bit of full compressed is '0'.
        if prefix and prefix[0] == "0":
            utf8_partial = prefix[1:]
            for pad in range(0, max_padding_bits + 1):
                extended = utf8_partial + ("0" * pad)
                candidate = _decompress_standard_suffix(extended)
                if candidate is None:
                    continue
                check = compress_payload(candidate, dictionary)
                if not _accepts(candidate, check, b_comment):
                    continue
                cfull = check.get("compressed", "")
                assert isinstance(cfull, str)
                meta = {
                    "comment_bits": b_comment,
                    "angle_bits": angle_bits,
                    "lc": lc,
                    "la": la,
                    "method": check.get("method"),
                }
                score = len(cfull)
                if best is None or score < best[2]:
                    best = (candidate, meta, score)

        # Dictionary mode: first bit is '1'.
        if prefix and prefix[0] == "1":
            body_partial = prefix[1:]
            for pad in range(0, max_padding_bits + 1):
                extended = body_partial + ("0" * pad)
                candidate = _decompress_dictionary_bitstream(extended, dictionary)
                if candidate is None:
                    continue
                check = compress_payload(candidate, dictionary)
                if not _accepts(candidate, check, b_comment):
                    continue
                cfull = check.get("compressed", "")
                assert isinstance(cfull, str)
                meta = {
                    "comment_bits": b_comment,
                    "angle_bits": angle_bits,
                    "lc": lc,
                    "la": la,
                    "method": check.get("method"),
                }
                score = len(cfull)
                if best is None or score < best[2]:
                    best = (candidate, meta, score)

    if best is None:
        return None
    return best[0], best[1]
