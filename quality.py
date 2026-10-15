#!/usr/bin/env python3
"""VexUserCheck v3.2 - Username quality scoring.

Scores a username from 0 (worthless) to 100 (premium) using a weighted
rubric that combines:
  · Length           (3-4 char names are premium)
  · Vowel balance    (natural language: 30-50% vowels)
  · Digit density    (each digit costs points)
  · Special density  (each '_' or '.' costs points)
  · Pronounceability (consecutive consonant/vowel runs)
  · Dictionary match (common English/loan words earn a bonus)

The scorer is deterministic, fast (<1µs per name), and dependency-free.
"""

from __future__ import annotations

import re
from functools import lru_cache

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VOWELS = frozenset("aeiou")

# A curated list of high-value short names. Extend as needed.
# These get a +25 bonus on top of their base score.
_PREMIUM_WORDS = frozenset({
    "ace", "arc", "art", "ash", "awe", "axe", "bay", "bee", "bit", "box",
    "boy", "bug", "cat", "chi", "cod", "cog", "cry", "cub", "cut", "dad",
    "day", "den", "dew", "dig", "dog", "dot", "duo", "ear", "eel", "egg",
    "ego", "elf", "elk", "elm", "end", "era", "eye", "fan", "fax", "fay",
    "fig", "fin", "fit", "fix", "flu", "fly", "fog", "fox", "fry", "fun",
    "fur", "gap", "gas", "gem", "gig", "gin", "god", "gun", "guy", "gym",
    "ham", "hay", "hem", "hen", "hex", "hip", "hit", "hue", "hug", "ice",
    "ink", "inn", "ion", "ivy", "jab", "jam", "jar", "jaw", "jay", "jet",
    "jig", "job", "jog", "jot", "joy", "jug", "keg", "key", "kid", "kin",
    "kit", "lab", "lad", "lag", "lap", "law", "leg", "lid", "lie", "lip",
    "log", "lot", "low", "mad", "man", "map", "mat", "men", "mix", "mob",
    "mom", "mop", "mud", "mug", "nap", "net", "new", "nil", "nod", "nor",
    "now", "nut", "oak", "oar", "odd", "off", "oil", "old", "one", "orb",
    "ore", "our", "out", "owe", "owl", "own", "pad", "pan", "par", "pat",
    "paw", "pay", "pea", "peg", "pen", "pet", "pie", "pig", "pin", "pit",
    "pod", "pop", "pot", "pro", "pub", "pug", "pun", "pup", "put", "rag",
    "ram", "ran", "rap", "rat", "raw", "ray", "red", "rib", "rid", "rig",
    "rim", "rip", "rob", "rod", "roe", "rot", "row", "rub", "rug", "rum",
    "run", "rut", "sad", "sag", "sap", "sat", "saw", "say", "sea", "see",
    "set", "sew", "she", "shy", "sin", "sip", "sir", "sit", "six", "ski",
    "sky", "sly", "sob", "sod", "son", "sow", "soy", "spa", "spy", "sub",
    "sum", "sun", "tab", "tag", "tan", "tap", "tar", "tax", "tea", "ten",
    "the", "tie", "tin", "tip", "toe", "ton", "too", "top", "tow", "toy",
    "try", "tub", "tug", "two", "urn", "use", "van", "vat", "vet", "via",
    "vow", "war", "was", "wax", "way", "web", "wed", "wee", "wet", "who",
    "why", "wig", "win", "wit", "woe", "wok", "won", "woo", "wow", "yak",
    "yam", "yap", "yaw", "yea", "yes", "yet", "yew", "you", "zap", "zed",
    "zen", "zip", "zoo",
})

# Letters that look confusing next to each other
_CONFUSING_PAIRS = (
    "l1", "i1", "o0", "g9", "b8", "s5", "z2", "rn", "vv", "ww",
)

