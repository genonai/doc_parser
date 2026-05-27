from .base_enricher import BaseEnricher
from .toc_enricher import TOCEnricher
from .metadata_enricher import MetadataEnricher
from .image_description_enricher import ImageDescriptionEnricher
from .custom_fields_enricher import CustomFieldsEnricher

ENRICHERS = {
    "toc": TOCEnricher,
    "toc_enricher": TOCEnricher,
    "extract_metadata": MetadataEnricher,
    "metadata_enricher": MetadataEnricher,
    "image_description": ImageDescriptionEnricher,
    "image_description_enricher": ImageDescriptionEnricher,
    "custom_fields": CustomFieldsEnricher,
    "custom_fields_enricher": CustomFieldsEnricher,
}

__all__ = ["BaseEnricher", "TOCEnricher", "MetadataEnricher", "ImageDescriptionEnricher", "CustomFieldsEnricher", "ENRICHERS"]
