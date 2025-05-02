from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    oc : str = Field(..., alias="OC")
    genos_admin_token: str = Field(..., alias="GENOS_ADMIN_TOKEN")
    genos_admin_user_id: int = Field(..., alias="GENOS_ADMIN_USER_ID")
    
    genos_test_vdb_index: str = Field(..., alias="GENOS_TEST_VDB_INDEX")
    genos_test_vdb_id: str = Field(..., alias="GENOS_TEST_VDB_ID")
    genos_test_serving_id: int = Field(..., alias="GENOS_TEST_SERVING_ID")
    genos_test_serving_rev_id: int = Field(..., alias="GENOS_TEST_SERVING_REV_ID")
    genos_test_preprocessor_id: int = Field(..., alias="GENOS_TEST_PREPROCESSOR_ID")

    admin_api_host: str = Field(..., alias="ADMIN_API_HOST")
    admin_api_port: int = Field(..., alias="ADMIN_API_PORT")
    admin_api_root_path: str = Field(..., alias="ADMIN_API_ROOT_PATH")

    llmops_weaviate_host: str = Field(..., alias="LLMOPS_WEAVIATE_HOST")
    llmops_weaviate_port: str = Field(..., alias="LLMOPS_WEAVIATE_PORT")
    llmops_weaviate_replicas: str = Field(..., alias="LLMOPS_WEAVIATE_REPLICAS")
    
    class Config:
        env_file = ".env"
        case_sensitive = False   

settings = Settings()