"""Optional read-only market-data interface for separate research applications."""
import hmac
import os
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from .md import store

router = APIRouter(prefix='/api/research/v1', tags=['market research'])


def require_research(response: Response, authorization: str = Header('')) -> frozenset[UUID]:
    response.headers['Cache-Control'] = 'no-store'
    expected = os.environ.get('COREI_RESEARCH_API_TOKEN', '')
    configured = os.environ.get('COREI_RESEARCH_API_ACCOUNTS', '')
    try:
        accounts = frozenset(UUID(account.strip()) for account in configured.split(',') if account.strip())
    except ValueError:
        accounts = frozenset()
    if len(expected) < 32 or not accounts:
        raise HTTPException(503, 'Market research access is not configured.', headers={'Cache-Control': 'no-store'})
    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not hmac.compare_digest(token.encode(), expected.encode()):
        raise HTTPException(401, 'A valid market research credential is required.',
                            headers={'Cache-Control': 'no-store', 'WWW-Authenticate': 'Bearer'})
    return accounts


@router.get('/capabilities')
def capabilities(accounts: frozenset[UUID] = Depends(require_research)):
    return {'product': 'corei-os-mini', 'contract_version': 1, 'access': 'market-data:read',
            'operations': ['bars'], 'account_ids': sorted(str(account) for account in accounts)}


@router.get('/bars')
def read_bars(account_id: UUID, symbol: str = Query(min_length=1, max_length=128, pattern=r"\S"),
              timeframe: str = Query('H1', pattern='^(S1|M1|M5|M15|M30|H1|H4|D1)$'),
              limit: int = Query(100, ge=1, le=200),
              accounts: frozenset[UUID] = Depends(require_research)):
    if account_id not in accounts:
        raise HTTPException(404, 'Market data is unavailable for this account.', headers={'Cache-Control': 'no-store'})
    return {'source': 'corei-os-mini', 'account_id': account_id, 'symbol': symbol.strip(), 'timeframe': timeframe,
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'items': store.bars(account_id, symbol.strip(), timeframe, limit)}
