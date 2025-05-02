import re
from collections import namedtuple
from typing import Union

from pydantic import BaseModel, Field

from schemas.vdb_schema import LawVectorResult, VDBResponse

# law_info 또는 admrule_info의 Type. function parameter
RuleInfo = namedtuple("RuleInfo", ["rule_id", "enforce_date", "enact_date", "is_effective"])

# 상하위법령 
HierarchyLaws = namedtuple("HierarchyLaws", ["law_id", "law_num", "law_code", "law_type", "law_name", "parent_id"])

# 관련법령
ConnectedLaws = namedtuple("ConnectedLaws", ["law_id", "law_num", "law_code", "law_type", "law_name"])

class ArticleChapter(BaseModel):
    """법령/행정규칙 조문의 (편)장절(관) 정보
    """

    chapter_num: int = Field(1, description="조문의 장(chapter) 번호")
    chapter_title: str = Field("", description="조문의 장(chapter) 제목")
    section_num: int = Field(0, description="조문의 절(section) 번호")
    section_title: str = Field("", description="조문의 절(section) 제목")

    def extract_text(self, text:str):
        CHAPTERINFO = r"(제(\d+)장)\s*(.*?)(?:<|$)"
        SECTIONINFO = r"(제(\d+)절)\s*(.*?)(?:<|$)"        
        chapter_match = re.search(CHAPTERINFO, text)
        if chapter_match:
            self.chapter_num=int(chapter_match.group(2))
            self.chapter_title=chapter_match.group(1) + " " + chapter_match.group(3).strip()
        
        section_match = re.search(SECTIONINFO, text)
        if section_match:
            self.section_num=int(section_match.group(2))
            self.section_title=section_match.group(1) + " " + section_match.group(3).strip()

        return self

class LawMetadata(BaseModel):
    law_id: str = Field(..., description="법령 ID")
    law_num: str = Field(..., description="법령 번호 (6자리 문자열)")
    announce_num: str = Field(..., description="공포번호")
    announce_date: str = Field(..., description="공포일자 (yyyymmdd 형식)")
    enforce_date: str = Field(..., description="시행일자 (yyyymmdd 또는 '00000000')")
    law_name: str = Field(..., description="법령명(한글)")
    law_short_name: Union[str, None] = Field(None, description="법령명 약칭")
    law_type: str = Field(..., description="법종구분명")
    law_field: str = Field(..., description="법 분야명")
    is_effective: int = Field(
        ..., title="현행여부", description="0: 현행, 1: 예정, -1:과거(연혁)"
    )
    hierarchy_laws: list[HierarchyLaws] = Field(
        [], description="상하위법 (법률, 시행령, 시행규칙, 행정규칙 정보)"
    )
    connected_laws: list[ConnectedLaws] = Field(
        [], description="참조법, 관련 타 법령 정보"
    )
    related_addenda_law: list[str] = Field(
        [], description="부칙 (공포일자 최신순으로 정렬된 부칙 ID 리스트)"
    )
    related_appendices_law: list[str] = Field([], description="별표 ID 목록")
    dept: Union[str, None] = Field(None, description="소관부처 (기관명 + 관련부서명)")
    enact_date: str = Field(..., description="제정일자 (첫 번째 부칙의 공포일자)")
    is_law: bool = Field(
        True, description="법령/행정규칙 구분 : True = 법령, False = 행정규칙"
    )


