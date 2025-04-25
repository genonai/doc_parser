
from parsers.extractor import (
    extract_addenda_id,
    extract_appendix_id,
)
from schemas import (
    AdmRuleMetadata,
    FileAttached,
    ParserContent,
)
from commons.regex_handler import RegexProcessor
from commons.type_converter import TypeConverter

type_converter = TypeConverter()
regex_processor = RegexProcessor()


def parse_admrule_info(admrule_id: str, admrule_data: dict, hierarchy_laws, connected_laws) -> ParserContent:
    """행정규칙 정보를 파싱하여 ParserContent 객체로 반환하는 함수"""
    # 기본 정보 추출
    basic_info = extract_basic_info(admrule_data)
    
    # 부칙 정보 추출
    addenda, enact_date = extract_addenda_info(admrule_id, admrule_data)
    
    # 별표 정보 추출
    appendices = extract_appendix_info(admrule_id, admrule_data)
    
    # 첨부파일 정보 추출
    file_attached = extract_file_attachments(admrule_data)
    
    # 메타데이터 생성
    metadata = create_admrule_metadata(
        admrule_id=admrule_id,
        basic_info=basic_info,
        hierarchy_laws=hierarchy_laws,
        connected_laws=connected_laws,
        addenda=addenda,
        appendices=appendices,
        enact_date=enact_date,
        file_attached=file_attached
    )
    
    return ParserContent(metadata=metadata, content=[])

def extract_basic_info(admrule_data: dict) -> dict:
    """행정규칙 기본 정보를 추출하는 함수"""
    basic_info = admrule_data.get("행정규칙기본정보", {})
    
    return {
        "admrule_num": basic_info.get("행정규칙ID", ""),
        "announce_num": basic_info.get("발령번호", ""),
        "announce_date": basic_info.get("발령일자", ""),
        "enforce_date": basic_info.get("시행일자", ""),
        "rule_name": basic_info.get("행정규칙명", ""),
        "rule_type": basic_info.get("행정규칙종류", ""),
        "article_form": True if basic_info.get("조문형식여부") == "Y" else False,
        "is_effective": 0 if basic_info.get("현행여부") == "Y" else -1,
        "dept": basic_info.get("담당부서기관명", "")
    }

def extract_addenda_info(admrule_id: str, admrule_data: dict) -> tuple[list, str]:
    """부칙 정보를 추출하는 함수"""
    addenda = []
    enact_date = "00000000"
    
    if admrule_data.get("부칙"):
        try:
            addenda_data = type_converter.converter(admrule_data.get("부칙"), list[dict])
            addenda, enact_date = extract_addenda_id(admrule_id, addenda_data)
        except Exception:
            # 변환 실패 시 기본값 유지
            pass
    
    return addenda, enact_date

def extract_appendix_info(admrule_id: str, admrule_data: dict) -> list:
    """별표 정보를 추출하는 함수"""
    appendices = []
    appendix_data = admrule_data.get("별표")
    
    if appendix_data and appendix_data.get("별표단위"):
        try:
            appendix_units = type_converter.converter(appendix_data.get("별표단위"), list[dict])
            appendices = extract_appendix_id(admrule_id, appendix_units)
        except Exception:
            # 변환 실패 시 기본값 유지
            pass
    
    return appendices

def extract_file_attachments(admrule_data: dict) -> list[FileAttached]:
    """첨부파일 정보를 추출하는 함수"""
    file_attached = []
    attachments = admrule_data.get("첨부파일")
    
    if type_converter.validator(attachments, dict):
        file_attached = [
            FileAttached(
                id=link.split("flSeq=")[-1],
                filename=name,
                filelink=link
            )
            for link, name in zip(attachments.get("첨부파일링크", []), attachments.get("첨부파일명", []))
        ]
    
    return file_attached

def create_admrule_metadata(
    admrule_id: str,
    basic_info: dict,
    hierarchy_laws: list,
    connected_laws: list,
    addenda: list,
    appendices: list,
    enact_date: str,
    file_attached: list
) -> AdmRuleMetadata:
    """행정규칙 메타데이터 객체를 생성하는 함수"""
    return AdmRuleMetadata(
        admrule_id=admrule_id,
        admrule_num=basic_info["admrule_num"],
        announce_num=basic_info["announce_num"],
        announce_date=basic_info["announce_date"],
        enforce_date=basic_info["enforce_date"],
        rule_name=basic_info["rule_name"],
        rule_type=basic_info["rule_type"],
        article_form=basic_info["article_form"],
        is_effective=basic_info["is_effective"],
        hierarchy_laws=hierarchy_laws,
        connected_laws=connected_laws,
        related_addenda_admrule=addenda,
        related_appendices_admrule=appendices,
        dept=basic_info["dept"],
        enact_date=enact_date,
        file_attached=file_attached,
    )


