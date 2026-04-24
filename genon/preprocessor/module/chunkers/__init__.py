from .GenosBucketChunker import GenosBucketChunker
from .GenosCharacterBucketChunker import GenosCharacterBucketChunker
from .SimpleChunker import SimpleChunker

CHUNKERS = {
    "bucket": GenosBucketChunker,
    "char_bucket": GenosCharacterBucketChunker,
    "simple": SimpleChunker,
}

__all__ = ["GenosBucketChunker", "GenosCharacterBucketChunker", "SimpleChunker", "CHUNKERS"]
