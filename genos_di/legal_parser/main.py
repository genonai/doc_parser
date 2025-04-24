from fastapi import Depends, FastAPI, Query

from params import AdmRuleRequestParams, LawItemRequestParams
from schemas import ParserRequest, ParserResponse
from service import download_data, get_parse_result
from utils.file_utils import load_keys_from_csv

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
    request.law_ids_input.law_ids = law_ids_dict.law_ids_input.law_ids[i_start:i_end]
    request.law_ids_input.admrule_ids = law_ids_dict.law_ids_input.admrule_ids[i_start:i_end]

    return await get_parse_result(request)

@app.get("/parse/all")
async def run_parser() -> ParserResponse:
    request = load_keys_from_csv()
    return await get_parse_result(request)

### OPEN API 법령 정보 원본 (JSON) 다운로드
@app.get("/law-download")
async def download_law(query: LawItemRequestParams = Depends()):
    await download_data(query)
    

@app.get("/admrule-download")
async def download_admrule(query: AdmRuleRequestParams = Depends()):
    await download_data(query)
