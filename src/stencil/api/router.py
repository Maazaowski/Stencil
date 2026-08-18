"""Main API router — mounts all sub-routers under /api/v1."""

from fastapi import APIRouter

from stencil.api.accounts import router as accounts_router
from stencil.api.ai_debug import router as ai_debug_router
from stencil.api.auth import router as auth_router
from stencil.api.dashboard import router as dashboard_router
from stencil.api.evals import router as evals_router
from stencil.api.exceptions import router as exceptions_router
from stencil.api.field_schemas import router as field_schemas_router
from stencil.api.intakes import router as intakes_router
from stencil.api.invoices import router as invoices_router
from stencil.api.models import router as models_router
from stencil.api.output_specs import router as output_specs_router
from stencil.api.pipeline import router as pipeline_router
from stencil.api.profile_authoring import router as profile_authoring_router
from stencil.api.profiles import router as profiles_router
from stencil.api.settings import router as settings_router
from stencil.api.users import router as users_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(auth_router)
api_router.include_router(invoices_router)
api_router.include_router(intakes_router)
# Mount the authoring sub-router before the generic profiles router so its
# /profiles/authoring/* paths are never shadowed by /profiles/{profile_id}.
api_router.include_router(profile_authoring_router)
api_router.include_router(profiles_router)
api_router.include_router(accounts_router)
api_router.include_router(field_schemas_router)
api_router.include_router(output_specs_router)
api_router.include_router(models_router)
api_router.include_router(exceptions_router)
api_router.include_router(dashboard_router)
api_router.include_router(pipeline_router)
api_router.include_router(settings_router)
api_router.include_router(ai_debug_router)
api_router.include_router(evals_router)
api_router.include_router(users_router)
