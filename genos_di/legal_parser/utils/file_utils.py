import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import DefaultDict

import pandas as pd
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def export_json(data, id, num, is_admrule=True, is_input=False):
    if is_input: 
        output_file = f"inputs/response_{id}.json"
    else :
        rule_type = 'admrule' if is_admrule else 'law'
        output_file = f"result/{rule_type}_{id}_{num}.json"
    logger.info(f"JSON 데이터 저장: ID={id}, 파일 경로={output_file}")
    with open(f'resources/{output_file}', "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def load_json_as_model(json_file: str, model: type[BaseModel]) -> BaseModel:
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return model.model_validate(data)


def load_json(key):
    base_path = Path(__file__).parent.parent / "resources"  # legal_parser 기준으로 상위 폴더 접근
    file_path = base_path / "inputs" / f"response_{key}"

    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)

def load_keys_from_csv() -> DefaultDict[str, list[str]]:
    """법령검색목록.csv 파일에서 '법령MST'를, 행정규칙검색목록.csv에서 '행정규칙ID' 리스트를 추출합니다.

    Returns:
         defaultdict[str, list[str]]: 
            'law_ids': 법령MST 리스트,
            'admrule_ids' : 행정규칙ID 리스트

    """
    law_csv_path = Path("resources/inputs/법령검색목록.csv")
    admrule_csv_path = Path("resources/inputs/행정규칙검색목록.csv")

    law_ids_dict: DefaultDict[str, list[str]] = defaultdict(list)

    # 필요한 열만 읽기 (메모리와 속도 최적화)
    try:
        law_id_series = pd.read_csv(law_csv_path, usecols=['법령MST'], dtype=str, header=1).squeeze('columns')
        law_ids_dict['law_ids'] = law_id_series.dropna().values.tolist()
    except ValueError as e:
        raise ValueError(f"'법령MST' 컬럼을 찾을 수 없습니다: {e}") from e
    
    try:
        admrule_id_series = pd.read_csv(admrule_csv_path, usecols=['행정규칙ID'], dtype=str, header=1).squeeze('columns')
        law_ids_dict['admrule_ids'] = admrule_id_series.dropna().values.tolist()

    except ValueError as e:
        raise ValueError(f"'행정규칙ID' 컬럼을 찾을 수 없습니다: {e}") from e
    
    return law_ids_dict
