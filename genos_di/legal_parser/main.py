from fastapi import Depends, FastAPI, Query

from commons.file_handler import load_keys_from_csv
from schemas.params import AdmRuleRequestParams, LawItemRequestParams
from schemas.schema import ParserRequest, ParserResponse, PipelineResponse
from schemas.vdb_schema import VDBResponse
from services.download_service import download_data
from services.law_service import get_parse_result
from services.service import (
    process_all_pipeline,
    process_updated_pipeline,
)
from services.vdb_service import process_law_vectorization

app = FastAPI()

@app.get("/")
async def read_root():
    return "Hello, Parser for Legal Data"

# TODO 하루에 한 번 돌아가게 만들기

@app.get("/test/parser")
async def test_parser(
    i_start:int = Query(default=0),
    i_end:int = Query(default=5)
) -> ParserResponse:
    law_ids_dict = load_keys_from_csv()

    request = ParserRequest()
    request.law_ids = law_ids_dict.law_ids[i_start:i_end]
    request.admrule_ids = law_ids_dict.admrule_ids[i_start:i_end]

    return await get_parse_result(request)

@app.get("/test/vdb")
async def test_vdb(
) -> VDBResponse:
    return await process_law_vectorization()

@app.get("/parse/all")
async def run_parser() -> PipelineResponse:
    request = load_keys_from_csv()
    return await process_all_pipeline(request)

@app.get("/parse/updated")
async def run_updator() -> PipelineResponse:
    return await process_updated_pipeline()
    
### OPEN API 법령 정보 원본 (JSON) 다운로드
@app.get("/law-download")
async def download_law(query: LawItemRequestParams = Depends()):
    await download_data(query)
    

@app.get("/admrule-download")
async def download_admrule(query: AdmRuleRequestParams = Depends()):
    await download_data(query)
