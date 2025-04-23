from pydantic import BaseModel, Field, PrivateAttr
from typing import Pattern, Union, Iterator

import re

class InvalidRegexError(ValueError):
    pass


class RegexProcessor(BaseModel):
    patterns: dict[str, str] = Field(default_factory=dict)
    _compiled_patterns: dict[str, Union[Pattern, None]] = PrivateAttr(default_factory=dict[str, None])

    class Config:
        arbitrary_types_allowed = True

    def model_post_init(self, context):
        self.set_patterns()
        return super().model_post_init(context)

    def set_patterns(self):
        regex_patterns = {
            "CHAPTER_INFO": r"(제(\d+)장)\s*(.*?)(?:<|$)",
            "SECTION_INFO": r"(제(\d+)절)\s*(.*?)(?:<|$)",
            "IS_PREAMBLE": r"(제(\d+)장)\s*(.*?)(?:<|$)|(제(\d+)절)\s*(.*?)(?:<|$)",
            "AMEND_DATE": r"<개정 ([^<>]*)>",
            "BLANKET": r"\(([^)]+)\)",
            "BLANKET_DATE": r"\(.*?개정.*?\)",
            "DATE_KOR": r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
            "CHEVRON_DATE": r"<(\d{4})\.(\d{1,2})\.(\d{1,2})>",
            "DATE": r"(\d{4})[.\u00B7]\s*(\d{1,2})[.\u00B7]\s*(\d{1,2})",
            "ARTICLE_NUM": r"제(\d{1,4})조(?:의(\d+))?(?=\s|\(|$)",
            "APPENDIX_REF": r"별표\s*(\d+)(?:\-(\d+))?",
            "APPENDIX_FORM_REF": r"별지\s*제\s*(\d+)호\s*서식(?:\-(\d+))?",
            "ADDENDUM_NUM": r"<?제(\d+(?:-\d+)?)(?:호)?",
            "ADDENDUM_TITLE": r"^\s*부칙(?:\s*\(.*?\))?\s*<[^>]+>",
            "ADDENDUM_ARTICLE": r"^\s*(제\d+조(?:\s*\([^)]*\))?)",

            # "ADR_ADDENDUM_ARTICLE": r"\s*(제\d+조\s*\(.*?\))",  # 행정규칙 부칙 조문 패턴
            # "LAW_ADDENDUM_ARTICLE": r"^\s*(제\d+조(?:\s*\([^)]*\))?)",  # 법령 부칙 조문 패턴
            # "LAW_ADDENDUM_TITLE": r"^\s*부칙\s*\(.*?\)\s*<[^>]+>",    # 법령 부칙 제목 패턴
            # "ADDENDUM_TITLE": r"부칙.*?>",

        }
        for key, value in regex_patterns.items():
            self.add_pattern(name=key, pattern=value)

    def get_pattern(self, name:str) -> Union[str, None]:
        return self.patterns[name] if self.__contains__(name) else None
    
    def get_compiled_pattern(self, name:str) -> Union[str, None]:
        return self._compiled_patterns[name] if name in self._compiled_patterns else None

    def add_pattern(self, name:str, pattern: str):
        if not isinstance(pattern, str):
            raise TypeError(message=' pattern must be string type')
        self.patterns[name] = pattern
        _ = self.compile(name)
    
    def remove_pattern(self, name:str) -> bool:
        if self.__contains__(name):
            del self.patterns[name]
            if name in self._compiled_patterns:
                del self._compiled_patterns[name]
            return True
        return False
    
    def __list_patterns__(self) -> list[str]:
        return list(self.patterns.keys())
    
    def __contains__(self, name:str) -> bool:
        return name in self.patterns

    
    def compile(self, name:str, flags:int = 0) -> Pattern:
        if name not in self._compiled_patterns and self.__contains__(name):
            try:
                _compiled_pattern = re.compile(self.patterns[name], flags)
            except re.error as e:
                del self.patterns[name]
                raise InvalidRegexError(f"유효하지 않은 정규표현식 패턴: {str(e)}")
            else:
                self._compiled_patterns[name] = _compiled_pattern
        return self._compiled_patterns[name]
        
    
    def _get_pattern_and_apply(
        self,
        name: str,
        *args,
        method: str,
        flags: int = 0
        ):
        if self.__contains__(name):
            if self._compiled_patterns[name] is None:
                _ = self.compile(name, flags)
            func = getattr(self._compiled_patterns[name], method)
            return func(*args)
        raise ValueError(f"Pattern '{name}' not found.")

    def match(self, name: str, text: str, flags: int = 0) -> Union[re.Match, None]:
        return self._get_pattern_and_apply(name, text, method="match", flags=flags)

    def search(self, name: str, text: str, flags:int = 0) -> Union[re.Match]:
        return self._get_pattern_and_apply(name, text, method="search", flags=flags)

        
    def findall(self, name: str, text: str, flags:int = 0) -> list:
        return self._get_pattern_and_apply(name, text, method="findall", flags=flags)

    def finditer(self, name: str, text: str, flags: int = 0) -> Iterator[re.Match]:
         return self._get_pattern_and_apply(name, text, method="finditer", flags=flags)
    
    def sub(self, name:str, repl:str, text:str, flags: int = 0) -> str:
        return self._get_pattern_and_apply(name, repl, text, method="sub", flags=flags)
    
    def subn(self, name: str, repl: str, text: str, flags: int = 0) -> tuple[str, int]:
        return self._get_pattern_and_apply(name, repl, text, method="subn", flags=flags)

    def split(self, name: str, text: str, flags: int = 0) -> list[str]:
        return self._get_pattern_and_apply(name, text, method="split", flags=flags)




regex_processor = RegexProcessor()
