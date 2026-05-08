from .GenosSmartChunker import GenosSmartChunker
from .HierarchicalChunker import HierarchicalChunker
from .HybridChunker import HybridChunker

CHUNKERS = {
    "smart": GenosSmartChunker,
    "hierarchical": HierarchicalChunker,
    "hybrid": HybridChunker,
}

__all__ = ["GenosSmartChunker", "HierarchicalChunker", "HybridChunker"]
