"""JSON serialization for Decimal financial fields (Step 37).

Domain contracts carry money/prices/volumes as ``Decimal`` so arithmetic is
exact. Pydantic's default JSON rendering for ``Decimal`` is a **string**
(``"10000.25"``), which would change every API response's shape. This module
defines the one serializer used by the API response models so ``Decimal``
fields keep rendering as JSON **numbers** — byte-compatible with the
pre-Step-37 wire format — while the domain keeps exact arithmetic internally.

A note on the remaining lossiness: JSON numbers cannot represent ``0.1``
exactly either, so the *transport* has always been approximate. What Step 37
removes is inexactness *inside* the application: aggregation, comparison and
risk logic now operate on exact values, and only the final display edge rounds.
"""
from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer

# The annotated type for every Decimal financial field on API response models.
# Annotated is resolved at import time (not via TypeAlias indirection) so Pydantic
# applies the serializer in every model that uses it.
DecimalAsNumber = Annotated[Decimal, PlainSerializer(lambda value: float(value), return_type=float)]