class LawArticleMetadata(BaseModel):
    article_id: str = Field(..., description="조문 ID (법령ID + 조문번호)")
    article_num: int = Field(..., description="조문번호")
    article_sub_num: int = Field(..., description="조문 가지번호")
    is_preamble: bool = Field(..., description="전문여부 (T: 전문, F: 조문)")
    article_chapter: ArticleChapter = Field(default_factory=ArticleChapter, description="조문의 장, 절 정보")
    article_title: str = Field(..., description="조문 제목")
    enforce_date: str = Field(
        ..., description="시행일자 (yyyymmdd 형식, 시행예정시 '00000000')"
    )
    announce_date: str = Field(
        ..., description="조문 최신 공포(개정)일자 (yyyymmdd 형식)"
    )
    law_id: str = Field(..., description="법령 ID")
    is_effective: int = Field(
        0, title="현행여부", description="0: 현행, 1: 예정, -1:과거(연혁)"
    )
    related_appendices: list[str] = Field([], description="관련 별표 ID 리스트")
    related_addenda: list[str] = Field([], description="관련 부칙 ID 리스트")


class AppendixMetadata(BaseModel):
    appendix_id: str = Field(..., description="(법령 ID)(별표 번호(4자리) 별표 가지번호(2자리)")
    appendix_num : int = Field(..., description="별표 번호")
    appendix_sub_num: int = Field(..., description="별표 가지번호")
    appendix_seq_num: str = Field(
        ..., description="별표 시퀀스 번호 (고유값))"
    )
    appendix_type: str = Field(
        ..., description="별표 구분 (1: 별표, 2: 서식, 3: 별지, 4: 별도, 5: 부록)"
    )
    appendix_title: str = Field(..., description="별표 제목")
    appendix_link: str = Field(..., description="별표파일링크")
    announce_date: Union[str, None] = Field(
        None,
        description="최신 별표 개정일자 (yyyymmdd 형식, 삭제된 별표는 제목에 존재)",
    )
    enforce_date: str = Field(
        ..., description="별표 시행일자 (yyyymmdd 형식, 시행예정일은 '00000000')"
    )
    is_effective: int = Field(
        0, title="현행여부", description="0: 현행, 1: 예정, -1:과거(연혁)"
    )
    law_id: str = Field(..., description="법령 ID")
    related_articles: list[str] = Field([], description="관련 조문 ID 리스트")
    related_addenda : list[str] = Field([], description="관련 부칙 ID 리스트")


class AddendumMetadata(BaseModel):
    addendum_id: str = Field(..., description="부칙 ID (법령ID + 부칙 공포일자)")
    addendum_num: str = Field(..., description="부칙 공포번호")
    addendum_title: str = Field(..., description="부칙 제목")
    announce_date: str = Field(..., description="부칙 공포일자 (yyyymmdd 형식)")
    law_id: str = Field(..., description="법령 ID")
    related_laws: list[str] = Field([], description="관련 법령명, 주로 타법 개정의 법령명")
    related_articles: list[str] = Field([], description="관련 조문 ID")
    related_appendices: list[str] = Field([], description="관련 별표 ID")
    is_exit: bool = Field(False, description="이 부칙이 가장 오래된 조문 개정일 이전에 제정되었는지 여부")  # NOTE preprocessor_genos 변경 사항 고지

# 행정규칙 첨부파일
FileAttached = namedtuple("FileAttached", ["id", "filename", "filelink"])

class AdmRuleMetadata(BaseModel):
    admrule_id: str = Field(..., description="행정규칙 ID")
    admrule_num: str = Field(..., description="행정규칙 번호")
    announce_num: str = Field(..., description="행정규칙 발령번호")
    announce_date: str = Field(..., description="발령일자 (yyyymmdd 형식)")
    enforce_date: str = Field(..., description="시행일자")
    rule_name: str = Field(..., description="행정규칙명")
    rule_type: str = Field(..., description="행정규칙 종류 코드")
    article_form: bool = Field(
        ...,
        description="조문형식여부 (True: 조문번호 있음, False: 조문 번호 없을 가능성 있음)",
    )
    is_effective: int = Field(
        0, title="현행여부", description="0: 현행, 1: 예정, -1:과거(연혁)", le=1, ge=-1
    )
    hierarchy_laws: list[HierarchyLaws] = Field(
        [], description="상하위법 (법률, 시행령, 시행규칙, 행정규칙 정보)"
    )
    connected_laws: list[ConnectedLaws] = Field(
        [], description="참조법, 관련 타 법령 정보"
    )
    related_addenda_admrule: list[str] = Field(
        [], description="부칙 (해당 법령의 부칙 키 리스트, 공포일자 최신순으로 정렬)"
    )
    related_appendices_admrule: list[str] = Field([], description="별표 (관련 별표 ID 리스트)")
    dept: Union[str, None] = Field(None, description="소관부처 (기관명 + 관련 부서명)")
    enact_date: str = Field(..., description="제정일자 (첫 번째 부칙의 공포일자)")
    file_attached: list[FileAttached] = Field(
        [], description="행정규칙 첨부파일 (ID, 파일명, 파일링크)"
    )
    is_law: bool = Field(
        False, description="법령/행정규칙 구분 : True = 법령, False = 행정규칙"
    )


