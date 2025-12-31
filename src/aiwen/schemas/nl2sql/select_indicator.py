from pydantic import BaseModel


class IndicatorSelectResponseSchema(BaseModel):
    indicator_name: str|None = None


class SelectIndicatorRequest(BaseModel):
    query:str

class SelectSubIndicatorRequest(BaseModel):
    indicator_name: str | None = None
    indicator_gid: str | None = None
