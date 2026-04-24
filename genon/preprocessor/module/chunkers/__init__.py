from .GenosBucketChunker import GenosBucketChunker
from .SimpleChunker import SimpleChunker

CHUNKERS = {
    "bucket": GenosBucketChunker,
    "simple": SimpleChunker,
}

__all__ = ["GenosBucketChunker", "SimpleChunker", "CHUNKERS"]
