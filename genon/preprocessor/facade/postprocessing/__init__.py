from .table_refiner import TableRefiner
from .subject_extractor import SubjectExtractor
from .enhanced_image_description import EnhancedImageDescriptionPostprocessor
from .table_description import TableDescriptionPostprocessor

POSTPROCESSORS = {
    "table_refiner": TableRefiner,
    "subject_extractor": SubjectExtractor,
    "enhanced_image_description": EnhancedImageDescriptionPostprocessor,
    "table_description": TableDescriptionPostprocessor,
}

__all__ = [
    "TableRefiner",
    "SubjectExtractor",
    "EnhancedImageDescriptionPostprocessor",
    "TableDescriptionPostprocessor",
    "POSTPROCESSORS",
]
