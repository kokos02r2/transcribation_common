from fastapi import APIRouter

from app.api.v1.endpoints import (billing_router, pages_router, token_generator,
                                  transcribation_router, users_router)

main_router = APIRouter()
main_router.include_router(token_generator, prefix='', tags=['Token generator'])
main_router.include_router(transcribation_router, prefix='', tags=['Transcribation'])
main_router.include_router(billing_router, prefix='', tags=['Billing'])
main_router.include_router(pages_router, prefix='', tags=['Pages'])
main_router.include_router(users_router)
