from .GenosSmartChunker import GenosSmartChunker
from .HierarchicalChunker import HierarchicalChunker
from .HybridChunker import HybridChunker
from .RecursiveCharChunker import RecursiveCharChunker

CHUNKERS = {
    "smart": GenosSmartChunker,
    "hierarchical": HierarchicalChunker,
    "hybrid": HybridChunker,
    "recursive": RecursiveCharChunker,
}

__all__ = ["GenosSmartChunker", "HierarchicalChunker", "HybridChunker", "RecursiveCharChunker"]
