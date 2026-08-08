"""Bot de análisis fundamental: fetch -> normalizar -> rankear por sector -> reportar."""

from .models import (
    FetchError,
    FundamentalSnapshot,
    NoDataError,
    UnmappedTickerError,
    UpstreamError,
)

__version__ = "0.1.0"

__all__ = [
    "FundamentalSnapshot",
    "FetchError",
    "NoDataError",
    "UpstreamError",
    "UnmappedTickerError",
    "__version__",
]
