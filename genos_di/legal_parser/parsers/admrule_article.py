
from commons.regex_handler import RegexProcessor
from commons.type_converter import TypeConverter
from commons.utils import format_date, replace_strip
from parsers.extractor import (
    extract_article_num,
    extract_date_to_yyyymmdd,
    extract_related_appendices,
    get_latest_date,
)
from schemas.schema import (
    AdmRuleArticleMetadata,
    ArticleChapter,
    ParserContent,
    RuleInfo,
)

type_converter = TypeConverter()
regex_processor = RegexProcessor()

def parse_admrule_article_info(admrule_info: RuleInfo, article_list: list[str]) -> list[ParserContent]:
    """행정규칙 조문 정보를 파싱하여 ParserContent 객체 리스트로 반환하는 함수"""
    if not article_list:
        return []
    
    admrule_articles = []
    article_list = type_converter.converter(article_list, list[str], use_default=True)
    
    # 기본 정보 추출
    admrule_id = admrule_info.rule_id
    enforce_date = admrule_info.enforce_date
    is_effective = admrule_info.is_effective
    enact_date = admrule_info.enact_date
    
    article_chapter = ArticleChapter()
    current_chapter = None
    
    for article in article_list:
        parsed_article = process_admrule_article(
            article, 
            admrule_id, 
            enforce_date, 
            is_effective, 
            enact_date, 
            article_chapter, 
            current_chapter
        )
        
        if parsed_article:
            admrule_articles.append(parsed_article)
            # 전문인 경우 current_chapter 업데이트
            if parsed_article.metadata.is_preamble:
                current_chapter = parsed_article.metadata.article_chapter
    
    return admrule_articles

def process_admrule_article(
    article: str, 
    admrule_id: str, 
    enforce_date: str, 
    is_effective: int, 
    enact_date: str, 
    article_chapter: ArticleChapter, 
    current_chapter: ArticleChapter = None
) -> ParserContent:
    """행정규칙 조문을 처리하여 ParserContent 객체를 생성하는 함수"""
    # 조문 구조 추출
    article_chapter.extract_text(article)
    
    # 전문 여부 확인
    is_preamble = bool(regex_processor.search("IS_PREAMBLE", article))
    
    if is_preamble:
        # 전문인 경우 처리
        article_num, article_sub_num, title, updated_chapter = process_preamble(article_chapter)
        article_id = f"{admrule_id}{article_num:04d}{article_sub_num:03d}"
        current_chapter = updated_chapter
    else:
        # 일반 조문인 경우 처리
        article_id, article_num, article_sub_num = extract_article_num(admrule_id, article)
        title = extract_article_title(article)
    
    # 개정일자 추출
    announce_date = extract_article_announce_date(article, enact_date)
    
    # 삭제된 조문 처리
    if "삭제" in article:
        announce_date = extract_deleted_article_date(article, announce_date)
        enforce_date = announce_date
        title = "삭제"
    
    # 조문 내용 처리
    content = replace_strip(article.split("\n"))
    
    # 관련 별표 추출
    related_appendices = extract_related_appendices(admrule_id, content)
    
    # 메타데이터 생성
    metadata = create_admrule_article_metadata(
        article_id, 
        article_num, 
        article_sub_num, 
        title, 
        current_chapter or article_chapter, 
        enforce_date, 
        announce_date, 
        admrule_id, 
        is_effective, 
        is_preamble, 
        related_appendices
    )
    
    return ParserContent(metadata=metadata, content=content)

def process_preamble(article_chapter: ArticleChapter) -> tuple[int, int, str, ArticleChapter]:
    """전문을 처리하는 함수"""
    article_num = article_chapter.chapter_num
    article_sub_num = 0
    title = article_chapter.chapter_title
    
    updated_chapter = ArticleChapter(
        chapter_num=article_chapter.chapter_num,
        chapter_title=article_chapter.chapter_title,
        section_num=article_chapter.section_num,
        section_title=article_chapter.section_title,
    )
    
    return article_num, article_sub_num, title, updated_chapter

def extract_article_title(article: str) -> str:
    """조문 제목을 추출하는 함수"""
    title_match = regex_processor.search("BLANKET", article)
    return title_match.group(1) if title_match else ""

def extract_article_announce_date(article: str, enact_date: str) -> str:
    """조문 개정일자를 추출하는 함수"""
    matches = regex_processor.findall("BLANKET_DATE", article)
    date_matches = []
    
    for match in matches:
        date_matches.extend(extract_date_to_yyyymmdd(match))
    
    return get_latest_date(date_matches, enact_date)

def extract_deleted_article_date(article: str, default_date: str) -> str:
    """삭제된 조문의 날짜를 추출하는 함수"""
    announce_date_match = regex_processor.search("CHEVRON_DATE", article)
    
    if announce_date_match:
        year, month, day = announce_date_match.groups()
        return format_date(year, month, day)
    
    return default_date

def create_admrule_article_metadata(
    article_id: str, 
    article_num: int, 
    article_sub_num: int, 
    title: str, 
    article_chapter: ArticleChapter, 
    enforce_date: str, 
    announce_date: str, 
    admrule_id: str, 
    is_effective: int, 
    is_preamble: bool, 
    related_appendices: list
) -> AdmRuleArticleMetadata:
    """행정규칙 조문 메타데이터를 생성하는 함수"""
    return AdmRuleArticleMetadata(
        article_id=article_id,
        article_num=article_num,
        article_sub_num=article_sub_num,
        article_title=title,
        article_chapter=article_chapter,
        enforce_date=enforce_date,
        announce_date=announce_date,
        admrule_id=admrule_id,
        is_effective=is_effective,
        is_preamble=is_preamble,
        related_addenda=[],
        related_appendices=related_appendices,
    )
