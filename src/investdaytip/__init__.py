"""InvestDayTip — stock recommendation tool."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from investdaytip.main import get_recommendations as _get_recs

    get_recommendations = _get_recs
else:

    def get_recommendations(*args, **kwargs):
        from investdaytip.main import get_recommendations as _gr

        return _gr(*args, **kwargs)


__all__ = ["get_recommendations"]
__version__ = "0.9.0"
