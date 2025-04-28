from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Union
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

class VectorRegisterRequest(BaseModel):
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
    vdb_id: int
    description: Optional[str] = None
    serving_id: int
    serving_rev_id: int
    preprocessor_id: int
    batch_size: int
    params: str         # ex) "{\"chunk_size\":1000,\"chunk_overlap\":100}",
    files: List[DocumentFile]

class FileData(BaseModel):
    filename: str
    fullpath: str
    temporary_name: str

class RegisterData(BaseModel):
    doc_ids: List[int]
    upsert_ids: List[int]

class LoginData(BaseModel):
    access_token: str
    refresh_token: str
    is_password_change_required: bool
    is_reset_required: bool
    is_consent_required: bool
    user: dict
    
class VectorAPIResponse(BaseModel):
    code: int
    errMsg: str
    data: Union[FileData, RegisterData, LoginData, None]

