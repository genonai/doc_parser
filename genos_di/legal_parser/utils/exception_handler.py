from fastapi import status


class ClientError(Exception):
    def __init__(self, id:str, detail: str, status_code: int = status.HTTP_404_NOT_FOUND):
        self.id = id 
        self.detail = detail
        self.status_code = status_code
    
