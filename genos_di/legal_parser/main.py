
import logging

from fastapi import Depends, FastAPI

from params import AdmRuleRequestParams, LawItemRequestParams
from service import download_data, get_parse_result

logging.basicConfig(level=logging.INFO,
                    format='[%(name)s]-%(levelname)s: %(message)s')
  
logger = logging.getLogger(__name__)


app = FastAPI()

@app.get("/")
async def read_root():
    return "Hello, Parser for Legal Data"

# TODO 하루에 한 번 돌아가게 만들기

@app.get("/metadata-example")
async def parser_metadata() -> int:
    law_ids_dict = {
        "law_ids": ["224537", "264627", "268423"],
        "admrule_ids": ["2100000213205"]
    }
    return await get_parse_result(law_ids_dict)


### OPEN API 법령 정보 원본 (JSON) 다운로드
@app.get("/law-download")
async def download_law(query: LawItemRequestParams = Depends()):
    await download_data(query)
    

@app.get("/admrule-download")
async def download_admrule(query: AdmRuleRequestParams = Depends()):
    await download_data(query)
