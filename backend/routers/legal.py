"""Public legal disclosures only; never return the settings object."""
from fastapi import APIRouter, Response
from core.config import settings

router = APIRouter(prefix='/legal', tags=['legal'])


@router.get('/config')
async def legal_config(response: Response) -> dict[str, str | bool]:
    response.headers['Cache-Control'] = 'no-store'
    disclosures = {key: getattr(settings, 'legal_' + key) for key in (
        'operator_name', 'operator_address', 'contact_email',
        'hosting_details', 'retention_details', 'transfer_details',
    )}
    # Older installations can keep using their general contact address.
    disclosures['privacy_email'] = settings.legal_privacy_email.strip() or disclosures['contact_email']
    enabled = settings.legal_pages_enabled and all(value.strip() for value in disclosures.values())
    # Disabled deployments must not expose operator details through the API either.
    return {'enabled': enabled, **{key: value if enabled else '' for key, value in disclosures.items()}}
