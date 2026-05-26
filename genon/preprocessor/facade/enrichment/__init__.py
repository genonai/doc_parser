from .base_enricher import BaseEnricher
from .toc_enricher import TOCEnricher
from .image_description_enricher import ImageDescriptionEnricher
from .custom_fields_enricher import CustomFieldsEnricher

ENRICHERS = {
    "toc": TOCEnricher,
    "toc_enricher": TOCEnricher,
    "image_description": ImageDescriptionEnricher,
    "image_description_enricher": ImageDescriptionEnricher,
    "custom_fields": CustomFieldsEnricher,
    "custom_fields_enricher": CustomFieldsEnricher,
}

__all__ = ["BaseEnricher", "TOCEnricher", "ImageDescriptionEnricher", "CustomFieldsEnricher", "ENRICHERS"]
