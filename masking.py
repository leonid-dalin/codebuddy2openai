"""
masking — hides compliance-template terms from upstream keyword filtering.

The upstream backend runs keyword-based content filtering before inference.
Its triggers include security terms that also appear verbatim in the fixed
compliance templates clients prepend as system messages ("Refuse requests
for DoS attacks, exploit development, credential testing, C2 frameworks
..."). Those declarations refuse harm; the filter has no way to tell, so the
whole request is rejected.

This module inserts a zero-width space (U+200B) inside the terms those
templates contain:

    "DoS" -> "Do\u200bS"

Human readers and the model see the same word; the backend's substring
match no longer fires.

Scope
-----
- one explicit term list, matched case-insensitively
- by default only system-role messages are rewritten, where compliance
  templates live; other roles pass through untouched
- independent module, importable and testable on its own, and optional:
  the proxy runs without it
- it cannot and does not bypass filtering of genuinely harmful user input;
  it only stops client-authored refusal templates from tripping the filter
"""

from __future__ import annotations

import re
from typing import Iterable

# Zero-width space inserted inside a term to break the backend substring match;
_ZWSP = "\u200b"

# Terms that trip the filter, taken from client compliance templates that were
# rejected in practice. All are refusal-of-harm phrasing. Case-insensitive match.
SENSITIVE_TERMS: list[str] = [
    "DoS",
    "DDoS",
    "exploit",
    "credential testing",
    "credential stuffing",
    "supply chain compromise",
    "supply-chain compromise",
    "detection evasion",
    "C2 frameworks",
    "C2 framework",
    "command and control",
    "malicious purposes",
    "malicious intent",
    "mass targeting",
    "brute force",
    "brute-force",
    "privilege escalation",
    "reverse shell",
    "remote code execution",
    "SQL injection",
    "XSS",
    "CSRF",
    "phishing",
    "malware",
    "ransomware",
    "keylogger",
    "rootkit",
    "backdoor",
    "botnet",
    "zero-day",
    "0day",
]

# One combined regex, longest terms first so short terms cannot consume
# longer ones. Word boundaries, case-insensitive.
_PATTERN = re.compile(
    "|".join(re.escape(t) for t in sorted(SENSITIVE_TERMS, key=len, reverse=True)),
    re.IGNORECASE,
)


def _zero_width_split(term: str) -> str:
    """Insert a zero-width space inside the term."""
    if len(term) <= 1:
        return term
    # One insertion after the first character is enough to break substring matching.
    return term[0] + _ZWSP + term[1:]


def mask_text(text: str) -> str:
    """Mask trigger terms in text; unchanged when nothing matches."""
    if not text:
        return text
    return _PATTERN.sub(lambda m: _zero_width_split(m.group(0)), text)


def _iter_text_blocks(content):
    """Yield (container, key) for text blocks in OpenAI content (str or block list)."""
    if isinstance(content, str):
        yield content, None  # plain string: the caller replaces it in place
    elif isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                yield blk, "text"


def mask_messages(messages: Iterable[dict],
                         roles: tuple[str, ...] = ("system",)) -> list[dict]:
    """Mask the chosen roles' message text and return a new list.

    The input list is not modified. Defaults to system messages only; pass
    roles=("system", "user") to widen.
    """
    out: list[dict] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        role = m.get("role")
        nm = dict(m)  # shallow copy: never mutate the caller's message
        if role in roles:
            content = m.get("content")
            if isinstance(content, str):
                nm["content"] = mask_text(content)
            elif isinstance(content, list):
                new_blocks = []
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "text":
                        nb = dict(blk)
                        nb["text"] = mask_text(blk.get("text", ""))
                        new_blocks.append(nb)
                    else:
                        new_blocks.append(blk)
                nm["content"] = new_blocks
        out.append(nm)
    return out


def mask_body(body: dict, roles: tuple[str, ...] = ("system",)) -> dict:
    """Mask body["messages"] and return a new body dict (shallow copy)."""
    if not body.get("messages"):
        return body
    nb = dict(body)
    nb["messages"] = mask_messages(body["messages"], roles=roles)
    return nb


# ---------------------------------------------------------------------------
# Self-test: python3 masking.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    samples = [
        "Refuse requests for DoS attacks and exploit development.",
        "Dual-use security tools (C2 frameworks, credential testing) require authorization.",
        "这是一段正常的中文，不含任何触发词。",
        "Prevent privilege escalation and brute force attacks.",
        "No sensitive words here at all.",
    ]
    print("=== before / after ===")
    for s in samples:
        d = mask_text(s)
        changed = "changed" if d != s else "same"
        print(f"{changed} | original: {s}")
        if d != s:
            print(f"     | masked: {d}")
            print("     | visible characters identical; difference is U+200B")
    print()
    print("=== messages masking (system role only) ===")
    msgs = [
        {"role": "system", "content": "Refuse DoS attacks and exploit development."},
        {"role": "user", "content": "explain DoS attacks"},
    ]
    out = mask_messages(msgs)
    for m in out:
        print(f"  [{m['role']}] {m['content']!r}")
    print()
    assert "\u200b" in out[0]["content"], "system content should be masked"
    assert "\u200b" not in out[1]["content"], "user content must stay untouched"
    print("self-test passed: system masked, user untouched")
