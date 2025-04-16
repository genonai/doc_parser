from typing import Literal, Optional
from urllib.parse import urlencode

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator


class APIEndpoints(BaseModel):
    base_url: AnyHttpUrl = "https://www.law.go.kr/DRF/"
    item_endpoint: str = "lawService.do"
    list_endpoint: str = "lawSearch.do"

    def get_full_url(self, query: str, is_item: bool = True) -> str:
        endpoint = self.item_endpoint if is_item else self.list_endpoint
        return f"{self.base_url}{endpoint}?{query}"


class BaseRequestParams(BaseModel):
    """공통 Request Params Schema
    """

    OC: Literal["mer013"] = Field("mer013")
    type: Literal["HTML", "XML", "JSON"] = Field(
        "JSON", description="출력 형태: HTML/XML/JSON (필수)"
    )

    def get_query_params(self) -> str:
        """클래스 데이터를 쿼리 문자열로 변환"""
        params_dict = self.model_dump(exclude_none=True)  # None 값 제외
        query_keyword = params_dict.pop("query", None)
        encoded_params = urlencode(params_dict)

        if query_keyword:
            return f"{encoded_params}&query={query_keyword}"
        else:
            return encoded_params  # URL 인코딩된 쿼리 문자열 반환


class LawItemRequestParams(BaseRequestParams):
    """현행 법령 본문 조회 API Request Params
    """

    target: Literal["law"] = Field("law")
    ID: Optional[str] = Field(None, description="법령 ID (ID 또는 KEY 중 하나 필수)")
    MST: Optional[str] = Field(
        None, description="법령 마스터 번호", examples=["264627"]
    )
    JO: Optional[str] = Field(
        None,
        description=(
            "조번호: 생략 시 모든 조를 표시. "
            "6자리 숫자 (조번호+조가지번호, 예: 000200 - 2조, 001002 - 10조의 2)"
        ),
    )

    @model_validator(mode="after")
    def validate_id_or_mst(
        cls, values: "LawItemRequestParams"
    ) -> "LawItemRequestParams":
        """ID 또는 MST 중 하나는 반드시 입력해야 함"""
        if not values.ID and not values.MST:
            raise ValueError("ID 또는 MST 중 하나는 반드시 입력해야 합니다.")
        return values

    @field_validator("JO")
    def validate_jo_format(cls, value):
        if value and not (00000 <= value <= 999999):
            raise ValueError("JO는 6자리 숫자여야 합니다 (예: 000200).")
        return value


class LawSystemRequestParams(BaseRequestParams):
    """법령 체계도 본문 조회 API
    """

    target: Literal["lsStmd"] = "lsStmd"
    ID: Optional[str] = Field(None, description="법령 ID")
    MST: Optional[str] = Field(None, description="법령 마스터 번호")
    LM: Optional[str] = Field(None, description="법령명")
    LD: Optional[int] = Field(None, description="법령의 공포일자")
    LN: Optional[int] = Field(None, description="법령의 공포번호")

    @model_validator(mode="after")
    def validate_id_or_mst(
        cls, values: "LawSystemRequestParams"
    ) -> "LawSystemRequestParams":
        """ID 또는 MST 중 하나는 반드시 입력해야 함"""
        if not values.ID and not values.MST:
            raise ValueError("ID 또는 MST 중 하나는 반드시 입력해야 합니다.")
        return values


class AdmRuleRequestParams(BaseRequestParams):
    """행정규칙 본문 조회 API
    """

    target: Literal["admrul"] = "admrul"
    ID: Optional[str] = Field("2100000213205", description="행정규칙 일련번호")
    LID: Optional[str] = Field(None, description="행정규칙 ID")
    LM: Optional[str] = Field(None, description="정확한 행정규칙명")

    @model_validator(mode="after")
    def validate_id_or_mst(
        cls, values: "AdmRuleRequestParams"
    ) -> "AdmRuleRequestParams":
        """ID 또는 LID 중 하나는 반드시 입력해야 함"""
        if not values.ID and not values.LID:
            raise ValueError("ID 또는 LID 중 하나는 반드시 입력해야 합니다.")
        return values


class LicBylRequestParams(BaseRequestParams):
    """법령 별표 서식 목록 조회
    """

    target: Literal["licbyl"] = "licbyl"
    search: Optional[int] = Field(
        2,
        description="검색범위 (기본: 1 별표서식명, 2:해당법령검색, 3:별표본문검색)",
        ge=1,
        le=3,
    )
    query: Optional[str] = Field("*", description="검색을 원하는 질의 (default=*)")
    display: Optional[int] = Field(
        20, description="검색된 결과 개수 (default=20 max=100)", le=100
    )
    page: Optional[int] = Field(1, description="검색 결과 페이지 (default=1)")
    sort: Literal["lasc", "ldes"] = Field(
        "lasc",
        description="정렬옵션 (기본: lasc 별표서식명 오름차순, ldes 별표서식명 내림차순)",
    )
    org: Optional[str] = Field(None, description="소관부처별 검색(소관부처코드 제공)")
    knd: Optional[str] = Field(
        None, description="별표종류 (1: 별표, 2: 서식, 3: 별지, 4: 별도, 5: 부록)"
    )
    gana: Optional[str] = Field(None, description="사전식 검색 (ga, na, da 등)")
    popYn: Optional[str] = Field(
        None, description="상세화면 팝업창 여부 (Y일 경우 팝업창으로 표시)"
    )
    mulOrg: Literal["OR", "AND"] = Field(
        "OR", description="소관부처 2개 이상 검색 조건 (default: OR)"
    )


class AdmBylRequestParams(BaseRequestParams):
    """행정규칙 별표 서식 목록 조회
    """

    target: Literal["admbyl"] = "admbyl"
    knd: Optional[str] = Field(None, description="별표종류 (1: 별표, 2: 서식, 3: 별지)")
    search: Optional[int] = Field(
        2,
        description="검색범위 (기본: 1 별표서식명, 2:해당법령검색, 3:별표본문검색)",
        ge=1,
        le=3,
    )
    query: Optional[str] = Field("*", description="검색을 원하는 질의 (default=*)")
    display: Optional[int] = Field(
        20, description="검색된 결과 개수 (default=20 max=100)", le=100
    )
    page: Optional[int] = Field(1, description="검색 결과 페이지 (default=1)")
    sort: Literal["lasc", "ldes"] = Field(
        "lasc",
        description="정렬옵션 (기본: lasc 별표서식명 오름차순, ldes 별표서식명 내림차순)",
    )
    org: Optional[str] = Field(None, description="소관부처별 검색(소관부처코드 제공)")
    gana: Optional[str] = Field(None, description="사전식 검색 (ga, na, da 등)")
    popYn: Optional[str] = Field(
        None, description="상세화면 팝업창 여부 (Y일 경우 팝업창으로 표시)"
    )
