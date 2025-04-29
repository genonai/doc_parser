from fastapi import Depends, FastAPI, Query

from commons.file_handler import load_keys_from_csv
from schemas.params import AdmRuleRequestParams, LawItemRequestParams
from schemas.schema import ParserRequest, ParserResponse
from services.download_service import download_data
from services.service import get_amend_result, get_parse_result

app = FastAPI()

@app.get("/")
async def read_root():
    return "Hello, Parser for Legal Data"

# TODO 하루에 한 번 돌아가게 만들기

@app.get("/test")
async def test_parser(
    i_start:int = Query(default=0),
    i_end:int = Query(default=5)
) -> ParserResponse:
    law_ids_dict = load_keys_from_csv()

    request = ParserRequest()
    request.law_ids = law_ids_dict.law_ids[i_start:i_end]
    request.admrule_ids = law_ids_dict.admrule_ids[i_start:i_end]

    return await get_parse_result(request)

@app.get("/parse/all")
async def run_parser() -> ParserResponse:
    request = load_keys_from_csv()
    return await get_parse_result(request)

@app.get("/parse/updated")
async def run_updator() -> ParserResponse:
    return await get_amend_result()

### OPEN API 법령 정보 원본 (JSON) 다운로드
@app.get("/law-download")
async def download_law(query: LawItemRequestParams = Depends()):
    await download_data(query)
    

@app.get("/admrule-download")
async def download_admrule(query: AdmRuleRequestParams = Depends()):
    await download_data(query)
