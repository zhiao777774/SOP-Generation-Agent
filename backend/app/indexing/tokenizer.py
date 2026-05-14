import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


DOMAIN_TOKEN_PATTERN = re.compile(
    r"(?x)"
    r"(?:"
    r"\#[0-9]+\s*[A-Za-z]+\d+[A-Za-z0-9._/-]*"
    r"|[A-Za-z]{1,4}\d{6,}-[A-Za-z0-9-]+"
    r"|[A-Za-z]+[A-Za-z0-9]*(?:[-_./][A-Za-z0-9]+)+"
    r"|[A-Za-z]+[0-9][A-Za-z0-9._/-]*"
    r"|\d+(?:\.\d+)+(?:[A-Za-z\u4e00-\u9fff]+)?"
    r"|\d+(?:rpm|RPM|min|sec|ms|s|Nm|NM|度|mm|cm|kg|g)\b"
    r"|[XYZxyz]\s*軸"
    r"|[A-Z]{2,}(?=\b)"
    r")"
)
BASIC_TOKEN_PATTERN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")

TRADITIONAL_MARKERS = set("無維異點膠鎖檢測資開機軸產處體復線錯誤電壓")
SIMPLIFIED_MARKERS = set("无维异点胶锁检测资开机轴产处体复线错误电压")


@dataclass(frozen=True)
class TokenizerConfig:
    cjk_tokenizer: str = "auto"
    script_normalization: str = "dual"
    ckiptagger_data_dir: Optional[Path] = None
    domain_dict_path: Optional[Path] = None
    ckiptagger_dict_mode: str = "recommend"
    jieba_dict_path: Optional[Path] = None
    domain_token_extraction: bool = True
    temporary_terms: Dict[str, float] = field(default_factory=dict)


@dataclass
class TokenizationRecord:
    text_preview: str
    tokenizer: str
    script: str
    script_ratio: Dict[str, float]
    cjk_tokens: List[str]
    normalized_tokens: List[str]
    permanent_dictionary_hits: List[str]
    temporary_dictionary_hits: List[str]
    preserved_domain_tokens: List[str]
    final_tokens: List[str]


class SparseTokenizer:
    def __init__(self, config: Optional[TokenizerConfig] = None):
        self.config = config or TokenizerConfig()
        self._ws = None
        self._ckip_dictionary = None
        self._jieba = None
        self._opencc_t2s = None
        self._opencc_s2t = None
        self._dictionary_terms = _load_dictionary_terms(self.config.domain_dict_path)
        self._temporary_terms = dict(self.config.temporary_terms or {})

    @property
    def name(self) -> str:
        return self.config.cjk_tokenizer

    @property
    def dictionary_term_count(self) -> int:
        return len(self._dictionary_terms)

    @property
    def temporary_term_count(self) -> int:
        return len(self._temporary_terms)

    def tokenize(self, text: str) -> List[str]:
        return self.tokenize_with_record(text).final_tokens

    def tokenize_with_record(self, text: str) -> TokenizationRecord:
        script, ratio = _detect_script(text)
        tokenizer_name = self._choose_tokenizer(script)
        cjk_tokens = self._tokenize_original(text, tokenizer_name)
        normalized_tokens = self._normalized_tokens(text, tokenizer_name, script)
        permanent_hits = _dictionary_hits(text, self._dictionary_terms)
        temporary_hits = _dictionary_hits(text, self._temporary_terms)
        domain_tokens = _extract_domain_tokens(text) if self.config.domain_token_extraction else []
        final_tokens = _normalize_tokens(
            cjk_tokens + normalized_tokens + permanent_hits + temporary_hits + domain_tokens
        )
        return TokenizationRecord(
            text_preview=_preview(text),
            tokenizer=tokenizer_name,
            script=script,
            script_ratio=ratio,
            cjk_tokens=_normalize_tokens(cjk_tokens),
            normalized_tokens=_normalize_tokens(normalized_tokens),
            permanent_dictionary_hits=_normalize_tokens(permanent_hits),
            temporary_dictionary_hits=_normalize_tokens(temporary_hits),
            preserved_domain_tokens=_normalize_tokens(domain_tokens),
            final_tokens=final_tokens,
        )

    def _choose_tokenizer(self, script: str) -> str:
        requested = self.config.cjk_tokenizer
        if requested != "auto":
            return requested
        if script == "english":
            return "regex"
        if script == "traditional" and self.config.ckiptagger_data_dir:
            return "ckiptagger"
        return "jieba"

    def _tokenize_original(self, text: str, tokenizer_name: str) -> List[str]:
        if tokenizer_name == "ckiptagger":
            return self._ckip_tokens(text)
        if tokenizer_name == "jieba":
            return self._jieba_tokens(text)
        return _basic_tokens(text)

    def _normalized_tokens(self, text: str, tokenizer_name: str, script: str) -> List[str]:
        if self.config.script_normalization == "none" or script == "english":
            return []
        values = []
        for normalized in self._normalized_texts(text):
            if normalized != text:
                values.extend(self._tokenize_original(normalized, tokenizer_name))
        return values

    def _normalized_texts(self, text: str) -> List[str]:
        if self.config.script_normalization == "s2t":
            return [self._convert_s2t(text)]
        if self.config.script_normalization == "t2s":
            return [self._convert_t2s(text)]
        return [self._convert_s2t(text), self._convert_t2s(text)]

    def _convert_s2t(self, text: str) -> str:
        self._ensure_opencc()
        return self._opencc_s2t.convert(text)

    def _convert_t2s(self, text: str) -> str:
        self._ensure_opencc()
        return self._opencc_t2s.convert(text)

    def _ckip_tokens(self, text: str) -> List[str]:
        self._ensure_ckiptagger()
        kwargs = {}
        if self._ckip_dictionary:
            key = (
                "coerce_dictionary"
                if self.config.ckiptagger_dict_mode == "coerce"
                else "recommend_dictionary"
            )
            kwargs[key] = self._ckip_dictionary
        return list(self._ws([text], **kwargs)[0])

    def _jieba_tokens(self, text: str) -> List[str]:
        self._ensure_jieba()
        return list(self._jieba.cut(text))

    def _ensure_ckiptagger(self) -> None:
        if self._ws is not None:
            return
        if not self.config.ckiptagger_data_dir:
            raise RuntimeError("SOP_CKIPTAGGER_DATA_DIR is required for CKIPTagger tokenization.")
        try:
            from ckiptagger import WS, construct_dictionary
        except ImportError as exc:
            raise RuntimeError("ckiptagger is required for CKIPTagger tokenization.") from exc
        self._ws = WS(str(self.config.ckiptagger_data_dir))
        if self._dictionary_terms:
            self._ckip_dictionary = construct_dictionary(self._dictionary_terms)

    def _ensure_jieba(self) -> None:
        if self._jieba is not None:
            return
        try:
            import jieba
        except ImportError as exc:
            raise RuntimeError("jieba is required for jieba tokenization.") from exc
        if self.config.jieba_dict_path and self.config.jieba_dict_path.exists():
            jieba.load_userdict(str(self.config.jieba_dict_path))
        self._jieba = jieba

    def _ensure_opencc(self) -> None:
        if self._opencc_s2t is not None and self._opencc_t2s is not None:
            return
        try:
            from opencc import OpenCC
        except ImportError as exc:
            raise RuntimeError("opencc is required when SOP_SCRIPT_NORMALIZATION is enabled.") from exc
        self._opencc_s2t = OpenCC("s2t")
        self._opencc_t2s = OpenCC("t2s")


