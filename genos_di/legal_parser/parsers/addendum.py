import re

from constants import ADDENDUMNUM, ADDENDUMTITLE, BLANCKET, DATE
from extractor import extract_related_appendices
from schemas import AddendumMetadata, ParserContent


def parse_addendum_info(law_id: str, addendum_data: dict, is_admrule: bool = False) -> list[ParserContent]:
    """법령 또는 행정규칙의 부칙을 파싱하여 구조화된 콘텐츠로 반환합니다.
    is_admrule이 True일 경우 행정규칙 부칙을 처리합니다.
    """
    # TODO: 조문에서 가장 오래된 기준 날짜 받아와서 그 전의 부칙은 넣지 않기.
    # 공통: 부칙 본문을 조문 단위로 나누기 위한 내부 함수
    def split_addendum_content(title: str, text: list[str], is_admrule: bool=False) -> list[str]:
        contents = []

        if is_admrule:
            ARTICLE_PATTERN = re.compile(r"\s*(제\d+조\s*\(.*?\))")

            # 행정규칙: 한 줄에 모든 조문이 섞여 있을 수 있으므로, 문자열 기준으로 분리
            for raw in text:
                line = raw.strip()
                section = []

                if title and title in line:
                    contents.append(title)
                    line = re.sub(ADDENDUMTITLE, "", line).strip()

                parts = ARTICLE_PATTERN.split(line)
                if len(parts) == 1:
                    if parts[0]:
                        section.append(parts[0].strip())
                else:
                    for i in range(1, len(parts), 2):
                        # "제N조 (내용)" 형태로 구성
                        article = parts[i]
                        content = parts[i + 1].strip() if i + 1 < len(parts) else ""
                        section.append(f"{article} {content}")

                contents.extend(section)

        else:
            ARTICLE_PATTERN = re.compile(r"^\s*(제\d+조(?:\s*\([^)]*\))?)")  # 조문 패턴
            ADDENDUM_PATTERN = re.compile(r"^\s*부칙\s*\(.*?\)\s*<[^>]+>")     # 부칙 제목 패턴

            buffer = ""
            contents = []  # contents 배열 초기화

            for line in text:
                raw_line = line.rstrip()  # 오른쪽 공백 제거
                line = line.strip()  # 양쪽 공백 제거

                # 타이틀 추가 (첫 번째 줄에서만)
                if not contents:
                    if title in line:
                        contents.append(title)
                        line = re.sub(ADDENDUMTITLE, "", line)  # 부칙 제목 관련 처리

                # 부칙 제목 줄 처리
                if ADDENDUM_PATTERN.match(raw_line):
                    if buffer:
                        contents.append(buffer.strip())
                        buffer = ""  # buffer 초기화
                    contents.append(line)  # 부칙 제목 추가
                    continue  # 부칙 제목 처리 후 다음 줄로
                               
                if raw_line.startswith("  "):
                    buffer += " " + line  # 공백으로 시작하면 buffer에 이어붙이기
                    continue

                # 조문 시작 (제1조, 제2조 등)
                if ARTICLE_PATTERN.match(line):
                    if buffer:
                        contents.append(buffer.strip())  # 이전 내용 추가
                    buffer = line  # 새 조문 시작
                    continue  # 새 조문이면 계속 처리
                # 들여쓰기된 줄 처리 (하위 항목이면 buffer에 이어붙이기)
                else:
                    buffer += " " + line if buffer else line  # 공백 시작이 아니면 새로운 줄을 buffer에 추가

            # 마지막에 남은 buffer 처리
            if buffer:
                contents.append(buffer.strip())
        return [c for c in contents if c.strip()]


    # 공통: 부칙 내용에서 제목, 부칙번호, 공포일자를 추출하는 함수
    def extract_addendum_info(item: str):
        if is_admrule:
            # 행정규칙 부칙의 경우 제목, 번호, 공포일자를 정규식으로 추출
            title_match = re.search(ADDENDUMTITLE, item)
            title = title_match.group(0) if title_match else None
            number_match = re.search(ADDENDUMNUM, item)
            number = number_match.group(1) if number_match else None
            date_match = re.search(DATE, item)
            announce_date = f"{date_match.group(1)}{int(date_match.group(2)):02d}{int(date_match.group(3)):02d}" if date_match else None
        else:
            # 법령 부칙은 기본적으로 부칙내용에서 직접 추출
            title = item.get('부칙내용', [])[0][0].lstrip()
            number = item.get("부칙공포번호")
            announce_date = item.get("부칙공포일자")

        return title, number, announce_date

    addendum_list = []
    addendum_units = addendum_data.get("부칙단위", []) if not is_admrule else addendum_data.get('부칙내용', [])
    
    for item in addendum_units:
        title, number, announce_date = extract_addendum_info(item)
        if is_admrule:
            item = item if isinstance(item, list) else [item]
            addendum_content = split_addendum_content(title, item, True)
        else:
            addendum_content = split_addendum_content(title, item.get("부칙내용")[0])

        # 공통: 부칙 내용에서 관련 법령, 관련 별표 추출
        related_laws = [match.group(1) for match in re.finditer(BLANCKET, title if is_admrule else title)]
        related_appendices = extract_related_appendices(law_id, addendum_content)

        # 메타데이터 생성
        addendum_meta = AddendumMetadata(
            addendum_id=f"{law_id}{announce_date}",
            addendum_num=number,
            addendum_title=title,
            announce_date=announce_date,
            law_id=law_id,
            related_laws=related_laws,
            related_articles=[],
            related_appendices=related_appendices
        )

        addendum_result = ParserContent(
            metadata=addendum_meta,
            content=addendum_content
        )

        addendum_list.append(addendum_result)

    return addendum_list

