"""Pydantic request/response models. Each one mirrors a schema in openapi.yaml.

These are NOT the source of truth: openapi.yaml is. If they disagree, fix this file.
Response models make FastAPI check our own output before sending it, so a bug that would
break the contract becomes a 500 in tests instead of a silent surprise for clients.
"""

from typing import Literal

from pydantic import BaseModel


class Health(BaseModel):
    """openapi.yaml: #/components/schemas/Health"""

    status: Literal["ok"]
    database: Literal["ok"]
