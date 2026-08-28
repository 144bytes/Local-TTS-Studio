"""Offline text normalizer: user text -> TTS-friendly Russian.

The original text is never destroyed. `normalize()` returns both, plus a list of
what was changed (for the debug report / UI hints).

Layers, in order:
  1. protect fully-English sentences (don't russify them)
  2. pronunciation lexicon  (C++, JavaScript, PHP, ...)  — boundary aware
  3. acronyms               (API, CPU, HTTP, ...)         — spell-out policy
  4. numbers / dates / time / percent / ranges / units   — rule-based
  5. light punctuation tidy (final period, ... -> …)

Everything is configurable via config["normalizer"].
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# defaults (merged with config["normalizer"])
# ---------------------------------------------------------------------------
DEFAULT_LEXICON = {
    "c++": "си плюс плюс", "c#": "си шарп", "c/c++": "си, си плюс плюс",
    ".net": "дот нет", "node.js": "нод джей эс", "node js": "нод джей эс",
    "nodejs": "нод джей эс", "next.js": "некст джей эс", "nuxt.js": "накст джей эс",
    "javascript": "джаваскрипт", "typescript": "тайпскрипт", "coffeescript": "кофискрипт",
    "python": "пайтон", "java": "джава", "kotlin": "котлин", "golang": "гоу лэнг",
    "php": "пи эйч пи", "ruby": "руби", "rust": "раст", "scala": "скала",
    "swift": "свифт", "perl": "перл", "haskell": "хаскелл", "elixir": "эликсир",
    "react": "реакт", "reactjs": "реакт", "vue": "вью", "vue.js": "вью",
    "angular": "энгулар", "svelte": "свелт", "jquery": "джей квери",
    "django": "джанго", "flask": "фласк", "fastapi": "фаст эй пи ай",
    "laravel": "ларавел", "symfony": "симфони", "spring": "спринг",
    "express": "экспресс", "nestjs": "нест джей эс",
    "cgi": "си джи ай", "wsgi": "вэ эс джи ай", "asgi": "эй эс джи ай",
    "html": "эйч ти эм эл", "html5": "эйч ти эм эл пять", "css": "си эс эс",
    "css3": "си эс эс три", "scss": "эс си эс эс", "sass": "сасс",
    "xml": "икс эм эл", "yaml": "ямл", "json": "джейсон", "toml": "томл",
    "sql": "эс кью эл", "nosql": "ноу эс кью эл", "postgresql": "постгрес",
    "postgres": "постгрес", "mysql": "май эс кью эл", "sqlite": "эс кью лайт",
    "mongodb": "монго ди би", "redis": "редис", "graphql": "граф кью эл",
    "api": "эй пи ай", "rest": "рест", "restful": "рестфул", "grpc": "джи эр пи си",
    "sdk": "эс ди кей", "cli": "си эл ай", "gui": "гуи", "ide": "ай ди и",
    "url": "ю эр эл", "uri": "ю эр ай", "http": "эйч ти ти пи", "https": "эйч ти ти пи эс",
    "ftp": "эф ти пи", "ssh": "эс эс эйч", "tcp": "ти си пи", "udp": "ю ди пи",
    "ip": "ай пи", "dns": "ди эн эс", "ssl": "эс эс эл", "tls": "ти эл эс",
    "github": "гитхаб", "gitlab": "гитлаб", "bitbucket": "битбакет", "git": "гит",
    "linux": "линукс", "unix": "юникс", "ubuntu": "убунту", "debian": "дебиан",
    "windows": "виндоус", "macos": "мак ос", "android": "андроид", "ios": "ай ос",
    "docker": "докер", "kubernetes": "кубернетис", "k8s": "кубернетис",
    "nginx": "энджин экс", "apache": "апач", "webpack": "вебпак", "vite": "вайт",
    "cmake": "си мейк", "gcc": "джи си си", "clang": "кланг", "llvm": "эл эл ви эм",
    "npm": "эн пи эм", "pip": "пип", "yarn": "ярн", "pnpm": "пи эн пи эм",
    "aws": "эй дабл ю эс", "gcp": "джи си пи", "azure": "жур", "cdn": "си ди эн",
    "vscode": "вэ эс код", "vs code": "вэ эс код", "jetbrains": "джетбрейнс",
    "chatgpt": "чат джи пи ти", "gpt": "джи пи ти", "llm": "эл эл эм",
    "ai": "эй ай", "ml": "эм эл", "gpu": "джи пи ю", "cpu": "си пи ю",
    "ram": "рам", "ssd": "эс эс ди", "hdd": "эйч ди ди", "usb": "ю эс би",
    "os": "оу эс", "ui": "ю ай", "ux": "ю экс", "qa": "кью эй",
    "js": "джей эс", "ts": "ти эс", "regex": "регэксп", "utf": "ю ти эф",
}

# acronyms that should be spelled by Russian letter names when NOT in the lexicon
_RU_LETTER = {
    "а": "а", "б": "бэ", "в": "вэ", "г": "гэ", "д": "дэ", "е": "е", "ж": "жэ",
    "з": "зэ", "и": "и", "к": "ка", "л": "эль", "м": "эм", "н": "эн", "о": "о",
    "п": "пэ", "р": "эр", "с": "эс", "т": "тэ", "у": "у", "ф": "эф", "х": "ха",
    "ц": "цэ", "ч": "че", "ш": "ша", "щ": "ща", "э": "э", "ю": "ю", "я": "я",
}
_EN_LETTER = {
    "a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи",
    "h": "эйч", "i": "ай", "j": "джей", "k": "кей", "l": "эл", "m": "эм", "n": "эн",
    "o": "оу", "p": "пи", "q": "кью", "r": "ар", "s": "эс", "t": "ти", "u": "ю",
    "v": "ви", "w": "дабл ю", "x": "экс", "y": "уай", "z": "зед",
}

_ONES = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь",
         "девять", "десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать",
         "пятнадцать", "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
_ONES_F = {1: "одна", 2: "две"}
_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят",
         "семьдесят", "восемьдесят", "девяносто"]
_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот",
             "семьсот", "восемьсот", "девятьсот"]
_MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
           "августа", "сентября", "октября", "ноября", "декабря"]
_ORD = {  # ordinal for dates/times, feminine/neuter as needed — kept simple, masculine day form
    1: "первое", 2: "второе", 3: "третье", 4: "четвёртое", 5: "пятое", 6: "шестое",
    7: "седьмое", 8: "восьмое", 9: "девятое", 10: "десятое", 11: "одиннадцатое",
    12: "двенадцатое", 13: "тринадцатое", 14: "четырнадцатое", 15: "пятнадцатое",
    16: "шестнадцатое", 17: "семнадцатое", 18: "восемнадцатое", 19: "девятнадцатое",
    20: "двадцатое", 30: "тридцатое", 31: "тридцать первое",
}
for _t in (20, 30):
    for _o in range(1, 10):
        _ORD[_t + _o] = _TENS[_t // 10] + " " + _ORD[_o]

# genitive of small cardinals (for ranges: "от трёх до пяти")
_GEN = {1: "одного", 2: "двух", 3: "трёх", 4: "четырёх", 5: "пяти", 6: "шести",
        7: "семи", 8: "восьми", 9: "девяти", 10: "десяти", 11: "одиннадцати",
        12: "двенадцати", 13: "тринадцати", 14: "четырнадцати", 15: "пятнадцати",
        16: "шестнадцати", 17: "семнадцати", 18: "восемнадцати", 19: "девятнадцати",
        20: "двадцати", 30: "тридцати", 40: "сорока", 50: "пятидесяти",
        100: "ста", 1000: "тысячи"}
# genitive ordinals for year endings ("...двадцать шестого")
_ORD_GEN = {1: "первого", 2: "второго", 3: "третьего", 4: "четвёртого", 5: "пятого",
            6: "шестого", 7: "седьмого", 8: "восьмого", 9: "девятого", 10: "десятого",
            11: "одиннадцатого", 12: "двенадцатого", 13: "тринадцатого", 14: "четырнадцатого",
            15: "пятнадцатого", 16: "шестнадцатого", 17: "семнадцатого", 18: "восемнадцатого",
            19: "девятнадцатого", 20: "двадцатого", 30: "тридцатого"}
_TENS_ORD_GEN = {2: "двадцатого", 3: "тридцатого", 4: "сорокового", 5: "пятидесятого",
                 6: "шестидесятого", 7: "семидесятого", 8: "восьмидесятого", 9: "девяностого"}
_HUND_ORD_GEN = {1: "сотого", 2: "двухсотого", 3: "трёхсотого", 4: "четырёхсотого",
                 5: "пятисотого", 6: "шестисотого", 7: "семисотого", 8: "восьмисотого",
                 9: "девятисотого"}


def _gen_num(n: int) -> str:
    if n in _GEN:
        return _GEN[n]
    if n < 100 and n % 10 and n // 10 * 10 in _GEN:
        return _GEN[n // 10 * 10] + " " + _GEN[n % 10]
    return _int_to_words(n)


@dataclass
class NormalizedText:
    original: str
    normalized: str
    replacements: list[dict] = field(default_factory=list)

    def note(self, kind: str, src: str, dst: str):
        self.replacements.append({"kind": kind, "from": src, "to": dst})


# ---------------------------------------------------------------------------
def _int_to_words(n: int, gender: str = "m") -> str:
    if n == 0:
        return "ноль"
    if n < 0:
        return "минус " + _int_to_words(-n, gender)
    out: list[str] = []

    def under_1000(x: int, g: str) -> list[str]:
        p: list[str] = []
        if x >= 100:
            p.append(_HUNDREDS[x // 100]); x %= 100
        if x >= 20:
            p.append(_TENS[x // 10]); x %= 10
        if x:
            if x <= 2 and g in ("f",):
                p.append(_ONES_F[x])
            else:
                p.append(_ONES[x])
        return p

    millions = n // 1_000_000
    thousands = (n % 1_000_000) // 1000
    rest = n % 1000
    if millions:
        out += under_1000(millions, "m")
        out.append(_plural(millions, "миллион", "миллиона", "миллионов"))
    if thousands:
        out += under_1000(thousands, "f")
        out.append(_plural(thousands, "тысяча", "тысячи", "тысяч"))
    if rest or not out:
        out += under_1000(rest, gender)
    return " ".join(out)


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def _decimal_to_words(whole: str, frac: str) -> str:
    w = int(whole)
    f = int(frac)
    unit = _plural(w, "целая", "целых", "целых")
    denom = {1: "десятая", 2: "сотая", 3: "тысячная"}.get(len(frac), "")
    denom_pl = _plural(f, denom, denom.replace("ая", "ых"), denom.replace("ая", "ых"))
    return f"{_int_to_words(w, 'f')} {unit} {_int_to_words(f, 'f')} {denom_pl}".strip()


# ---------------------------------------------------------------------------
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#./-]*")
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_INT = re.compile(r"(?<![\d.,])-?\d{1,12}(?![\d.,])")
_DEC = re.compile(r"(?<!\d)(\d{1,9})[.,](\d{1,6})(?!\d)")
_PCT = re.compile(r"(\d[\d.,]*)\s*%")
_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})(?::\d{2})?\b")
_YEAR_CTX = re.compile(r"\b(\d{4})\s*(?=(?:год|г\.|годах))", re.IGNORECASE)
_RANGE = re.compile(r"(?<!\d)(\d{1,6})\s*[-–—]\s*(\d{1,6})(?!\d)")


class TextNormalizer:
    def __init__(self, config: dict):
        n = (config or {}).get("normalizer", {})
        self.enabled = n.get("enabled", True)
        self.lexicon = {**DEFAULT_LEXICON, **{k.lower(): v for k, v in (n.get("lexicon", {}) or {}).items()}}
        self.acronyms = {k.lower(): v for k, v in (n.get("acronyms", {}) or {}).items()}  # "spell" | "keep" | explicit
        self.spell_default = n.get("spell_unknown_acronyms", True)
        self.numbers = n.get("numbers", True)
        self.dates = n.get("dates", True)
        # longest keys first so "node.js" wins over "node"
        self._lex_keys = sorted(self.lexicon, key=len, reverse=True)

    # -- public ------------------------------------------------------------
    def normalize(self, text: str) -> NormalizedText:
        nt = NormalizedText(original=text or "", normalized=text or "")
        if not self.enabled or not nt.normalized.strip():
            return nt

        out_sents = []
        for sent in _split_keep(nt.normalized):
            if _is_english_sentence(sent):
                out_sents.append(sent)   # leave English prose alone
                continue
            out_sents.append(self._normalize_ru_sentence(sent, nt))
        nt.normalized = "".join(out_sents)
        nt.normalized = _tidy_punct(nt.normalized)
        return nt

    # -- internals -------------------------------------------------------
    def _normalize_ru_sentence(self, s: str, nt: NormalizedText) -> str:
        if self.dates:
            s = _DATE.sub(lambda m: self._date(m, nt), s)
            s = _TIME.sub(lambda m: self._time(m, nt), s)
            s = _YEAR_CTX.sub(lambda m: self._year_ctx(m, nt), s)
        s = self._lexicon_pass(s, nt)
        if self.numbers:
            s = _RANGE.sub(lambda m: self._range(m, nt), s)
            s = _PCT.sub(lambda m: self._pct(m, nt), s)
            s = _DEC.sub(lambda m: self._dec(m, nt), s)
            s = _INT.sub(lambda m: self._int(m, nt), s)
        s = self._acronym_pass(s, nt)
        return s

    def _lexicon_pass(self, s: str, nt: NormalizedText) -> str:
        for key in self._lex_keys:
            # boundary: not preceded/followed by a word char (cyr/lat/digit); hyphen ok after
            pat = re.compile(r"(?<![\wёЁ])" + re.escape(key) + r"(?![\wёЁ]|\+|#)", re.IGNORECASE)
            def repl(m, k=key):
                nt.note("lexicon", m.group(0), self.lexicon[k])
                return self.lexicon[k]
            s = pat.sub(repl, s)
        return s

    def _acronym_pass(self, s: str, nt: NormalizedText) -> str:
        def repl(m):
            w = m.group(0)
            lw = w.lower().strip(".")
            if lw in self.lexicon:
                return w  # already handled
            pol = self.acronyms.get(lw)
            if pol and pol not in ("spell", "keep"):
                nt.note("acronym", w, pol); return pol
            if pol == "keep":
                return w
            is_acro = w.isupper() and 2 <= len(w.strip(".")) <= 6 and w.strip(".").isalpha()
            if is_acro and (pol == "spell" or self.spell_default):
                spelled = _spell(w.strip("."))
                nt.note("acronym", w, spelled)
                return spelled
            return w
        return _LATIN_WORD.sub(repl, s)

    def _int(self, m, nt):
        v = int(m.group(0))
        words = _int_to_words(v)
        nt.note("number", m.group(0), words)
        return words

    def _dec(self, m, nt):
        words = _decimal_to_words(m.group(1), m.group(2))
        nt.note("number", m.group(0), words)
        return words

    def _pct(self, m, nt):
        num = m.group(1).replace(",", ".")
        base = _decimal_to_words(*num.split(".")) if "." in num else _int_to_words(int(num))
        pv = int(float(num))
        words = f"{base} {_plural(pv, 'процент', 'процента', 'процентов')}"
        nt.note("percent", m.group(0), words)
        return words

    def _range(self, m, nt):
        a, b = int(m.group(1)), int(m.group(2))
        words = f"от {_gen_num(a)} до {_gen_num(b)}"
        nt.note("range", m.group(0), words)
        return words

    def _date(self, m, nt):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        day = _ORD.get(d, _int_to_words(d))
        month = _MONTHS[mo] if 1 <= mo <= 12 else _int_to_words(mo)
        year = _year_words(y)
        words = f"{day} {month} {year} года"
        nt.note("date", m.group(0), words)
        return words

    def _year_ctx(self, m, nt):
        y = int(m.group(1))
        words = _year_words(y)
        nt.note("year", m.group(0).strip(), words)
        return words + " "

    def _time(self, m, nt):
        h, mi = int(m.group(1)), int(m.group(2))
        hw = f"{_int_to_words(h, 'm')} {_plural(h, 'час', 'часа', 'часов')}"
        if mi == 0:
            words = hw
        else:
            words = f"{hw} {_int_to_words(mi, 'f')} {_plural(mi, 'минута', 'минуты', 'минут')}"
        nt.note("time", m.group(0), words)
        return words


def _year_words(y: int) -> str:
    # genitive ordinal: "две тысячи двадцать шестого"
    if y % 1000 == 0:  # 2000, 3000 ...
        return {2000: "двухтысячного", 1000: "тысячного", 3000: "трёхтысячного"}.get(
            y, _int_to_words(y) + "ного")
    th = y // 1000
    rest = y % 1000
    parts = [_int_to_words(th, "f"), "тысячи" if th != 1 else "тысяча"] if th else []
    hund = rest // 100
    tail = rest % 100
    ten, one = tail // 10, tail % 10
    if hund and tail == 0:
        parts.append(_HUND_ORD_GEN.get(hund, _HUNDREDS[hund]))
        return " ".join(parts)
    if hund:
        parts.append(_HUNDREDS[hund])
    if tail == 0 and not hund:
        parts.append("го")  # unreachable-ish; year always has a tail here
    elif ten >= 2 and one == 0:
        parts.append(_TENS_ORD_GEN.get(ten, _TENS[ten]))
    elif ten >= 2:
        parts.append(_TENS[ten])
        parts.append(_ORD_GEN.get(one, _ONES[one] + "ого"))
    else:  # tail 1..19
        parts.append(_ORD_GEN.get(tail, _ONES[tail] + "ого"))
    return " ".join(parts)


def _spell(word: str) -> str:
    out = []
    for ch in word:
        lo = ch.lower()
        if lo in _EN_LETTER:
            out.append(_EN_LETTER[lo])
        elif lo in _RU_LETTER:
            out.append(_RU_LETTER[lo])
        elif ch.isdigit():
            out.append(_ONES[int(ch)])
        else:
            out.append(ch)
    return " ".join(out)


def _split_keep(text: str) -> list[str]:
    parts = re.split(r"(\s+)", text)
    # regroup into sentences keeping whitespace
    sents, buf = [], ""
    for p in parts:
        buf += p
        if re.search(r"[.!?…][\"'”»)\]]*\s*$", buf):
            sents.append(buf); buf = ""
    if buf:
        sents.append(buf)
    return sents or [text]


_EN_FUNC = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "and", "or",
            "but", "with", "without", "this", "that", "these", "those", "of", "to",
            "in", "on", "for", "from", "it", "its", "you", "your", "we", "our",
            "can", "will", "would", "should", "have", "has", "not", "as", "at", "by"}


def _is_english_sentence(s: str) -> bool:
    words = re.findall(r"[A-Za-zА-Яа-яЁё]+", s)
    if len(words) < 4:
        return False
    lower = [w.lower() for w in words]
    cyr = sum(1 for w in words if re.search(r"[А-Яа-яЁё]", w))
    func = sum(1 for w in lower if w in _EN_FUNC)
    # english prose: barely any cyrillic AND at least 2 english function words
    return cyr <= 1 and func >= 2


def _tidy_punct(s: str) -> str:
    s = s.replace("...", "…").replace(" ,", ",").replace(" .", ".")
    s = re.sub(r"[ \t]{2,}", " ", s)
    st = s.strip()
    if st and not re.search(r"[.!?…:—]$", st):
        s = s.rstrip() + "."
    return s
