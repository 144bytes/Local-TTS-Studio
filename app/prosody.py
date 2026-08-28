"""Text preparation: user text -> normalized text -> ordered [Segment].

Pipeline position:  UI text  ->  TextNormalizer  ->  stress marks  ->
semantic chunking  ->  context-aware pauses  ->  [Segment]  ->  Qwen.

Key design choices (see the calibration brief):
  * Qwen is called on LARGE semantic chunks (whole paragraphs when they fit),
    NOT sentence-by-sentence — this preserves prosodic continuity.
  * Pauses are inserted only at structural boundaries (paragraph / forced split),
    scaled by the preset and given a small random jitter so they don't sound
    mechanical. No silence is inserted inside a chunk — Qwen's own handling of
    . , ; : ? ! … — does that.
  * Delivery = the preset's `generation` params + `prosody` pause policy +
    a minimal `audio` touch (mostly gain). No pitch-shift, no per-segment
    time-stretch.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field, replace

from .text_normalizer import TextNormalizer

_ACUTE = "́"
_VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"


@dataclass
class Style:
    temperature: float = 0.85
    top_p: float = 1.0
    repetition_penalty: float = 1.05
    gain_db: float = 0.0
    eq: dict = field(default_factory=dict)
    compress: bool = False
    language: str = "ru"

    def clamped(self) -> "Style":
        return replace(
            self,
            temperature=_clamp(self.temperature, 0.3, 1.3),
            top_p=_clamp(self.top_p, 0.5, 1.0),
            repetition_penalty=_clamp(self.repetition_penalty, 1.0, 1.5),
            gain_db=_clamp(self.gain_db, -18.0, 12.0),
        )


@dataclass
class Segment:
    kind: str                 # "speech" | "silence"
    text: str = ""
    style: Style = field(default_factory=Style)
    duration_ms: int = 0
    boundary: str = ""        # "paragraph" | "split" (for silence segments, debug)

    @property
    def is_speech(self) -> bool:
        return self.kind == "speech"


@dataclass
class PreparedText:
    original: str
    normalized: str
    segments: list[Segment]
    warnings: list[str]
    replacements: list[dict]
    spoken_text: str
    n_words: int

    def plan(self) -> list[dict]:
        out = []
        for s in self.segments:
            if s.is_speech:
                out.append({"kind": "speech", "chars": len(s.text), "text": s.text})
            else:
                out.append({"kind": "silence", "ms": s.duration_ms, "boundary": s.boundary})
        return out


def _clamp(v, lo, hi):
    return max(lo, min(hi, float(v)))


_PARA_RE = re.compile(r"\n\s*\n+")
_DASH_LINE_RE = re.compile(r"^\s*[—–-]\s*$")
_SENTENCE_RE = re.compile(r"(?<=[.!?…])[\"'”»)\]]*(?=\s)")
_WORD_RE = re.compile(r"[^\s]+")
_BRACKET_RE = re.compile(r"\[[^\[\]\n]{0,60}\]")


class TextPrep:
    def __init__(self, config: dict):
        self.cfg = config
        self.normalizer = TextNormalizer(config)
        st = config.get("stress", {})
        self.stress_enabled = bool(st.get("enabled", True))
        self.stress_marker = st.get("marker", "+")
        self.stress_dict = {k.lower(): v for k, v in (st.get("dictionary", {}) or {}).items()}
        self.presets = config.get("presets", {})
        self.gen = config.get("generation", {})
        ch = config.get("chunking", {})
        self.target_chars = int(ch.get("target_chars", 700))
        self.max_chars = int(ch.get("max_chars", 1100))
        p = config.get("pauses", {})
        self.para_ms = int(p.get("paragraph_ms", 520))
        self.split_ms = int(p.get("split_ms", 150))
        self.jitter = float(p.get("jitter", 0.16))
        self.min_ms = int(p.get("min_ms", 0))
        self.max_ms = int(p.get("max_ms", 1200))

    # -- public -----------------------------------------------------------
    def style_for_preset(self, preset_name: str, language: str = "ru") -> Style:
        p = self.presets.get(preset_name) or {}
        g = p.get("generation", {})
        a = p.get("audio", {})
        return Style(
            temperature=float(g.get("temperature", self.gen.get("temperature", 0.85))),
            top_p=float(g.get("top_p", self.gen.get("top_p", 1.0))),
            repetition_penalty=float(g.get("repetition_penalty", self.gen.get("repetition_penalty", 1.05))),
            gain_db=float(a.get("gain_db", 0.0)),
            eq=dict(a.get("eq", {}) or {}),
            compress=bool(a.get("compress", False)),
            language=language,
        ).clamped()

    def prepare(self, text: str, preset_name: str, language: str = "ru",
                seed: int | None = None) -> PreparedText:
        rng = random.Random(seed if seed is not None else None)
        warnings: list[str] = []
        text = text or ""
        preset = self.presets.get(preset_name) or {}
        pr = preset.get("prosody", {})
        pause_scale = float(pr.get("pause_scale", 1.0))
        jitter = float(pr.get("jitter", self.jitter))
        trail_ms = int(pr.get("trail_ms", 0))
        style = self.style_for_preset(preset_name, language)

        if _BRACKET_RE.search(text):
            warnings.append("Команды в квадратных скобках больше не нужны — удалены. "
                            "Интонацию задаёт пресет.")
            text = _BRACKET_RE.sub("", text)

        nt = self.normalizer.normalize(text)
        normalized = self._apply_stress(nt.normalized, warnings)

        segments: list[Segment] = []
        blocks = [b.strip() for b in _PARA_RE.split(normalized) if b.strip()]
        for bi, block in enumerate(blocks):
            if bi > 0:
                segments.append(self._silence(self.para_ms, pause_scale, jitter, rng, "paragraph"))
            # a lone dash line -> a beat
            if _DASH_LINE_RE.match(block):
                if segments and segments[-1].kind == "silence":
                    segments[-1].duration_ms += self.para_ms
                else:
                    segments.append(self._silence(self.para_ms, pause_scale, jitter, rng, "paragraph"))
                continue
            block = re.sub(r"\s*\n\s*", " ", block)
            chunks = self._semantic_chunks(block)
            for ci, chunk in enumerate(chunks):
                if ci > 0:
                    segments.append(self._silence(self.split_ms, pause_scale, jitter, rng, "split"))
                segments.append(Segment("speech", text=chunk, style=style))

        if trail_ms and segments and segments[-1].is_speech:
            segments.append(self._silence(trail_ms, 1.0, jitter, rng, "trail"))

        segments = _trim_edges(segments)
        spoken = " ".join(s.text for s in segments if s.is_speech)
        n_words = len(_WORD_RE.findall(spoken.replace(_ACUTE, "").replace(self.stress_marker, "")))
        return PreparedText(original=nt.original, normalized=normalized, segments=segments,
                            warnings=warnings, replacements=nt.replacements,
                            spoken_text=spoken, n_words=n_words)

    def known_preset_names(self) -> list[str]:
        return sorted(self.presets, key=lambda k: self.presets[k].get("order", 99))

    # -- internals -----------------------------------------------------
    def _silence(self, base_ms: int, scale: float, jitter: float, rng: random.Random,
                 boundary: str) -> Segment:
        v = base_ms * scale
        if jitter > 0:
            v *= 1.0 + rng.uniform(-jitter, jitter)
        v = int(round(max(self.min_ms, min(self.max_ms, v))))
        return Segment("silence", duration_ms=v, boundary=boundary)

    def _semantic_chunks(self, block: str) -> list[str]:
        block = block.strip()
        if len(block) <= self.max_chars:
            return [block]
        # split at sentence ends, then greedily pack to ~target_chars
        cuts = [0] + [m.end() for m in _SENTENCE_RE.finditer(block)] + [len(block)]
        cuts = sorted(set(cuts))
        sentences = [block[cuts[i]:cuts[i + 1]].strip() for i in range(len(cuts) - 1)]
        sentences = [s for s in sentences if s]
        chunks: list[str] = []
        buf = ""
        for s in sentences:
            cand = f"{buf} {s}".strip() if buf else s
            if len(cand) <= self.target_chars or not buf:
                buf = cand
            else:
                chunks.append(buf)
                buf = s
            if len(buf) >= self.max_chars:  # a single monster sentence
                chunks.append(buf)
                buf = ""
        if buf:
            chunks.append(buf)
        return chunks or [block]

    def _apply_stress(self, text: str, warnings: list[str]) -> str:
        if not self.stress_enabled:
            return text.replace(self.stress_marker, "")
        if self.stress_dict:
            def repl(m):
                w = m.group(0)
                rep = self.stress_dict.get(w.lower())
                if rep is None:
                    return w
                return rep.capitalize() if w[:1].isupper() else rep
            text = re.sub(r"[А-Яа-яЁё]+", repl, text)
        out, i, mk = [], 0, self.stress_marker
        while i < len(text):
            c = text[i]
            if c == mk and i + 1 < len(text) and text[i + 1] in _VOWELS:
                out.append(text[i + 1]); out.append(_ACUTE); i += 2; continue
            if c == mk:
                i += 1; continue
            out.append(c); i += 1
        return "".join(out)


def _trim_edges(segs: list[Segment]) -> list[Segment]:
    out: list[Segment] = []
    for s in segs:
        if s.kind == "silence" and out and out[-1].kind == "silence":
            out[-1].duration_ms += s.duration_ms
        else:
            out.append(s)
    while out and out[0].kind == "silence":
        out.pop(0)
    while out and out[-1].kind == "silence":
        out.pop()
    return out
