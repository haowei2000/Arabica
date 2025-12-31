from pydantic import BaseModel, TypeAdapter


class SingleDimension(BaseModel):
    """
    Represents a single dimension_registry used in NL2SQL schema.

    Attributes:
        dimension_name (str): Human-readable name of the dimension_registry.
        dimension_code (str): Machine-friendly identifier for the dimension_registry.
        allowed_values (list[str]): Permitted values for this dimension_registry.
    """
    dimension_name: str
    dimension_code: str
    dimension_gid: str
    allowed_values: list[str]|None = None



SubDimensionResponseSchema = TypeAdapter(list[SingleDimension])
