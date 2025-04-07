import re

from constants import BLANCKET, BLANCKETDATE, CHAPTERINFO, CHEVRONDATE

from schemas import (
    AdmRuleArticleMetadata,
    AdmRuleMetadata,
    ArticleChapter,
    FileAttached,
    ParserContent,
    RuleInfo,
)
from utils import (
    extract_addenda_id,
    extract_appendix_id,
    extract_article_num,
    extract_date_to_yyyymmdd,
    get_latest_date,
    replace_strip,
)


# 행정규칙 조회 -> 행정규칙
def parse_admrule_info(admrule_id: str, admrule_data: dict, hierarchy_laws, connected_laws) -> ParserContent:
    
    # 기본정보
    basic_info = admrule_data["행정규칙기본정보"]

    admrule_num = basic_info.get("행정규칙ID", "")
    announce_num = basic_info.get("발령번호", "")
    announce_date = basic_info.get("발령일자", "")
    enforce_date = basic_info.get("시행일자", "")
    rule_name = basic_info.get("행정규칙명", "")
    rule_type = basic_info.get("행정규칙종류", "")
    article_form = True if basic_info.get("조문형식여부") == "Y" else False
    is_effective = 0 if basic_info.get("현행여부") == "Y" else -1
    dept = basic_info.get("담당부서기관명", "")

    ## 부칙, 별표 ID 리스트
    appendices = extract_appendix_id(admrule_id, admrule_data.get("별표", {}))
    addenda, enact_date = extract_addenda_id(admrule_id, admrule_data.get("부칙", {}))

    ## 첨부파일 리스트
    attachments = admrule_data.get("첨부파일", {})
    file_attached = [
        FileAttached(
            id=link.split("flSeq=")[-1],
            filename=name,
            filelink=link
        )
        for link, name in zip(attachments["첨부파일링크"], attachments["첨부파일명"])
    ]


    metadata = AdmRuleMetadata(
        admrule_id=admrule_id,
        admrule_num=admrule_num,
        announce_num=announce_num,
        announce_date=announce_date,
        enforce_date=enforce_date,
        rule_name=rule_name,
        rule_type=rule_type,
        article_form=article_form,
        is_effective=is_effective,
        hierarchy_laws=hierarchy_laws,
        connected_laws=connected_laws,
        related_addenda=addenda,
        related_appendices=appendices,
        dept=dept,
        enact_date=enact_date,
        file_attached=file_attached,
    )

    return ParserContent(
        metadata=metadata,
        content=[]
    )


# 행정규칙 조회 -> 행정규칙 조문
def parse_admrule_article_info(admrule_info: RuleInfo, article_list:list[str]) -> list[ParserContent]:
    '''
        행정규칙 조문 처리
    '''
    admrule_articles = []
    
    admrule_id = admrule_info.id
    enfoce_date = admrule_info.enforce_date
    is_effective = admrule_info.is_effective
    enact_date = admrule_info.enact_date

    article_chapter = ArticleChapter()
    for article in article_list:
        
        article_chapter.extract_text(article)
        is_preamble = bool(re.search(CHAPTERINFO, article))
        if is_preamble:
            article_num = f"{article_chapter.chapter_num:04d}000"
            title = article_chapter.chapter_title
        else :
            article_num = extract_article_num(article) or "0000000"

        article_id = f"{admrule_id}{article_num}"
        
        # 3. 조문 제목 추출: () 안의 첫 번째 문자열
        title_match = re.search(BLANCKET, article)
        title = title_match.group(1) if title_match else ""


        # 4. 개정일자 추출: "(개정 yyyy. m. d.,  yyyy. m. d.)" 형식에 맞는 날짜 찾기
        matches = re.findall(BLANCKETDATE, article)
        date_matches = []
        for match in matches:
            # 쉼표로 구분된 모든 날짜를 찾기
            date_matches.extend(extract_date_to_yyyymmdd(match))    
        # 최신 날짜 선택
        announce_date = get_latest_date(date_matches, enact_date)

        # 5. 삭제된 조문 처리
        if "삭제" in article:
            announce_date_match = re.search(CHEVRONDATE, article)
            if announce_date_match:
                year, month, day = announce_date_match.groups()
                announce_date = f"{year}{int(month):02d}{int(day):02d}"
            title = "삭제"
        
        # 조문 내용 처리
        content = replace_strip(article.split("\n"))

        # 메타데이터 생성
        metadata = AdmRuleArticleMetadata(
            article_id=article_id,
            article_num=article_num,
            article_title=title,
            article_chapter=article_chapter,
            enforce_date=enfoce_date,
            announce_date=announce_date,
            admrule_id=admrule_id,
            is_effective=is_effective,
            is_preamble=is_preamble,
            related_laws=[],
            related_articles=[],
            related_addenda=[],
            related_appendices=[],
        )
        parsed_article = ParserContent(
            metadata=metadata,
            content=content
        )
        admrule_articles.append(parsed_article)

    return admrule_articles