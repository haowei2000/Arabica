from pydantic import BaseModel, TypeAdapter


class SingleIndicator(BaseModel):
    indicator_name: str
    indicator_gid: str

SubIndicatorSelectResponseSchema = TypeAdapter(list[SingleIndicator])
