from .schema import SemanticSource, PrecursorAvailable

# Frozen boundary table (spec §4.3). Edit HERE to move a boundary.
def classify(semantic_source, precursor_available, engineering_required: bool = False) -> str:
    if engineering_required:
        return "D1"
    if semantic_source == SemanticSource.DENOVO:
        return "A1"
    if semantic_source == SemanticSource.NON_NATIVE:
        return "C1"
    return {
        PrecursorAvailable.AVAILABLE_IN_MEDIUM: "B1",
        PrecursorAvailable.AVAILABLE_BY_SIMPLE_FEEDING: "B2",
        PrecursorAvailable.REQUIRES_CUSTOM_SYNTHESIS: "B3",
        PrecursorAvailable.UNKNOWN: "B4",
    }[precursor_available]

_CLASS_DELIVERABLE = {"A1": 1, "B1": 1, "B2": 1, "B3": 0, "B4": 0, "C1": 0, "D1": 0}

def deliverable_now(semantic_source, precursor_available, engineering_required: bool = False) -> int:
    """Pre-registered L2 rubric (frozen). Boundary == _CLASS_DELIVERABLE + classify()."""
    return _CLASS_DELIVERABLE[classify(semantic_source, precursor_available, engineering_required)]
