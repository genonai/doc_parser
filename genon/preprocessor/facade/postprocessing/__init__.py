from .table_refiner import TableRefiner
from .enhanced_image_description import EnhancedImageDescriptionPostprocessor

POSTPROCESSORS = {
    "table_refiner": TableRefiner,
    "enhanced_image_description": EnhancedImageDescriptionPostprocessor,
}

__all__ = ["TableRefiner", "EnhancedImageDescriptionPostprocessor", "POSTPROCESSORS"]