# Unicode-friendly regex for common 3-5 letter patterns
_WORD_LIKE = re.compile(r"^[a-z]+$")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_username(name: str) -> int:
    """Return a quality score from 0 (worthless) to 100 (premium).

    The score is the sum of five independent rubrics, capped at 100.
    """
    if not name:
        return 0

    name = name.strip().lower()
    n = len(name)

    if n == 0:
        return 0

    score = 0

    # --- Rubric 1: Length (max 40 pts) ---
    if n == 3:
        score += 40
    elif n == 4:
        score += 30
    elif n == 5:
        score += 20
    elif n == 6:
        score += 10
    elif n <= 8:
        score += 5

    # --- Rubric 2: Vowel balance (max 20 pts) ---
    alpha = [c for c in name if c.isalpha()]
    if alpha:
        vowel_count = sum(1 for c in alpha if c in _VOWELS)
        vowel_ratio = vowel_count / len(alpha)

        # Ideal 30-50% vowels
        if 0.30 <= vowel_ratio <= 0.50:
            score += 20
        elif 0.20 <= vowel_ratio < 0.30 or 0.50 < vowel_ratio <= 0.60:
            score += 12
        elif 0.10 <= vowel_ratio < 0.20 or 0.60 < vowel_ratio <= 0.70:
            score += 5

    # --- Rubric 3: No digits (max 15 pts) ---
    digit_count = sum(1 for c in name if c.isdigit())
    score += max(0, 15 - digit_count * 8)

    # --- Rubric 4: No special chars (max 10 pts) ---
    special_count = name.count("_") + name.count(".")
    score += max(0, 10 - special_count * 6)

    # --- Rubric 5: Pronounceability (max 15 pts) ---
    score += _pronounceability_bonus(name)

    # --- Bonus: Dictionary word ---
    if name in _PREMIUM_WORDS:
        score += 25

    # --- Penalty: Confusing pairs ---
    for pair in _CONFUSING_PAIRS:
        if pair in name:
            score -= 10

    # --- Penalty: 3+ consecutive consonants or vowels ---
    if _has_long_run(name):
        score -= 8

    # --- Penalty: Too many digits ---
    if digit_count > 2:
        score -= 15

    return max(0, min(100, score))


def score_batch(names: list[str]) -> list[tuple[str, int]]:
    """Score a batch of names, returning (name, score) tuples sorted DESC."""
    scored = [(n, score_username(n)) for n in names]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def filter_by_quality(
    names: list[str],
    min_score: int = 25,
) -> tuple[list[str], list[str]]:
    """Split *names* into (kept, dropped) based on quality threshold.

    Returns (kept, dropped) where kept is sorted by score DESC.
    """
    scored = score_batch(names)
    kept = [n for n, s in scored if s >= min_score]
    dropped = [n for n, s in scored if s < min_score]
    return kept, dropped


def score_to_tier(score: int) -> str:
    """Human-readable tier for a given score."""
    if score >= 85:
        return "premium"
    if score >= 65:
        return "excellent"
    if score >= 45:
        return "good"
    if score >= 25:
        return "fair"
    return "poor"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pronounceability_bonus(name: str) -> int:
    """Return 0-15 based on vowel/consonant alternation."""
    if not name:
        return 0

    # Count transitions: VC or CV patterns
    prev_vowel = None
    transitions = 0
    for c in name:
        if not c.isalpha():
            prev_vowel = None
            continue
        is_vowel = c in _VOWELS
        if prev_vowel is not None and is_vowel != prev_vowel:
            transitions += 1
        prev_vowel = is_vowel

    # Ideal alternation: len(name) - 1 transitions
    max_transitions = max(1, len(name) - 1)
    ratio = transitions / max_transitions

    if ratio >= 0.75:
        return 15
    if ratio >= 0.55:
        return 10
    if ratio >= 0.40:
        return 5
    return 0


def _has_long_run(name: str, max_run: int = 3) -> bool:
    """Return True if there's a run of >max_run same-type letters."""
    if len(name) < max_run + 1:
        return False

    run = 1
    prev_vowel = None
    for c in name:
        if not c.isalpha():
            prev_vowel = None
            run = 1
            continue
        is_vowel = c in _VOWELS
        if is_vowel == prev_vowel:
            run += 1
            if run > max_run:
                return True
        else:
            run = 1
        prev_vowel = is_vowel
    return False


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    samples = [
        "vex", "neo", "echo", "zen", "arc", "ace",
        "xyz", "qqq", "a_b_c", "123", "x9z",
        "phantom", "warrior", "sunset", "cloud9",
        "aaaeiou", "bcd", "storm", "frost", "blaze",
    ]
    for s in samples:
        print(f"{s:<12} → {score_username(s):>3}  ({score_to_tier(score_username(s))})")
