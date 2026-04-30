from .GenosBucketChunker import GenosBucketChunker
from .HierarchicalChunker import HierarchicalChunker
from .HybridChunker import HybridChunker

CHUNKERS = {
    "bucket": GenosBucketChunker,
    "hierarchical": HierarchicalChunker,
    "hybrid": HybridChunker,
}

__all__ = ["GenosBucketChunker", "HierarchicalChunker", "HybridChunker"]
