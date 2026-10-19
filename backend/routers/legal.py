"""Public legal disclosures only; never return the settings object."""
from fastapi import APIRouter, Response
from core.config import settings

router = APIRouter(prefix='/legal', tags=['legal'])


@router.get('/config')
async def legal_config(response: Response) -> dict[str, str]:
    response.headers['Cache-Control'] = 'no-store'
    return {key: getattr(settings, 'legal_' + key) for key in (
        'operator_name', 'operator_address', 'contact_email',
        'hosting_details', 'retention_details', 'transfer_details',
    )}
