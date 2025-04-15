from typing import Optional

from fastapi import Depends, FastAPI, Query

from params import AdmRuleRequestParams, APIEndpoints, LawItemRequestParams
from schemas import ParserResult
from service import fetch_api, get_parse_result
import logging

logging.basicConfig(level=logging.INFO,
                    format='[%(name)s]-%(levelname)s: %(message)s')
  
logger = logging.getLogger(__name__)


app = FastAPI()

@app.get("/")
async def read_root():
    return "Hello, Parser for Legal Data"

###  Data Parsing

# TODO 법령 \ 행정규칙으로 엑셀 파일 가져와서 ID("법률일련번호"), LID(행정규칙일련번호) 추출
# TODO 하루에 한 번 돌아가게 만들기

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
