from schemas.schema import ConnectedLaws, HierarchyLaws


def parse_hierarchy_laws(data, parent_id="None"):
    """법령체계도에서 상하위법 정보를 추출하는 함수
    """
    hierarchy_laws: list[HierarchyLaws] = []

    def extract_laws(node, parent_id):
        """DFS 기반"""
        if isinstance(node, dict):
            if "기본정보" in node:
                info = node["기본정보"]
                law_id = info.get("법령일련번호") or info.get("행정규칙일련번호", "")
                
                # 만약 law_id가 hierarchy_laws 리스트 안에 있다면 건너뛰기
                if any(law.law_id == law_id for law in hierarchy_laws):
                    return
                
                law_name = info.get("법령ID") or info.get("행정규칙ID")
                law_type = info.get("법종구분", {})
                
                law = HierarchyLaws(
                    law_id=law_id,
                    law_num=law_name,
                    law_code=law_type.get("법종구분코드", ""),
                    law_type=law_type.get("content", ""),
                    law_name=info.get("법령명") or info.get("행정규칙명", ""),
                    parent_id=parent_id
                )
                hierarchy_laws.append(law)
                parent_id = law_id

            for key, value in node.items():
                # "자치법규"는 무시
                if key == "자치법규":
                    continue
                
                # "행정규칙" 내부의 "고시", "예규", "훈령"은 탐색
                if parent_id and parent_id.startswith("행정규칙"):
                    if key not in ["고시", "예규", "훈령"]:
                        continue

                extract_laws(value, parent_id)

        elif isinstance(node, list):
            for item in node:
                extract_laws(item, parent_id)
    if data:
        extract_laws(data["상하위법"], parent_id)
    return hierarchy_laws

def parse_connected_laws(data):
    """법령체계도에서 관련법령 정보를 추출하는 함수
    """
    connected_laws: list[ConnectedLaws] = [] # 관련법 정보 리스트
    conlaw_dict = data.get("관련법령")

    if not isinstance(conlaw_dict, dict):
        return connected_laws
    
    conlaw_data = conlaw_dict.get("conlaw")

    # conlaw_date의 type을 list로 통일
    conlaw_data = conlaw_data if isinstance(conlaw_data, list) else [conlaw_data]

    
    for conlaw in conlaw_data:
        conlaw_id = conlaw.get("법령일련번호") or conlaw.get("행정규칙일련번호")
        conlaw_num = conlaw.get("법령ID") or conlaw.get("행정규칙ID")
        conlaw_name = conlaw.get("법령명") or conlaw.get("행정규칙명") or ""
        conlaw_code = conlaw.get("법종구분", {}).get("법종구분코드", "")
        conlaw_type = conlaw.get("법종구분", {}).get("content", "")

        if conlaw_id:
            connected_laws.append(ConnectedLaws(law_id=conlaw_id, law_num=conlaw_num, law_code=conlaw_code, law_type=conlaw_type, law_name=conlaw_name))

    return connected_laws

def categorize_law_ids(hier_laws: list[HierarchyLaws], connected_laws:list[ConnectedLaws]):
    """상하위법 및 관련법 정보를 기반으로 law와 admrule을 분류"""
    realted_law_ids = {"law": [], "admrule": []}

    for law in hier_laws + connected_laws:
        if law.law_code.startswith("A") and law.law_id not in realted_law_ids["law"]:
            realted_law_ids["law"].append(law.law_id)
        elif law.law_code.startswith("B") and law.law_id not in realted_law_ids["admrule"]:
            realted_law_ids["admrule"].append(law.law_id)

    return realted_law_ids

def parse_law_relationships(data):
    """법령 체계도 데이터를 파싱하여 상하위법, 관련법, 법령 ID 목록을 반환"""
    if not data:
        return None
    hier_laws = parse_hierarchy_laws(data)
    connected_laws = parse_connected_laws(data)
    related_laws_id = categorize_law_ids(hier_laws, connected_laws)

    return hier_laws, connected_laws, related_laws_id