def _load_dictionary_terms(path: Optional[Path]) -> Dict[str, float]:
    if not path or not path.exists():
        return {}
    terms: Dict[str, float] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        word, weight = _parse_dictionary_line(line)
        if word:
            terms[word] = weight
    return terms


def _parse_dictionary_line(line: str) -> tuple[str, float]:
    if "," in line:
        parts = [part.strip() for part in line.rsplit(",", 1)]
    else:
        parts = line.rsplit(maxsplit=1)
    if len(parts) == 2:
        try:
            return parts[0].strip(), float(parts[1])
        except ValueError:
            pass
    return line.strip(), 1.0


def _basic_tokens(text: str) -> List[str]:
    return BASIC_TOKEN_PATTERN.findall(text)


def _extract_domain_tokens(text: str) -> List[str]:
    return [match.group(0).strip() for match in DOMAIN_TOKEN_PATTERN.finditer(text)]


def _dictionary_hits(text: str, terms: Dict[str, float]) -> List[str]:
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def _normalize_tokens(tokens: List[str]) -> List[str]:
    normalized = []
    for token in tokens:
        value = " ".join(str(token).strip().lower().split())
        if value:
            normalized.append(value)
    return normalized


def _detect_script(text: str) -> tuple[str, Dict[str, float]]:
    cjk_chars = CJK_PATTERN.findall(text)
    latin_count = sum(1 for char in text if char.isascii() and char.isalpha())
    if not cjk_chars:
        return "english", {"traditional": 0.0, "simplified": 0.0, "latin": 1.0 if latin_count else 0.0}
    traditional = sum(1 for char in cjk_chars if char in TRADITIONAL_MARKERS)
    simplified = sum(1 for char in cjk_chars if char in SIMPLIFIED_MARKERS)
    cjk_count = len(cjk_chars)
    if traditional and simplified:
        script = "mixed"
    elif simplified > traditional:
        script = "simplified"
    elif traditional > simplified:
        script = "traditional"
    else:
        script = "mixed"
    total = max(cjk_count + latin_count, 1)
    return script, {
        "traditional": round(traditional / max(cjk_count, 1), 4),
        "simplified": round(simplified / max(cjk_count, 1), 4),
        "latin": round(latin_count / total, 4),
    }


def _preview(text: str, limit: int = 240) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit] + "..."
