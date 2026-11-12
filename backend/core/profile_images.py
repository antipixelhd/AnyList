"""Permanent uploaded profile images, distinct from evictable provider artwork."""
import asyncio
import io
import uuid
from PIL import Image,ImageOps,UnidentifiedImageError
from fastapi import HTTPException
from core.config import settings

LIMITS={'avatar':3*1024*1024,'background':6*1024*1024}
DIMENSIONS={'avatar':(400,400),'background':(1700,330)}
FORMATS={'JPEG','PNG','WEBP'}
_transforms=asyncio.Semaphore(2)


def normalize(content,kind,crop):
    try:
        with Image.open(io.BytesIO(content)) as original:
            if original.format not in FORMATS or getattr(original,'is_animated',False):
                raise ValueError('Use a still JPEG, PNG or WebP image.')
            if original.width*original.height>25_000_000:raise ValueError('Image is too large. Maximum resolution is 25 megapixels.')
            image=ImageOps.exif_transpose(original)
            width,height=image.size
            target=DIMENSIONS[kind];aspect=target[0]/target[1]
            crop_width=min(width,height*aspect)/crop.zoom;crop_height=crop_width/aspect
            left=(width-crop_width)*crop.x/100;top=(height-crop_height)*crop.y/100
            image=image.convert('RGBA' if 'A' in image.getbands() or image.info.get('transparency') is not None else 'RGB')
            image=image.resize(target,Image.Resampling.LANCZOS,box=(left,top,left+crop_width,top+crop_height))
            output=io.BytesIO();image.save(output,'WEBP',quality=85,method=4)
            return output.getvalue()
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as error:
        raise ValueError('This file is not a supported image.') from error


async def read_upload(file,kind,crop):
    content=await file.read(LIMITS[kind]+1)
    if len(content)>LIMITS[kind]:raise HTTPException(413,f'Maximum upload size is {LIMITS[kind]//1024//1024} MB.')
    try:
        async with _transforms:return await asyncio.to_thread(normalize,content,kind,crop)
    except ValueError as error:raise HTTPException(400,str(error)) from None


def filename(user_id,kind):
    return f'{user_id}.webp' if kind=='avatar' else f'{user_id}.banner.webp'


async def store(user_id,kind,content):
    def save():
        folder=settings.data_dir/'avatars';folder.mkdir(parents=True,exist_ok=True)
        target=folder/filename(user_id,kind);temporary=folder/(uuid.uuid4().hex+'.tmp')
        try:temporary.write_bytes(content);temporary.replace(target)
        finally:temporary.unlink(missing_ok=True)
    await asyncio.to_thread(save)


def background_route(user_id):return f'/api/proxy/profile/background/{user_id}'


async def background_url(db,profile):
    if not profile or not profile.background_url:return None
    value=profile.background_url
    if value==background_route(profile.user_id):return value
    from core import settings_store
    from core.image_cache import parse_image_url
    size,path=parse_image_url(value)
    if not size or not path:return value
    config=await settings_store.get_global_settings(db)
    return background_route(profile.user_id) if config and config.image_cache_enabled and size and path else value
