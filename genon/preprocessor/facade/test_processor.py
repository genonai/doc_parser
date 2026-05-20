from pathlib import Path

import yaml

from genon.preprocessor.facade.base_processor import BaseProcessor

_HERE = Path(__file__).parent
config = yaml.safe_load((_HERE / "intelligent_config.yaml").read_text(encoding="utf-8"))


class DocumentProcessor(BaseProcessor):
    def __init__(self, config=config):
        super().__init__(config)
