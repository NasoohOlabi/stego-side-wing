"""Shared steganographic bit-layer codec (sender + receiver).

Mirrors logic previously embedded in ``StegoPipeline`` for compression,
comment/angle embedding, and payload recovery after stripping embed prefixes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import secrets
import zlib
from itertools import product
from typing import Any, cast

from pydantic import validate_call

from workflows.contracts import PostAugmentation
from workflows.utils.text_utils import (
    build_post_text_dictionary,
    build_post_text_dictionary_report,
    flatten_comments,
)

MAX_LITERAL_LEN = 250
INVISIBLE_PAYLOAD_START = "\u2060\u2063\u2060"
INVISIBLE_PAYLOAD_END = "\u2063\u2060\u2063"
INVISIBLE_PAYLOAD_ZERO = "\u200c"
INVISIBLE_PAYLOAD_ONE = "\u200d"
INVISIBLE_PAYLOAD_LENGTH_BITS = 32
SECURE_PAYLOAD_V1_PREFIX = "swsec1."
SECURE_PAYLOAD_V2_PREFIX = "swsec2."
SECURE_PAYLOAD_NONCE_BYTES = 16
SECURE_PAYLOAD_MAC_BYTES = 16
FRAME_MAGIC = "1010010110100101"
FRAME_VERSION = 1
FRAME_VERSION_BITS = 8
FRAME_PAYLOAD_LENGTH_BITS = 64
FRAME_CRC32_BITS = 32
FRAME_HEADER_BITS = (
    len(FRAME_MAGIC) + FRAME_VERSION_BITS + FRAME_PAYLOAD_LENGTH_BITS + FRAME_CRC32_BITS
)


@validate_call
def encode_elias_gamma(value: int) -> str:
    """Encode a positive integer as a self-delimiting Elias-gamma code."""
    if value <= 0:
        raise ValueError("Elias-gamma values must be positive")
    binary = format(value, "b")
    return ("0" * (len(binary) - 1)) + binary


@validate_call
def decode_elias_gamma(bits: str, offset: int = 0) -> tuple[int, int] | None:
    """Decode one Elias-gamma integer and return its value and end offset."""
    if offset < 0 or offset >= len(bits):
        return None
    first_one = bits.find("1", offset)
    if first_one < 0:
        return None
    zero_count = first_one - offset
    end = first_one + zero_count + 1
    if end > len(bits):
        return None
    return int(bits[first_one:end], 2), end


@validate_call
def build_multi_frame_stream(payload_bits: str, frame_count: int) -> dict[str, Any]:
    """Build the compact count-and-length stream used by the multi-frame PoC."""
    if set(payload_bits) - {"0", "1"}:
        raise ValueError("Payload bits must contain only '0' and '1'")
    if not payload_bits:
        raise ValueError("Multi-frame payload bits must not be empty")
    count_bits = encode_elias_gamma(frame_count)
    length_bits = encode_elias_gamma(len(payload_bits))
    control_bits = count_bits + length_bits
    return {
        "stream_bits": control_bits + payload_bits,
        "control_bits": control_bits,
        "frame_count": frame_count,
        "payload_bit_length": len(payload_bits),
    }


@validate_call
def parse_multi_frame_stream(bits: str, expected_frame_count: int) -> dict[str, Any] | None:
    """Parse a compact PoC stream, requiring declared count and zero padding."""
    count = decode_elias_gamma(bits)
    if count is None:
        return None
    frame_count, offset = count
    length = decode_elias_gamma(bits, offset)
    if length is None:
        return None
    payload_bit_length, payload_start = length
    payload_end = payload_start + payload_bit_length
    if frame_count != expected_frame_count or payload_end > len(bits):
        return None
    padding_bits = bits[payload_end:]
    if set(padding_bits) - {"0"}:
        return None
    return {
        "frame_count": frame_count,
        "payload_bit_length": payload_bit_length,
        "payload_bits": bits[payload_start:payload_end],
        "control_bits": bits[:payload_start],
        "padding_bits": padding_bits,
    }


@validate_call
def is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and len(value) > 0


@validate_call
def to_binary_utf8(text: str) -> str:
    return "".join(format(b, "08b") for b in text.encode("utf-8"))


@validate_call
def from_binary_utf8(bits: str) -> str | None:
    if len(bits) % 8 != 0:
        return None
    try:
        payload = bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))
        return payload.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


def _b64_urlsafe(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_urlsafe_decode(text: str) -> bytes | None:
    try:
        padded = text + ("=" * (-len(text) % 4))
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return None


def _xor_stream(secret: str, nonce: bytes, length: int) -> bytes:
    secret_bytes = secret.encode("utf-8")
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            secret_bytes,
            nonce + counter.to_bytes(4, "big"),
            hashlib.sha256,
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _protect_hmac_xor_v1(payload: str, secret: str) -> str:
    nonce = secrets.token_bytes(SECURE_PAYLOAD_NONCE_BYTES)
    payload_bytes = payload.encode("utf-8")
    stream = _xor_stream(secret, nonce, len(payload_bytes))
    ciphertext = bytes(a ^ b for a, b in zip(payload_bytes, stream, strict=True))
    mac = hmac.new(
        secret.encode("utf-8"),
        b"swsec1" + nonce + ciphertext,
        hashlib.sha256,
    ).digest()[:SECURE_PAYLOAD_MAC_BYTES]
    return (
        f"{SECURE_PAYLOAD_V1_PREFIX}{_b64_urlsafe(nonce)}."
        f"{_b64_urlsafe(ciphertext)}.{_b64_urlsafe(mac)}"
    )


def _protect_secure_compact_v2(payload: str, secret: str) -> str:
    nonce = secrets.token_bytes(SECURE_PAYLOAD_NONCE_BYTES)
    compressed = zlib.compress(payload.encode("utf-8"), level=9)
    stream = _xor_stream(secret, nonce, len(compressed))
    ciphertext = bytes(a ^ b for a, b in zip(compressed, stream, strict=True))
    mac = hmac.new(
        secret.encode("utf-8"),
        b"swsec2" + nonce + ciphertext,
        hashlib.sha256,
    ).digest()[:SECURE_PAYLOAD_MAC_BYTES]
    frame = nonce + ciphertext + mac
    return f"{SECURE_PAYLOAD_V2_PREFIX}{_b64_urlsafe(frame)}"


def _unprotect_hmac_xor_v1(protected_payload: str, secret: str) -> str | None:
    if not protected_payload.startswith(SECURE_PAYLOAD_V1_PREFIX):
        return None
    parts = protected_payload[len(SECURE_PAYLOAD_V1_PREFIX) :].split(".")
    if len(parts) != 3:
        return None
    nonce = _b64_urlsafe_decode(parts[0])
    ciphertext = _b64_urlsafe_decode(parts[1])
    supplied_mac = _b64_urlsafe_decode(parts[2])
    if nonce is None or ciphertext is None or supplied_mac is None:
        return None
    expected_mac = hmac.new(
        secret.encode("utf-8"),
        b"swsec1" + nonce + ciphertext,
        hashlib.sha256,
    ).digest()[:SECURE_PAYLOAD_MAC_BYTES]
    if not hmac.compare_digest(supplied_mac, expected_mac):
        return None
    stream = _xor_stream(secret, nonce, len(ciphertext))
    payload_bytes = bytes(a ^ b for a, b in zip(ciphertext, stream, strict=True))
    try:
        return payload_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _unprotect_secure_compact_v2(protected_payload: str, secret: str) -> str | None:
    if not protected_payload.startswith(SECURE_PAYLOAD_V2_PREFIX):
        return None
    frame = _b64_urlsafe_decode(protected_payload[len(SECURE_PAYLOAD_V2_PREFIX) :])
    if frame is None:
        return None
    min_len = SECURE_PAYLOAD_NONCE_BYTES + SECURE_PAYLOAD_MAC_BYTES
    if len(frame) <= min_len:
        return None
    nonce = frame[:SECURE_PAYLOAD_NONCE_BYTES]
    supplied_mac = frame[-SECURE_PAYLOAD_MAC_BYTES:]
    ciphertext = frame[SECURE_PAYLOAD_NONCE_BYTES:-SECURE_PAYLOAD_MAC_BYTES]
    expected_mac = hmac.new(
        secret.encode("utf-8"),
        b"swsec2" + nonce + ciphertext,
        hashlib.sha256,
    ).digest()[:SECURE_PAYLOAD_MAC_BYTES]
    if not hmac.compare_digest(supplied_mac, expected_mac):
        return None
    stream = _xor_stream(secret, nonce, len(ciphertext))
    compressed = bytes(a ^ b for a, b in zip(ciphertext, stream, strict=True))
    try:
        payload_bytes = zlib.decompress(compressed)
        return payload_bytes.decode("utf-8")
    except (UnicodeDecodeError, zlib.error):
        return None


@validate_call
def protect_payload(payload: str, transform: str = "plain", secret: str | None = None) -> str:
    """Apply the configured reversible payload transform before embedding."""
    if transform == "plain":
        return payload
    if transform not in {"hmac_xor_v1", "secure_compact_v2"}:
        raise ValueError(f"Unsupported payload transform: {transform}")
    if not secret:
        raise ValueError(f"WORKFLOW_ENCODING_SECRET is required for {transform} payloads")
    if transform == "hmac_xor_v1":
        return _protect_hmac_xor_v1(payload, secret)
    return _protect_secure_compact_v2(payload, secret)


@validate_call
def unprotect_payload(
    protected_payload: str, transform: str = "plain", secret: str | None = None
) -> str | None:
    """Reverse ``protect_payload``; returns ``None`` on invalid secure payloads."""
    if transform == "plain":
        return protected_payload
    if transform not in {"hmac_xor_v1", "secure_compact_v2"} or not secret:
        return None
    if transform == "hmac_xor_v1":
        return _unprotect_hmac_xor_v1(protected_payload, secret)
    return _unprotect_secure_compact_v2(protected_payload, secret)


# The invisible-carrier helpers below are READ-ONLY on purpose. AGENTS.md forbids embedding
# payloads in zero-width / invisible / control-format Unicode, so there is deliberately no
# write-side counterpart: `extract_invisible_payload` and `strip_invisible_payload` exist only
# to detect and clean up legacy artifacts produced before that rule, and to assert in tests
# that current output carries no invisible payload.


def _split_invisible_payload(text: str) -> tuple[str, str | None]:
    start = text.find(INVISIBLE_PAYLOAD_START)
    if start < 0:
        return text, None
    payload_start = start + len(INVISIBLE_PAYLOAD_START)
    end = text.find(INVISIBLE_PAYLOAD_END, payload_start)
    if end < 0:
        return text, None
    visible = text[:start] + text[end + len(INVISIBLE_PAYLOAD_END) :]
    return visible, text[payload_start:end]


def _decode_invisible_payload_chars(payload_chars: str) -> str | None:
    bits_chars = set(payload_chars)
    if not bits_chars.issubset({INVISIBLE_PAYLOAD_ZERO, INVISIBLE_PAYLOAD_ONE}):
        return None
    bits = "".join("1" if ch == INVISIBLE_PAYLOAD_ONE else "0" for ch in payload_chars)
    if len(bits) < INVISIBLE_PAYLOAD_LENGTH_BITS:
        return None
    payload_len = int(bits[:INVISIBLE_PAYLOAD_LENGTH_BITS], 2)
    total_bits = INVISIBLE_PAYLOAD_LENGTH_BITS + (payload_len * 8)
    if len(bits) != total_bits:
        return None
    payload_bits = bits[INVISIBLE_PAYLOAD_LENGTH_BITS:]
    try:
        payload_bytes = bytes(
            int(payload_bits[idx : idx + 8], 2) for idx in range(0, len(payload_bits), 8)
        )
        return payload_bytes.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


@validate_call
def strip_invisible_payload(text: str) -> str:
    """Legacy migration-only helper for cleaning old artifacts."""
    visible, _ = _split_invisible_payload(text)
    return visible


@validate_call
def extract_invisible_payload(text: str) -> str | None:
    """Legacy migration-only helper for reading old artifacts."""
    _, payload_chars = _split_invisible_payload(text)
    if payload_chars is None:
        return None
    return _decode_invisible_payload_chars(payload_chars)


@validate_call
def get_bit_width(max_value: int) -> int:
    if max_value <= 0:
        return 0
    return 1 if max_value == 1 else math.ceil(math.log2(max_value + 1))


def _bits_crc32(bits: str) -> int:
    return zlib.crc32(bits.encode("ascii")) & 0xFFFFFFFF


@validate_call
def frame_payload_bits(payload_bits: str) -> str:
    if set(payload_bits) - {"0", "1"}:
        raise ValueError("Payload bits must contain only '0' and '1'")
    length_bits = format(len(payload_bits), f"0{FRAME_PAYLOAD_LENGTH_BITS}b")
    crc_bits = format(_bits_crc32(payload_bits), f"0{FRAME_CRC32_BITS}b")
    version_bits = format(FRAME_VERSION, f"0{FRAME_VERSION_BITS}b")
    return f"{FRAME_MAGIC}{version_bits}{length_bits}{crc_bits}{payload_bits}"


@validate_call
def parse_framed_payload_bits(bits: str) -> dict[str, Any]:
    if len(bits) < FRAME_HEADER_BITS:
        raise ValueError("Framed payload is shorter than the header")
    cursor = 0
    magic = bits[cursor : cursor + len(FRAME_MAGIC)]
    cursor += len(FRAME_MAGIC)
    if magic != FRAME_MAGIC:
        raise ValueError("Invalid framed payload magic")
    version = int(bits[cursor : cursor + FRAME_VERSION_BITS], 2)
    cursor += FRAME_VERSION_BITS
    if version != FRAME_VERSION:
        raise ValueError("Unsupported framed payload version")
    payload_bit_length = int(bits[cursor : cursor + FRAME_PAYLOAD_LENGTH_BITS], 2)
    cursor += FRAME_PAYLOAD_LENGTH_BITS
    expected_crc32 = int(bits[cursor : cursor + FRAME_CRC32_BITS], 2)
    cursor += FRAME_CRC32_BITS
    payload_end = cursor + payload_bit_length
    if len(bits) < payload_end:
        raise ValueError("Framed payload is truncated")
    payload_bits = bits[cursor:payload_end]
    actual_crc32 = _bits_crc32(payload_bits)
    if actual_crc32 != expected_crc32:
        raise ValueError("Framed payload checksum mismatch")
    return {
        "magic": magic,
        "version": version,
        "payload_bit_length": payload_bit_length,
        "expected_crc32": expected_crc32,
        "actual_crc32": actual_crc32,
        "payload_bits": payload_bits,
        "header_bits": bits[:FRAME_HEADER_BITS],
        "payload_end_offset": payload_end,
        "padding_bits": bits[payload_end:],
    }


@validate_call
def encode_int(value: int, max_value: int) -> str:
    width = get_bit_width(max_value)
    return "" if width == 0 else format(value, f"0{width}b")


@validate_call
def take_bits(bits: str, count: int) -> tuple[str, str, bool]:
    if count <= 0:
        return "", bits, False
    if len(bits) >= count:
        return bits[:count], bits[count:], False
    return bits.ljust(count, "0"), "", True


@validate_call
def build_dictionary(post: dict[str, Any]) -> list[str]:
    dictionary = build_post_text_dictionary(post, apply_capacity_profile=True)
    return [entry for entry in dictionary if is_non_empty_string(entry)]


@validate_call
def build_dictionary_report(post: dict[str, Any]) -> dict[str, Any]:
    """Source-aware deterministic dictionary metadata shared by sender and receiver."""
    return build_post_text_dictionary_report(post, apply_capacity_profile=True)


@validate_call
def compress_payload(payload: str, dictionary: list[str]) -> dict[str, Any]:
    """Same semantics as legacy ``StegoPipeline._compress_payload``."""
    std_binary = to_binary_utf8(payload)
    std_length = 1 + len(std_binary)

    n = len(payload)
    max_dict_index = len(dictionary)
    global_max_match = 0
    for text in dictionary:
        if len(text) > global_max_match:
            global_max_match = len(text)

    matches: dict[int, list[dict[str, int]]] = {}
    if n > 0 and dictionary:
        for i in range(n):
            current_char = payload[i]
            matches_at_i: list[dict[str, int]] = []
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
    choice: list[dict[str, Any] | None] = [None] * n
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
            cost = 1 + bw_dict_idx + doc_len_bits + bw_match_len + dp[i + match["len"]]
            if cost < dp[i]:
                dp[i] = cost
                choice[i] = {"kind": "dict", **match}

    curr = 0
    dict_binary_parts: list[str] = []
    references: list[dict[str, Any]] = []

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


def _comment_id_aliases(comment_id: Any) -> list[str]:
    if not isinstance(comment_id, str):
        return []
    aliases = [comment_id]
    if "_" in comment_id:
        aliases.append(comment_id.split("_", 1)[1])
    return aliases


def _comment_lookup_by_id(comments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    comment_map: dict[str, dict[str, Any]] = {}
    for comment in comments:
        for alias in _comment_id_aliases(comment.get("id")):
            comment_map[alias] = comment
    return comment_map


def _comment_chain_entry(comment: dict[str, Any]) -> dict[str, Any]:
    author = comment.get("author")
    return {
        "name": author if isinstance(author, str) and author.strip() else "Unknown",
        "body": comment.get("body") if isinstance(comment.get("body"), str) else "",
        "id": comment.get("id"),
        "parent_id": comment.get("parent_id"),
        "permalink": comment.get("permalink"),
    }


def _picked_comment_chain(
    picked_comment: dict[str, Any],
    comment_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    picked_chain: list[dict[str, Any]] = []
    current = picked_comment
    visited: set[str] = set()

    while True:
        current_id = str(current.get("id", ""))
        if current_id in visited:
            break
        visited.add(current_id)
        picked_chain.insert(0, _comment_chain_entry(current))

        parent_id = current.get("parent_id")
        if parent_id == current.get("link_id"):
            break

        parent = comment_map.get(str(parent_id))
        if parent is None and isinstance(parent_id, str) and "_" in parent_id:
            parent = comment_map.get(parent_id.split("_", 1)[1])
        if parent is None or parent is current:
            break
        current = parent

    return picked_chain


def comment_selection_choice_count(post: dict[str, Any]) -> int:
    return len(flatten_comments(post.get("comments", []))) + 1


def comment_selection_index(post: dict[str, Any], parent_id: Any) -> int:
    if not isinstance(parent_id, str) or not parent_id.strip():
        return 0
    parent_aliases = set(_comment_id_aliases(parent_id))
    for idx, comment in enumerate(flatten_comments(post.get("comments", [])), start=1):
        if parent_aliases & set(_comment_id_aliases(comment.get("id"))):
            return idx
    return 0


def comment_bit_aliases_for_index(idx: int, post: dict[str, Any]) -> list[str]:
    width = comment_selection_bit_width(post)
    choice_count = comment_selection_choice_count(post)
    if width <= 0:
        return [""]
    target = idx % choice_count
    return [
        format(value, f"0{width}b")
        for value in range(1 << width)
        if (value if value <= choice_count - 1 else value % choice_count) == target
    ]


def embed_in_comment_selection(bits: str, post: dict[str, Any]) -> dict[str, Any]:
    flattened_comments = flatten_comments(post.get("comments", []))
    n = len(flattened_comments)
    bits_count = get_bit_width(n)
    bits_used, remaining, insufficient = take_bits(bits, bits_count)
    selection_index = int(bits_used or "0", 2)
    if selection_index > n:
        selection_index %= n + 1

    picked_chain: list[dict[str, Any]] = []
    if selection_index > 0 and n > 0:
        picked_comment = flattened_comments[selection_index - 1]
        picked_chain = _picked_comment_chain(
            picked_comment,
            _comment_lookup_by_id(flattened_comments),
        )

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


def _nested_angle_groups(raw_angles: Any) -> list[list[dict[str, Any]]]:
    if not isinstance(raw_angles, list):
        return []
    groups: list[list[dict[str, Any]]] = []
    for raw_group in raw_angles:
        if raw_group is None:
            continue
        group = raw_group if isinstance(raw_group, list) else [raw_group]
        angles = [angle for angle in group if isinstance(angle, dict)]
        if angles:
            groups.append(angles)
    return groups


def flatten_angle_groups(nested_angles: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    angles: list[dict[str, Any]] = []
    for angle_group in nested_angles:
        for angle in angle_group:
            with_idx = dict(angle)
            with_idx["idx"] = len(angles)
            angles.append(with_idx)
    return angles


def flatten_nested_angles(post: dict[str, Any]) -> list[dict[str, Any]]:
    return flatten_angle_groups(_nested_angle_groups(post.get("angles", [])))


def embed_in_angle_selection(
    bits: str, nested_angles: list[list[dict[str, Any]]]
) -> dict[str, Any]:
    angles = flatten_angle_groups(nested_angles)

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


def _selection_embedding_fields(bits: str, post: dict[str, Any]) -> dict[str, Any]:
    nested_angles = _nested_angle_groups(post.get("angles", []))
    comment_emb = embed_in_comment_selection(bits, post)
    angle_emb = embed_in_angle_selection(comment_emb["remainingBits"], nested_angles)
    selection_signature = comment_emb["result"]["bitsUsed"] + angle_emb["bitsUsed"]

    warnings: list[str] = []
    if comment_emb["result"].get("insufficientBits"):
        warnings.append("Padding used in Comment Selection.")
    if angle_emb.get("insufficientBits"):
        warnings.append("Padding used in Angle Selection.")

    return {
        "commentEmbedding": comment_emb["result"],
        "angleEmbedding": angle_emb,
        "totalBitsEmbedded": comment_emb["result"]["bitsCount"] + angle_emb["bitsCount"],
        "fullEncodedBits": selection_signature,
        "commentBits": comment_emb["result"]["bitsUsed"],
        "angleBits": angle_emb["bitsUsed"],
        "selectionSignature": selection_signature,
        "warnings": warnings,
        "_remainingBitsUnembedded": len(angle_emb.get("remainingBits", "")),
    }


def augment_post(payload: str, post: dict[str, Any]) -> PostAugmentation:
    dictionary = build_dictionary(post)
    compression = compress_payload(payload, dictionary)
    warnings: list[str] = []
    if compression.get("method") == "standard":
        warnings.append("Dictionary compression inefficient; used standard encoding.")

    selection = _selection_embedding_fields(compression["compressed"], post)
    warnings.extend(selection.pop("warnings"))
    remaining_channel_bits = int(selection.pop("_remainingBitsUnembedded"))
    if remaining_channel_bits > 0:
        warnings.append(
            "Selection channel did not carry the full compressed payload; "
            "remaining bits require multi-post framing or audit-assisted recovery."
        )

    # A dict literal with a trailing ``**`` spread can't be checked structurally against a
    # TypedDict, so pyright falls back to inferring a plain dict here -- cast documents the
    # contract without changing how the dict is actually built.
    return cast(
        PostAugmentation,
        {
            "compression": compression,
            **selection,
            "remainingBitsUnembedded": remaining_channel_bits,
            "warnings": warnings,
        },
    )


def augment_post_with_selection_bits(bits: str, post: dict[str, Any]) -> PostAugmentation:
    """Diagnostic-only augmentation from already prepared selection-channel bits."""
    if set(bits) - {"0", "1"}:
        raise ValueError("Selection bits must contain only '0' and '1'")

    selection = _selection_embedding_fields(bits, post)
    selection.pop("_remainingBitsUnembedded")

    return cast(
        PostAugmentation,
        {
            "compression": {
                "method": "diagnostic_binary_selection_bits",
                "payload": bits,
                "compressed": bits,
                "compressedLength": len(bits),
                "originalLength": len(bits),
                "ratio": 1.0,
                "references": [],
                "compressionSkipped": True,
            },
            **selection,
            "diagnostic": {
                "binary_selection_bits": bits,
                "compression_skipped": True,
                "payload_transform_skipped": True,
            },
        },
    )


def selection_channel_capacity(post: dict[str, Any]) -> int:
    """Maximum number of selection bits that can be carried by one post."""
    nested_angles = _nested_angle_groups(post.get("angles", []))
    comment_bits = comment_selection_bit_width(post)
    angle_bits = angle_selection_bit_width(len(flatten_angle_groups(nested_angles)))
    return comment_bits + angle_bits


def _recoverable_width(choice_count: int) -> int:
    return int(math.log2(choice_count)) if choice_count > 1 else 0


def recoverable_selection_channel_capacity(post: dict[str, Any]) -> int:
    """Lossless capacity that excludes modulo-alias selection bit patterns."""
    return int(selection_channel_capacity_report(post)["recoverable_capacity_bits"])


@validate_call
def selection_channel_capacity_report(post: dict[str, Any]) -> dict[str, int]:
    """Describe physical and lossless capacity for one dynamic frame."""
    comment_choices = comment_selection_choice_count(post)
    tangent_choices = len(flatten_angle_groups(_nested_angle_groups(post.get("angles", []))))
    comment_safe_width = _recoverable_width(comment_choices)
    tangent_safe_width = _recoverable_width(tangent_choices)
    return {
        "comment_choices": comment_choices,
        "tangent_choices": tangent_choices,
        "comment_physical_width": comment_selection_bit_width(post),
        "tangent_physical_width": angle_selection_bit_width(tangent_choices),
        "comment_recoverable_bits": comment_safe_width,
        "tangent_recoverable_bits": tangent_safe_width,
        "recoverable_capacity_bits": comment_safe_width + tangent_safe_width,
    }


def _canonical_channel_bits(bits: str, safe_width: int, physical_width: int) -> tuple[str, str]:
    selected, remaining, _ = take_bits(bits, safe_width)
    value = int(selected or "0", 2)
    return format(value, f"0{physical_width}b") if physical_width else "", remaining


def augment_post_with_recoverable_selection_bits(
    bits: str, post: dict[str, Any]
) -> PostAugmentation:
    """Embed bits through only one-to-one comment and angle selection states."""
    comment_safe_width = _recoverable_width(comment_selection_choice_count(post))
    nested_angles = _nested_angle_groups(post.get("angles", []))
    angle_count = len(flatten_angle_groups(nested_angles))
    angle_safe_width = _recoverable_width(angle_count)
    comment_bits, remaining = _canonical_channel_bits(
        bits, comment_safe_width, comment_selection_bit_width(post)
    )
    angle_bits, _ = _canonical_channel_bits(
        remaining, angle_safe_width, angle_selection_bit_width(angle_count)
    )
    result = augment_post_with_selection_bits(comment_bits + angle_bits, post)
    result["recoverableBits"] = bits[: comment_safe_width + angle_safe_width]
    return result


def recoverable_frame_bit_candidates_from_observations(
    *, post: dict[str, Any], parent_id: Any, decoded_angle_index: int, n_angles: int
) -> list[str]:
    """Recover the unique lossless selection representation for one frame."""
    comment_safe_width = _recoverable_width(comment_selection_choice_count(post))
    angle_safe_width = _recoverable_width(n_angles)
    comment_index = comment_selection_index(post, parent_id)
    if comment_index >= (1 << comment_safe_width):
        return []
    if decoded_angle_index < 0 or decoded_angle_index >= (1 << angle_safe_width):
        return []
    comment_bits = format(comment_index, f"0{comment_safe_width}b") if comment_safe_width else ""
    angle_bits = format(decoded_angle_index, f"0{angle_safe_width}b") if angle_safe_width else ""
    return [comment_bits + angle_bits]


def frame_bits_across_posts(bits: str, posts: list[dict[str, Any]]) -> dict[str, Any]:
    """Split an arbitrary bitstring across a sequence of posts.

    Each frame uses the existing single-post selection channel, so the caller can
    recover the full bitstring by concatenating ``frame["bitsUsed"]`` in order.
    """
    if set(bits) - {"0", "1"}:
        raise ValueError("Selection bits must contain only '0' and '1'")
    if not posts:
        return {
            "originalBits": bits,
            "originalBitsLength": len(bits),
            "frames": [],
            "remainingBits": bits,
        }

    remaining = bits
    frames: list[PostAugmentation] = []
    for index, post in enumerate(posts):
        if not remaining:
            break
        capacity = selection_channel_capacity(post)
        taken, remaining, _ = take_bits(remaining, capacity)
        framed = augment_post_with_selection_bits(taken, post)
        framed["frameIndex"] = index
        framed["postId"] = post.get("id")
        framed["selectionChannelCapacity"] = capacity
        frames.append(framed)

    return {
        "originalBits": bits,
        "originalBitsLength": len(bits),
        "frames": frames,
        "remainingBits": remaining,
    }


def recover_bits_from_post_frames(frames: list[dict[str, Any]]) -> str:
    """Reassemble a multi-post bitstring produced by ``frame_bits_across_posts``."""
    parts: list[str] = []
    for frame in frames:
        compression = frame.get("compression", {})
        if not isinstance(compression, dict):
            continue
        compressed = compression.get("compressed")
        if isinstance(compressed, str):
            parts.append(compressed)
    return "".join(parts)


def frame_bit_candidates_from_observations(
    *,
    post: dict[str, Any],
    parent_id: Any,
    decoded_angle_index: int,
    n_angles: int,
) -> list[str]:
    comment_idx = comment_selection_index(post, parent_id)
    comment_aliases = comment_bit_aliases_for_index(comment_idx, post)
    angle_aliases = angle_bit_aliases_for_index(decoded_angle_index, n_angles)
    return [f"{cb}{ab}" for cb, ab in product(comment_aliases, angle_aliases)]


def comment_selection_bit_width(post: dict[str, Any]) -> int:
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


def angle_bits_decode_to_index(bits: str, idx: int, n_angles: int) -> bool:
    if n_angles <= 0:
        return False
    if not bits:
        return angle_selection_bit_width(n_angles) == 0 and idx % n_angles == 0
    return int(bits, 2) % n_angles == idx % n_angles


def angle_bit_aliases_for_index(idx: int, n_angles: int) -> list[str]:
    width = angle_selection_bit_width(n_angles)
    if width <= 0 or n_angles <= 0:
        return [""]
    target = idx % n_angles
    return [
        format(value, f"0{width}b") for value in range(1 << width) if value % n_angles == target
    ]


def _decompress_standard_suffix(utf8_bit_suffix: str) -> str | None:
    if len(utf8_bit_suffix) % 8 != 0:
        return None
    try:
        data = bytes(int(utf8_bit_suffix[i : i + 8], 2) for i in range(0, len(utf8_bit_suffix), 8))
        return data.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None


def decompress_after_embed_prefix(
    compressed_full: str, dictionary: list[str], lc: int, la: int
) -> str | None:
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


def _read_fixed_int(bits: str, pos: int, max_value: int) -> tuple[int, int] | None:
    """Read integer using same width as ``encode_int(..., max_value)``."""
    w = get_bit_width(max_value)
    if w == 0:
        return 0, pos
    if pos + w > len(bits):
        return None
    chunk = bits[pos : pos + w]
    return int(chunk, 2), pos + w


def _read_utf8_n_chars(bits: str, pos: int, n_chars: int) -> tuple[str, int] | None:
    """Read UTF-8 bytes (8 bits each) until exactly ``n_chars`` Unicode chars decode."""
    if n_chars <= 0:
        return "", pos
    buf = bytearray()
    p = pos
    # UTF-8 uses at most 4 bytes per code point. Keep a small slack window so
    # malformed or padded bitstreams terminate instead of scanning unbounded input.
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


def _decompress_dictionary_bitstream(rem: str, dictionary: list[str]) -> str | None:
    if not rem:
        return ""
    if not dictionary:
        return None
    max_dict_index = len(dictionary)
    global_max_match = max(len(t) for t in dictionary)
    pos = 0
    out: list[str] = []

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
    dictionary: list[str],
    pre_sender_post: dict[str, Any],
    nested_angles: list[list[dict[str, Any]]],
    decoded_angle_index: int,
) -> tuple[str, dict[str, Any]] | None:
    """Recover payload when the full compressed bitstring from encode is known.

    This is audit-assisted recovery, not proof that one visible comment carried
    the whole payload. Pure stego recovery only observes the selection bits.
    """
    return _recover_known_compressed_full(
        compressed_full,
        dictionary,
        pre_sender_post,
        nested_angles,
        decoded_angle_index,
        include_method=False,
    )


def _recover_known_compressed_full(
    compressed_full: str,
    dictionary: list[str],
    pre_sender_post: dict[str, Any],
    nested_angles: list[list[dict[str, Any]]],
    decoded_angle_index: int,
    *,
    include_method: bool,
) -> tuple[str, dict[str, Any]] | None:
    lc = comment_selection_bit_width(pre_sender_post)
    angles = flatten_angle_groups(nested_angles)

    la = angle_selection_bit_width(len(angles))
    if len(angles) == 0:
        return None
    if len(compressed_full) < lc + la:
        return None
    actual_angle = compressed_full[lc : lc + la]
    if not angle_bits_decode_to_index(actual_angle, decoded_angle_index, len(angles)):
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
    if include_method:
        method = compress_payload(payload, dictionary).get("method")
        if isinstance(method, str):
            meta["method"] = method
    return payload, meta


def _recovery_candidate(
    candidate: str,
    *,
    dictionary: list[str],
    lc: int,
    la: int,
    prefix: str,
    comment_bits: str,
    angle_bits: str,
) -> tuple[str, dict[str, Any], int] | None:
    check = compress_payload(candidate, dictionary)
    cfull = check.get("compressed", "")
    if not isinstance(cfull, str) or not cfull.startswith(prefix):
        return None
    if decompress_after_embed_prefix(cfull, dictionary, lc, la) != candidate:
        return None
    meta = {
        "comment_bits": comment_bits,
        "angle_bits": angle_bits,
        "lc": lc,
        "la": la,
        "method": check.get("method"),
    }
    return candidate, meta, len(cfull)


def _shorter_recovery(
    current: tuple[str, dict[str, Any], int] | None,
    candidate: tuple[str, dict[str, Any], int] | None,
) -> tuple[str, dict[str, Any], int] | None:
    if candidate is None:
        return current
    if current is None or candidate[2] < current[2]:
        return candidate
    return current


def recover_payload_bruteforce_comment_bits(
    dictionary: list[str],
    pre_sender_post: dict[str, Any],
    nested_angles: list[list[dict[str, Any]]],
    decoded_angle_index: int,
    max_padding_bits: int = 256,
    *,
    compressed_full: str | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """
    Recover payload by brute-forcing the comment-selection prefix (small ``2**lc``).

    When ``compressed_full`` is provided, candidates must match it exactly.
    Otherwise we require ``compress_payload`` output to start with the embed prefix and
    ``decompress_after_embed_prefix`` to round-trip the candidate.
    """
    lc = comment_selection_bit_width(pre_sender_post)
    angles = flatten_angle_groups(nested_angles)

    la = angle_selection_bit_width(len(angles))
    if not angles:
        return None

    if compressed_full is not None:
        return _recover_known_compressed_full(
            compressed_full,
            dictionary,
            pre_sender_post,
            nested_angles,
            decoded_angle_index,
            include_method=True,
        )

    # This path is intentionally bounded for small selection channels. It is
    # not suitable for large payload search; multi-post framing should carry
    # the remaining bits explicitly instead.
    n_comment_guesses = 1 << lc if lc > 0 else 1
    angle_bits_candidates = angle_bit_aliases_for_index(decoded_angle_index, len(angles))
    best: tuple[str, dict[str, Any], int] | None = None

    for guess in range(n_comment_guesses):
        b_comment = format(guess, f"0{lc}b") if lc > 0 else ""
        for angle_bits in angle_bits_candidates:
            prefix = b_comment + angle_bits

            # Standard mode completion: first bit of full compressed is '0'.
            if prefix and prefix[0] == "0":
                utf8_partial = prefix[1:]
                for pad in range(0, max_padding_bits + 1):
                    candidate = _decompress_standard_suffix(utf8_partial + ("0" * pad))
                    if candidate is None:
                        continue
                    best = _shorter_recovery(
                        best,
                        _recovery_candidate(
                            candidate,
                            dictionary=dictionary,
                            lc=lc,
                            la=la,
                            prefix=prefix,
                            comment_bits=b_comment,
                            angle_bits=angle_bits,
                        ),
                    )

            # Dictionary mode: first bit is '1'.
            if prefix and prefix[0] == "1":
                body_partial = prefix[1:]
                for pad in range(0, max_padding_bits + 1):
                    candidate = _decompress_dictionary_bitstream(
                        body_partial + ("0" * pad),
                        dictionary,
                    )
                    if candidate is None:
                        continue
                    best = _shorter_recovery(
                        best,
                        _recovery_candidate(
                            candidate,
                            dictionary=dictionary,
                            lc=lc,
                            la=la,
                            prefix=prefix,
                            comment_bits=b_comment,
                            angle_bits=angle_bits,
                        ),
                    )

    if best is None:
        return None
    return best[0], best[1]
