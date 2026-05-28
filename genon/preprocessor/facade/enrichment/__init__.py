from .base_enricher import BaseEnricher
from .toc_enricher import TOCEnricher
from .metadata_enricher import MetadataEnricher
from .image_description_enricher import ImageDescriptionEnricher
from .custom_fields_enricher import CustomFieldsEnricher
from .contextual_image_description_enricher import (
    ContextualImageDescriptionEnricher,
    ImageDescriptionOptions,
    PictureDescriptionExtractor,
)

ENRICHERS = {
    "toc": TOCEnricher,
    "extract_metadata": MetadataEnricher,
    "metadata_enricher": MetadataEnricher,
    "image_description": ImageDescriptionEnricher,
    "custom_fields": CustomFieldsEnricher,
    "contextual_image_description": ContextualImageDescriptionEnricher,
}

__all__ = [
    "BaseEnricher",
    "TOCEnricher",
    "MetadataEnricher",
    "ImageDescriptionEnricher",
    "CustomFieldsEnricher",
    "ContextualImageDescriptionEnricher",
    "ImageDescriptionOptions",
    "PictureDescriptionExtractor",
    "ENRICHERS",
]
