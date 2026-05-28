from pathlib import Path
from typing import Union
from transformers import AutoTokenizer, PreTrainedTokenizerBase

MINILM_PATH = Path("/models/doc_parser_models/sentence-transformers-all-MiniLM-L6-v2")
MINILM_HF = "sentence-transformers/all-MiniLM-L6-v2"

DEFAULT_TOKENIZER: Union[str, Path] = MINILM_PATH if MINILM_PATH.exists() else MINILM_HF


class CharTokenizer:
    """글자 수를 토큰 수로 사용하는 더미 토크나이저."""

    def tokenize(self, text: str) -> list:
        return list(text)

    def __call__(self, text: str) -> int:
        return len(text)

    @property
    def model_max_length(self) -> int:
        return int(1e30)


def resolve_tokenizer(
    tokenizer: Union[PreTrainedTokenizerBase, str, Path, None],
) -> Union[PreTrainedTokenizerBase, CharTokenizer]:
    """'char' 문자열이면 CharTokenizer, 아니면 AutoTokenizer로 로드."""
    if tokenizer is None or tokenizer == "miniLM":
        return AutoTokenizer.from_pretrained(DEFAULT_TOKENIZER)
    if tokenizer == "char":
        return CharTokenizer()
    if isinstance(tokenizer, PreTrainedTokenizerBase):
        return tokenizer
    return AutoTokenizer.from_pretrained(tokenizer)
