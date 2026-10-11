from pydantic import BaseModel


class CharacterImage(BaseModel):
    url: str
    provider: str


class CharacterMedia(BaseModel):
    id: int
    name: str
    kind: str
    images: list[CharacterImage]


class ScreenCharacterDetail(BaseModel):
    id: int
    name: str
    actor_id: int
    actor_name: str
    aliases: list[str]
    media: list[CharacterMedia]


class ScreenCharacterList(BaseModel):
    page: int
    limit: int
    results: list[ScreenCharacterDetail]
