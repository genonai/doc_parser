class BaseError(RuntimeError):
    pass


class ConversionError(BaseError):
    pass


class OperationNotAllowed(BaseError):
    pass

# CUSTOM EXCEPTIONS
class HwpConversionError(ConversionError):
    """Custom exception for HWP conversion errors."""
    pass


