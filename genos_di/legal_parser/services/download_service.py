from typing import Union

from api.law_client import fetch_api
from commons.file_handler import export_json_input
from schemas.params import (
    AdmRuleRequestParams,
    APIEndpoints,
    LawItemRequestParams,
)


async def download_data(query: Union[LawItemRequestParams, AdmRuleRequestParams]):
    api_url = APIEndpoints().get_item_url(query.get_query_params())
    id = query.MST if isinstance(query, LawItemRequestParams) else query.ID
    result =  await fetch_api(api_url)
    export_json_input(result, id)