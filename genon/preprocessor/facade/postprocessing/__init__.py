from .table_refiner import TableRefiner
from .subject_extractor import SubjectExtractor
from .enhanced_image_description import EnhancedImageDescriptionPostprocessor

POSTPROCESSORS = {
    "table_refiner": TableRefiner,
    "subject_extractor": SubjectExtractor,
    "enhanced_image_description": EnhancedImageDescriptionPostprocessor,
}

__all__ = ["TableRefiner", "SubjectExtractor", "EnhancedImageDescriptionPostprocessor", "POSTPROCESSORS"]
