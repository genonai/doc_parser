from constants import LAWFIELD
from schemas import ArticleChapter, LawArticleMetadata, LawMetadata, ParserContent, RuleInfo
from utils import (
    extract_addenda_id,
    extract_appendix_id,
    extract_latest_announce,
    replace_strip,
)


# 법령본문 조회 -> 법령
def parse_law_info(law_id: str, law_data: dict, hierarchy_laws, connected_laws,) -> ParserContent:
    law:dict = law_data.get('기본정보')

    # 소관부처 : 소관부처명 + 연락부서 부서명
    office = law["연락부서"]["부서단위"]
    dept = f"{office['소관부처명']} {office['부서명']}" 

    #법 분야명: 편 번호 -> 법 분야 dict에서 조회
    law_field = LAWFIELD.get(int(law.get("편장절관")[:2]))
    
    ## 부칙 ID (법령 키 + 부칙 공포일자) 리스트
    addenda_data: list[dict] = law_data.get("부칙").get("부칙단위", [])
    addenda, enact_date = extract_addenda_id(law_id, addenda_data)

    ## 별표 ID 리스트
    appendix_data:list[dict] = law_data.get("별표", {})
    appendices = extract_appendix_id(law_id, appendix_data)

    metadata = LawMetadata(
        law_id=law_id,
        law_num=law.get("법령ID"),
        announce_num=law.get("공포번호"),
        announce_date=law.get("공포일자"),
        enforce_date=law.get("시행일자"),
        law_name=law.get("법령명_한글"),
        law_short_name=law.get("법령명약칭"),
        law_type=law.get("법종구분", {}).get("content", ""),
        law_field=law_field,
        is_effective=0, 
        hierarchy_laws=hierarchy_laws,
        connected_laws=connected_laws,  
        related_addenda_law=addenda,  
        related_appendices=appendices,  
        dept=dept if dept else None,
        enact_date=enact_date,
    )

    return ParserContent(
        metadata=metadata,
        content=[]
    )

# 법령 조문 내용 처리
def stringify_article_content(data: dict) -> list[str]:
    """
    법령 조문 데이터를 문자열 리스트로 변환하는 함수
    """
    content = []

    # 조문 내용 추가
    if "조문내용" in data and data["조문내용"]:
        content.append(data["조문내용"].strip())

    # 항 내용 처리 함수
    def process_paragraphs(paragraphs):
        for paragraph in paragraphs:
            if "항내용" in paragraph:
                text = paragraph["항내용"]
                if isinstance(text, list):
                    text = text[0][0] if text and isinstance(text[0], list) else text[0]
                content.append(text.strip())

            # 호(조항) 처리
            if "호" in paragraph:
                process_subparagraphs(paragraph["호"])

    # 호 내용 처리 함수
    def process_subparagraphs(subparagraphs):
        for subparagraph in subparagraphs:
            if "호내용" in subparagraph:
                text = subparagraph["호내용"]
                if isinstance(text, list):
                    text = text[0][0] if text and isinstance(text[0], list) else text[0]
                content.append(text.strip())

            # 목(세부 조항) 처리
            if "목" in subparagraph:
                process_items(subparagraph["목"])

    # 목 내용 처리 함수
    def process_items(items):
        for item in items:
            if isinstance(item["목내용"], list):
                text = replace_strip(item["목내용"][0])
                content.extend(text)
            else:
                text = item["목내용"]
                content.append(text.strip())

    # 항이 리스트 또는 딕셔너리인 경우 모두 처리
    paragraphs = data.get("항", [])
    if isinstance(paragraphs, dict):
        paragraphs = [paragraphs]

    if paragraphs:
        process_paragraphs(paragraphs)

    return content

# 법령본문 조회 -> 조문
def parse_law_article_info(law_info:RuleInfo, article_data:dict) -> list[ParserContent]:
    
    article_list = []
    
    law_id = law_info.id
    enact_date = law_info.enact_date
    is_effective = law_info.is_effective

    article_units = article_data.get("조문단위", [])

    article_chapter = ArticleChapter()
    current_chapter = None

    for item in article_units:
        article_num = item.get("조문키")
        article_id = f"{law_id}{article_num}"
        article_title = item.get("조문제목", "")
        enforce_date = item.get("조문시행일자")
        article_content = stringify_article_content(item)
        is_preamble = True if item.get("조문여부") == "전문" else False

        # 전문인 경우 장 번호로 article_num 대체
        if is_preamble:
            content = item.get("조문내용", "")
            article_chapter.extract_text(content)
            current_chapter = ArticleChapter(
                chapter_num=article_chapter.chapter_num,
                chapter_title=article_chapter.chapter_title,
                section_num=article_chapter.section_num,
                section_title=article_chapter.section_title,
            )
            article_num = f"{article_chapter.chapter_num:04d}000"
        
        announce_date = extract_latest_announce(item, enact_date)

        artice_meta = LawArticleMetadata(
            article_id=article_id,
            article_num=article_num,
            is_preamble=is_preamble,
            article_title=article_title,
            article_chapter=current_chapter or article_chapter,
            enforce_date=enforce_date,
            announce_date=announce_date,
            law_id=law_id,
            is_effective=is_effective,
            related_laws=[],
            related_appendices=[],
            related_addenda=[],
            related_articles=[],
        )

        article_result = ParserContent(
            metadata=artice_meta,
            content=article_content
        )

        article_list.append(article_result)    
    return article_list