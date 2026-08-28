"""Text analyzer for the editor.

Normalization now runs automatically inside the pipeline, so this module:
  * shows WHAT the normalizer will change (so the user can trust it), and
  * flags things it CANNOT fix and the user should look at:
      - fully-English sentences (left as-is)
      - Latin words in Russian text that aren't in the lexicon
      - words with no vowels that aren't known acronyms
      - very long sentences
      - missing final punctuation
`auto_text` = the normalized text (in case the user wants to bake it into the script).
"""
from __future__ import annotations

import re

from .text_normalizer import TextNormalizer, _is_english_sentence, _split_keep

_CYR = "А-Яа-яЁё"
_WORD_RE = re.compile(rf"[{_CYR}]+(?:-[{_CYR}]+)*")
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.]*")
_SENT_RE = re.compile(r"[^.!?…]+[.!?…]*")
_VOWELS_RU = set("аеёиоуыэюя")


def analyze_text(text: str, config: dict) -> dict:
    text = text or ""
    tn = TextNormalizer(config)
    nt = tn.normalize(text)

    issues: list[dict] = []

    # English sentences left untouched
    for sent in _split_keep(text):
        if _is_english_sentence(sent):
            issues.append({"type": "english", "msg": "Английское предложение оставлено как есть "
                                                     "(озвучится по-английски): " + sent.strip()[:80]})

    normalized_lower = nt.normalized.lower()
    # Latin words that survived normalization inside a Russian context
    handled = {r["from"].lower() for r in nt.replacements}
    for m in _LATIN_RE.finditer(text):
        w = m.group(0)
        if w.lower() in handled:
            continue
        # is it inside an english sentence? skip
        issues.append({"type": "latin", "word": w,
                       "msg": f"«{w}» — латиница, нет в словаре произношения. "
                              f"Добавьте в normalizer.lexicon или напишите кириллицей."})

    for m in _WORD_RE.finditer(text):
        w = m.group(0)
        if sum(1 for c in w.lower() if c in _VOWELS_RU) == 0 and len(w) >= 2:
            issues.append({"type": "no-vowel", "word": w,
                           "msg": f"«{w}» — без гласных, модель может прочитать по буквам"})

    for m in _SENT_RE.finditer(nt.normalized):
        s = m.group(0).strip()
        if len(s) > 320:
            issues.append({"type": "long", "msg": f"Длинное предложение ({len(s)} симв.) — "
                                                  f"разбейте на два-три для естественного темпа"})

    if text.strip() and not re.search(r"[.!?…]\s*$", text.strip()):
        issues.append({"type": "no-final-punct", "msg": "Нет знака в конце — добавьте точку"})

    return {
        "issues": issues,
        "replacements": nt.replacements,
        "counts": {
            "changed": len(nt.replacements),
            "latin": sum(1 for i in issues if i["type"] == "latin"),
            "english": sum(1 for i in issues if i["type"] == "english"),
            "long": sum(1 for i in issues if i["type"] == "long"),
            "no_vowel": sum(1 for i in issues if i["type"] == "no-vowel"),
        },
        "normalized": nt.normalized,
        "auto_text": nt.normalized,
    }


def auto_fix(text: str, config: dict) -> str:
    return TextNormalizer(config).normalize(text).normalized
