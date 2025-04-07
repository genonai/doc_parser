from typing import Optional

from fastapi import Depends, FastAPI, Query

from params import AdmRuleRequestParams, APIEndpoints, LawItemRequestParams
from schemas import ParserResult
from service import fetch_api, get_parse_result



## FastAPI 서버 설정
app = FastAPI()

@app.get("/")
async def read_root():
    return "Hello, Parser for Legal Data"

###  Data Parsing

# TODO 법령 \ 행정규칙으로 엑셀 파일 가져와서 ID("법률일련번호"), LID(행정규칙일련번호) 추출

## TEST
# {'law': ['267539', '269549', '270237', '149922', '98243'], 'admrule': ['2100000250634', '2100000176403', '2100000248998', '2100000246106', '2100000255826', '2100000005194', '2100000237254', '2100000240092', '2100000242328', '2100000203875', '2100000248804', '2100000250694']}

# KEY = 법률일련번호(MST) / 행정규칙일련번호(ID)
@app.get("/metadata-example")
async def parser_metadata(KEY: Optional[str] = Query("264627")) -> list[ParserResult]:
    return await get_parse_result(KEY)

### 기타
@app.get("/law-download")
async def download_law(query: LawItemRequestParams = Depends()):
    api_url = APIEndpoints().get_full_url(query.get_query_params())
    return await fetch_api(api_url)

@app.get("/admrule-download")
async def download_admrule(query: AdmRuleRequestParams = Depends()):
    api_url = APIEndpoints().get_full_url(query.get_query_params())
    return await fetch_api(api_url)
