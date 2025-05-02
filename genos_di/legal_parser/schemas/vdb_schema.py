from typing import Optional

from pydantic import BaseModel, Field, field_validator

from commons.utils import normalize_to_nfc

#  From https://github.com/mindsandcompany/GenOS/blob/develop/admin-api/src/schema/data/vdb_schema.py

class DocumentFile(BaseModel):
    """
    문서 파일을 나타내는 스키마입니다.
    
    속성:
        name (str): 사용자 선택 위치의 상대 경로로 보내야 하는 파일 경로.
        path (str): 파일의 전체 경로.
    """
    name: str = Field(..., description="파일 경로. 사용자 선택한 위치의 상대 경로로 보내주세요")
    path: str
    
    @field_validator("name", mode='before')
    def validate_params(cls, value):
        return normalize_to_nfc(value)

class VDBRegisterRequest(BaseModel):
    """
    벡터 문서를 등록하는 요청을 나타내는 스키마입니다.
    
    속성:
        vdb_id (int): 벡터 데이터베이스의 ID.
        description (Optional[str]): 벡터 문서에 대한 설명 (선택 사항).
        serving_id (int): 벡터 문서의 서빙 ID.
        serving_rev_id (int): 서빙 문서의 리비전 ID.
        preprocessor_id (int): 전처리기 ID.
        batch_size (int): 처리할 배치 크기.
        params (str): 처리 파라미터 (예: 청크 크기 및 겹침 크기).
        files (List[DocumentFileSchema]): 등록할 문서 파일 목록.

    From DocumentCreateRequestV2(#L171)
    """
    vdb_id: str
    description: Optional[str] = None
    serving_id: int
    serving_rev_id: int
    preprocessor_id: int
    batch_size: int
    params: str         # ex) "{\"chunk_size\":1000,\"chunk_overlap\":100}",
    files: list[DocumentFile]  # 하나씩 올리기를 권장



class DataModel(BaseModel):
    files: list[DocumentFile]

class VDBUploadResponse(BaseModel):
    code: int
    errMsg: str
    data: Optional[DataModel]

class RegisterData(BaseModel):
    doc_ids: list[int]
    upsert_ids: list[int]

class VDBRegisterResponse(BaseModel):
    code: int
    errMsg: str
    data: RegisterData

class LawVectorResult(BaseModel):
    law_id: str
    law_num: str
    law_type: str  # 법령 / 행정규칙
    file_name: str
    doc_id: int
    vdb_id: int
    upsert_id: int

class LawInfo(BaseModel):
    law_type: str  # "law" or "admrule"
    law_id: str
    law_num: str
    filename: str  # 파일명을 포함하여 추후 참조

class VDBResponse(BaseModel):
    total_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    fail_filenames: set[str] = set()

    def increment_total(self):
        self.total_count += 1

    def increment_success(self):
        self.success_count += 1

    def increment_fail(self, filename: str):
        self.fail_count += 1
        self.fail_filenames.add(filename)
