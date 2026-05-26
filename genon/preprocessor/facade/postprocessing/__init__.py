from .table_refiner import TableRefiner

POSTPROCESSORS = {
    "table_refiner": TableRefiner,
}

__all__ = ["TableRefiner", "POSTPROCESSORS"]
