import json
import os
from datetime import datetime
from pathlib import Path

import aiofiles
import pandas as pd
import pytz
from pydantic import BaseModel

from commons.constants import (
    DIR_PATH_LAW_INPUT,
    DIR_PATH_LAW_RESULT,
    DIR_PATH_VDB_RESULT,
    FILE_PATH_ADMRULE_CSV,
    FILE_PATH_LAW_CSV,
)
from commons.loggers import ErrorLogger, MainLogger
from schemas.schema import ParserRequest
from schemas.vdb_schema import LawFileInfo, VDBUploadFile

main_logger = MainLogger.instance()
error_logger = ErrorLogger.instance()

def export_json(data, id, num, is_admrule=True):
    rule_type = 'admrule' if is_admrule else 'law'
    os.makedirs(DIR_PATH_LAW_RESULT, exist_ok=True)

    output_file = f"{DIR_PATH_LAW_RESULT}/{rule_type}_{id}_{num}.json"
    main_logger.debug(f"JSON 데이터 저장: ID={id}, 파일 경로={output_file}")
    with open(f'{output_file}', "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def export_mapping_json(data):
    os.makedirs(DIR_PATH_VDB_RESULT, exist_ok=True)

    seoul_tz = pytz.timezone('Asia/Seoul')
    created_at = datetime.now(seoul_tz).strftime('%Y%m%d')    
    output_file = f"{DIR_PATH_VDB_RESULT}/vdb_info_{created_at}.json"
    with open(f'{output_file}', "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    main_logger.debug(f"JSON 데이터 저장 성공, 파일 경로={output_file}")
   

def export_json_input(data, id):
    output_file = f"{DIR_PATH_LAW_INPUT}/response_{id}.json"
    main_logger.debug(f"OPENAPI 데이터 다운로드: ID={id}, 파일 경로={output_file}")
    with open(f'{output_file}', "w", encoding="utf-8") as f:
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

def load_keys_from_csv() -> ParserRequest:
    """법령검색목록.csv 파일에서 '법령MST'를, 행정규칙검색목록.csv에서 '행정규칙ID' 리스트를 추출합니다.

    Returns:
         defaultdict[str, list[str]]: 
            'law_ids': 법령MST 리스트,
            'admrule_ids' : 행정규칙ID 리스트

    """
    law_csv_path = Path(FILE_PATH_LAW_CSV)
    admrule_csv_path = Path(FILE_PATH_ADMRULE_CSV)

    request = ParserRequest()

    # 법령MST, 행정규칙ID 열만 읽기
    try:
        law_id_series = pd.read_csv(law_csv_path, usecols=['법령MST'], dtype=str, header=1).squeeze('columns')
        request.law_ids = law_id_series.dropna().values.tolist()
    except ValueError as e:
        raise ValueError(f"'법령MST' 컬럼을 찾을 수 없습니다: {e}") from e
    
    try:
        admrule_id_series = pd.read_csv(admrule_csv_path, usecols=['행정규칙ID'], dtype=str, header=1).squeeze('columns')
        request.admrule_ids = admrule_id_series.dropna().values.tolist()

    except ValueError as e:
        raise ValueError(f"'행정규칙ID' 컬럼을 찾을 수 없습니다: {e}") from e
    
    return request

async def extract_law_infos(dir_path: str) -> list[LawFileInfo]:
    """
    지정된 디렉토리의 파일명으로부터 법령 메타 정보를 추출합니다.

    파일명 규칙: law_<id>_<num>.json or admrule_<id>_<num>.json

    Returns:
        List[LawFileInfo]: 파일별 법령 정보 리스트
    """
    law_infos: list[LawFileInfo] = []

    for filename in sorted(os.listdir(dir_path)):
        if not filename.endswith('.json'):
            continue
        try:
            name, _ = os.path.splitext(filename)
            law_type, law_id, law_num = name.split("_")
        except ValueError as e:
            error_logger.vdb_error(f"[extract_law_infos] 잘못된 형식의 파일명입니다. {filename}", e)
            continue

        law_infos.append(
            LawFileInfo(
                law_type=law_type,
                law_id=law_id,
                law_num=law_num,
                filename=filename
            )
        )
    return law_infos

async def extract_local_files(dir_path: str) -> list[VDBUploadFile]:
    upload_files: list[VDBUploadFile] = []

    for filename in sorted(os.listdir(dir_path)):
        if not filename.endswith(".json"):
            continue

        full_path = os.path.join(dir_path, filename)

        async with aiofiles.open(full_path, "rb") as f:
            content = await f.read()

        upload_files.append(
            VDBUploadFile(file_name=filename, file_content=content))

    return upload_files