class AdmRuleArticleMetadata(BaseModel):
    article_id: str = Field(..., description="조문 ID (행정규칙ID + 조문번호)")
    article_num: int = Field(
        ..., description="조문번호"
    )
    article_sub_num: int = Field(..., description="조문 가지번호")
    admrule_id: str = Field(..., description="행정규칙 ID")
    article_chapter: ArticleChapter = Field(default_factory=ArticleChapter, description="조문의 장, 절 정보")
    article_title: str = Field(..., description="조문 제목")
    is_effective: int = Field(
        ..., title="현행여부", description="0: 현행, 1: 예정, -1:과거(연혁)", le=1, ge=-1
    )
    enforce_date: str = Field(
        ..., description="시행일자 (yyyymmdd 형식, 시행예정시 '00000000')"
    )
    announce_date: str = Field(
        ..., description="조문 최신 공포(개정)일자 (yyyymmdd 형식)"
    )
    is_preamble: bool = Field(..., description="전문여부 (T: 전문, F: 조문)")
    related_appendices: list[str] = Field([], description="관련 별표 ID 리스트")
    related_addenda: list[str] = Field([], description="관련 부칙 ID 리스트")


class ParserContent(BaseModel):
    metadata: Union[
        LawMetadata,
        LawArticleMetadata,
        AddendumMetadata,
        AdmRuleMetadata,
        AdmRuleArticleMetadata,
        AppendixMetadata,
    ] = Field(..., description="메타데이터")
    content: list[Union[dict, str]] = Field(
        ..., description="본문 텍스트 (law, appendix의 경우 빈 리스트일 수 있음)"
    )

class ParserRequest(BaseModel):
    law_ids: list[str] = []
    admrule_ids: list[str] = []

class ParserResult(BaseModel):
    """최종 출력 형태"""
    law: ParserContent = Field(..., description="법령 또는 행정규칙")
    article: list[ParserContent] = Field(..., description="조문" , default_factory=list)
    addendum: list[ParserContent] = Field(..., description="부칙", default_factory=list)
    appendix: list[ParserContent] = Field(..., description="별표", default_factory=list)

class ParserResponse(BaseModel):
    total_count: int = 0
    seen_count: int = 0
    unseen_count: int = 0
    success_count: int = 0
    fail_count: int = 0 
    seen_ids: dict[str, set[str]] = {}        # {"law": [...], "admrule": [...]}
    unseen_ids: dict[str, set[str]] = {}     # {"law": [...], "admrule": [...]}
    fail_ids: set[str] = set()     

    def model_post_init(self, __context):
        self.unseen_count = self.total_count

    def increment_total(self):
        self.total_count += 1
        
    def increment_success(self):
        self.success_count += 1

    def increment_fail(self, id:str):
        self.fail_count += 1
        self.fail_ids.add(id)


class PipelineResponse(BaseModel):
    parser: ParserResponse
    vdb: VDBResponse = VDBResponse()
    mappings: list[LawVectorResult] = []