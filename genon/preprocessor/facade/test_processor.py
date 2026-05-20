import yaml

from genon.preprocessor.facade.base_processor import BaseProcessor

config = yaml.safe_load(("/app/resource/config.yaml").read_text(encoding="utf-8"))


class DocumentProcessor(BaseProcessor):
    def __init__(self, config=config):
        super().__init__(config)
