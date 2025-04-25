from parsers.extractor import extract_related_appendices
from schemas import AddendumMetadata, ParserContent
from commons.utils import format_date
from commons.regex_handler import RegexProcessor
from commons.type_converter import TypeConverter

regex_processor = RegexProcessor()
type_converter = TypeConverter()

# ========================== 법령 부칙 처리 ============================================

def parse_law_addendum_info(law_id: str, addendum_data: dict) -> list[ParserContent]:
    """법령 부칙을 파싱하여 구조화된 콘텐츠로 반환합니다."""
    if not type_converter.validator(addendum_data, dict):
        return []
    
    addendum_list = []
    addendum_units = addendum_data.get("부칙단위")
    
    if not addendum_units:
        return []
    
    addendum_units = type_converter.converter(addendum_units, list[dict], use_default=True)
    
    for item in addendum_units:
        title, number, announce_date = extract_law_addendum_info(item)
        content = type_converter.converter(item.get("부칙내용", []), list[str])
        addendum_content = split_law_addendum_content(content)
        
        metadata = create_addendum_metadata(
            law_id, title, number, announce_date, addendum_content
        )
        
        addendum_result = ParserContent(
            metadata=metadata, 
            content=addendum_content
        )
        addendum_list.append(addendum_result)
    
    return addendum_list

def extract_law_addendum_info(item: dict) -> tuple[str, str, str]:
    """법령 부칙에서 제목, 번호, 공포일자를 추출하는 함수"""
    title = number = announce_date = ""
    
    if not type_converter.validator(item, dict):
        return title, number, announce_date
    
    # 부칙내용에서 제목 추출
    if item.get("부칙내용") and isinstance(item.get("부칙내용"), list) and item.get("부칙내용")[0]:
        title = item.get("부칙내용")[0][0].lstrip() if isinstance(item.get("부칙내용")[0], list) else ""
    
    number = item.get("부칙공포번호", "")
    announce_date = item.get("부칙공포일자", "")
    
    return title, number, announce_date

def split_law_addendum_content(text_data: list[str]) -> list[str]:
    """법령 부칙 본문을 조문 단위로 나누는 함수"""
    if not text_data or not isinstance(text_data, list):
        return []

    contents = []
    buffer = ""
    
    for line in text_data:
        raw_line = line.rstrip()
        line = line.strip()
        
        # 부칙 제목 줄 처리
        if regex_processor.match("ADDENDUM_TITLE", raw_line):
            if buffer:
                contents.append(buffer.strip())
                buffer = ""
            contents.append(line)
            continue
        
        # 들여쓰기 처리
        if raw_line.startswith("  "):
            buffer += " " + line
            continue
        
        # 조문 시작 (제1조, 제2조 등)
        if regex_processor.match("ADDENDUM_ARTICLE", line):
            if buffer:
                contents.append(buffer.strip())
            buffer = line
            continue
        else:
            buffer += " " + line if buffer else line
    
    # 마지막에 남은 buffer 처리
    if buffer:
        contents.append(buffer.strip())
    
    return [c for c in contents if c.strip()]

# ========================== 행정규칙 부칙 처리 ============================================

def parse_admrule_addendum_info(law_id: str, addendum_data: dict) -> list[ParserContent]:
    """행정규칙 부칙을 파싱하여 구조화된 콘텐츠로 반환합니다."""
    if not type_converter.validator(addendum_data, dict):
        return []
    
    addendum_list = []
    addendum_units = addendum_data.get("부칙내용")
    
    if not addendum_units:
        return []
    
    addendum_units = type_converter.converter(addendum_units, list[str], use_default=True)
    
    for item in addendum_units:
        title, number, announce_date = extract_admrule_addendum_info(item)
        addendum_content = split_admrule_addendum_content(title, item)
        
        metadata = create_addendum_metadata(
            law_id, title, number, announce_date, addendum_content
        )
        
        addendum_result = ParserContent(
            metadata=metadata, 
            content=addendum_content
        )
        addendum_list.append(addendum_result)
    
    return addendum_list

def extract_admrule_addendum_info(item: str) -> tuple[str, str, str]:
    """행정규칙 부칙에서 제목, 번호, 공포일자를 추출하는 함수"""
    title = number = announce_date = ""
    
    if not type_converter.validator(item, str):
        return title, number, announce_date
    
    item = item.strip()
    title_match = regex_processor.search("ADDENDUM_TITLE", item)
    title = title_match.group(0) if title_match else ""
    
    number_match = regex_processor.search("ADDENDUM_NUM", item)
    number = number_match.group(1) if number_match else ""
    
    date_match = regex_processor.search("DATE", item)
    if date_match:
        year, month, day = date_match.groups()
        announce_date = format_date(year, month, day)
    
    return title, number, announce_date

def split_admrule_addendum_content(title: str, text: str) -> list[str]:
    """행정규칙 부칙 본문을 조문 단위로 나누는 함수"""
    contents = []
    
    if not type_converter.validator(text, str):
        return contents
    
    line = text.strip()
    
    if title and title in line:
        contents.append(title)
        line = regex_processor.sub("ADDENDUM_TITLE", "", line).strip()
    
    parts = regex_processor.split("ADDENDUM_ARTICLE", line)
    if len(parts) == 1:
        if parts[0]:
            contents.append(parts[0].strip())
    else:
        for i in range(1, len(parts), 2):
            article = parts[i]
            content = parts[i + 1].strip() if i + 1 < len(parts) else ""
            contents.append(f"{article} {content}")
    
    return [c for c in contents if c.strip()]

def create_addendum_metadata(
    law_id: str,
    title: str,
    number: str,
    announce_date: str,
    content: list[str]
) -> AddendumMetadata:
    """부칙 메타데이터를 생성하는 함수"""
    related_laws = [match.group(1) for match in regex_processor.finditer("BLANKET", title)]
    related_appendices = extract_related_appendices(law_id, content)
    
    return AddendumMetadata(
        addendum_id=f"{law_id}{announce_date}",
        addendum_num=number,
        addendum_title=title,
        announce_date=announce_date,
        law_id=law_id,
        related_laws=related_laws,
        related_articles=[],
        related_appendices=related_appendices,
        is_exit=False
    )

# 메인 함수 - 타입에 따라 적절한 파서 호출
def parse_addendum_info(law_id: str, addendum_data: dict, is_admrule: bool = True) -> list[ParserContent]:
    """법령 또는 행정규칙의 부칙을 파싱하여 구조화된 콘텐츠로 반환합니다."""
    if is_admrule:
        return parse_admrule_addendum_info(law_id, addendum_data)
    else:
        return parse_law_addendum_info(law_id, addendum_data)
