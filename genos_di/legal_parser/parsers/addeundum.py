import re

from constants import ADDENDUMNUM, ADDENDUMTITLE, DATE
from schemas import AddendumMetadata, ParserContent
from utils import replace_strip


def parse_addendum_law_info(law_id: str, addendum_data:dict) -> list[ParserContent]:
    '''
        법령(법률, 시행령, 행정규칙) 부칙 조회
        TODO 조문에서 가장 오래된 기준 날짜 받아와서 그 전의 부칙은 넣지 않기.
    '''
    addendum_list = []
    for item in addendum_data.get("부칙단위", []):
        announce_date = item.get("부칙공포일자")
        addendum_id = f"{law_id}{announce_date}"
        addendum_num = item.get("부칙공포번호")
        addendum_text = replace_strip(item.get("부칙내용")[0])

        addendum_meta = AddendumMetadata(
            addendum_id=addendum_id,
            addendum_num=addendum_num,
            addendum_title=addendum_text[0],
            announce_date=announce_date,
            law_id=law_id,
            related_laws=[],
            related_articles=[],
        )

        # TODO 법령 조문처럼 조문단위로 끊어서 주기. 정규표현식 사용
        addendum_content = addendum_text
        
        addendum_result = ParserContent(
            metadata=addendum_meta,
            content=addendum_content
        )
        addendum_list.append(addendum_result)
    return addendum_list    

def parse_addendum_admrule_info(admrule_id:str, admrule_addendums:dict) -> list[ParserContent]:
    '''
        행정규칙 부칙 조회
    '''

    def extract_addendum_info(item: str):
        """부칙(행정규칙)에서 제목, 부칙번호, 공포날짜를 추출하는 함수"""
        title_match = re.search(ADDENDUMTITLE, item)
        title = title_match.group(0) if title_match else None

        number_match = re.search(ADDENDUMNUM, item)
        number = number_match.group(1) if number_match else None

        date_match = re.search(DATE, item)
        announce_date = f"{date_match.group(1)}{int(date_match.group(2)):02d}{int(date_match.group(3)):02d}" if date_match else None

        return title, number, announce_date
    
    admrule_addendums_result = []
    announce_dates = admrule_addendums.get('부칙공포일자', [])
    content_list = admrule_addendums.get('부칙내용', [])
    numbers = admrule_addendums.get('부칙공포번호', [])

    stripped_contents = replace_strip(content_list) # WATCH
    
    extracted_data = [extract_addendum_info(item) for item in stripped_contents]
    titles, numbers, announce_dates = zip(*extracted_data)

    contents = [item if isinstance(item, list) else [item] for item in stripped_contents]

    admrule_addendums_result = [
        ParserContent(
            metadata=AddendumMetadata(
                addendum_id=f"{admrule_id}{announce_dates[i]}",
                addendum_num=numbers[i],
                addendum_title=titles[i],
                announce_date=announce_dates[i],
                law_id=admrule_id,
                related_laws=[],
                related_articles=[]
            ),
            content=contents[i]
        )
        for i in range(len(contents))
    ]
    return admrule_addendums_result