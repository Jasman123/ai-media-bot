"""Signed, expiring media URLs for the local-disk storage adapter (providers fetch user photos from here)."""

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Response

from ..domain.ports import SignedMediaReader

router = APIRouter(route_class=DishkaRoute)


@router.get("/media/{key:path}")
async def media(key: str, exp: int, sig: str, reader: FromDishka[SignedMediaReader]):
    found = await reader.read_signed(key, exp, sig)
    if found is None:
        raise HTTPException(status_code=404)
    data, mime = found
    return Response(content=data, media_type=mime, headers={"Cache-Control": "private, max-age=300"})
