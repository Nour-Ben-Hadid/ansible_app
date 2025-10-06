import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from services.deployment_store import create_deployment, get_deployment, list_deployments
from services.playbook_generator import REGIONS_WEB_APP, generer_playbook


router = APIRouter()


class FormulaireParams(BaseModel):
    nom_app: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{1,39}$")
    resource_group: str = Field(min_length=1, max_length=90)
    app_repo_url: str = Field(min_length=1, max_length=250)
    app_repo_branch: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    location: str
    app_type: Literal["web_app"] = "web_app"
    app_language: Literal["nodejs", "python"]
    python_version: Literal["3.11", "3.10", "3.9", "3.8"] = "3.11"
    startup_file: str | None = Field(default=None, max_length=120)

    @field_validator("resource_group")
    @classmethod
    def validate_resource_group(cls, value: str) -> str:
        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-()")
        if any(char not in allowed_chars for char in value) or value.endswith("."):
            raise ValueError("Resource group name contains unsupported characters.")
        return value

    @field_validator("app_repo_url")
    @classmethod
    def validate_repo_url(cls, value: str) -> str:
        if not value.startswith("https://github.com/"):
            raise ValueError("Repository URL must use https://github.com/OWNER/REPO.git format.")

        path = value.removeprefix("https://github.com/")
        parts = path.removesuffix(".git").split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("Repository URL must include an owner and repository name.")
        return value

    @field_validator("app_repo_branch")
    @classmethod
    def validate_branch(cls, value: str) -> str:
        if value.startswith(("-", "/", ".")) or value.endswith(("/", ".")):
            raise ValueError("Branch name has an invalid start or end.")
        if ".." in value or any(char in value for char in " ~^:?*[\\"):
            raise ValueError("Branch name contains unsupported characters.")
        return value

    @field_validator("location")
    @classmethod
    def validate_location(cls, value: str) -> str:
        if value not in set(REGIONS_WEB_APP.values()):
            raise ValueError("Unsupported Azure region.")
        return value

    @field_validator("startup_file")
    @classmethod
    def validate_startup_file(cls, value: str | None) -> str | None:
        if value is None:
            return value

        clean_value = value.strip()
        if not clean_value:
            return None
        if clean_value.startswith(("/", "\\")) or ".." in clean_value:
            raise ValueError("Startup file cannot be absolute or contain path traversal.")

        allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./:-")
        if any(char not in allowed_chars for char in clean_value):
            raise ValueError("Startup file contains unsupported characters.")
        return clean_value


@router.get("/api/formulaires")
def lister_formulaires():
    try:
        return {"formulaires": list_deployments()}
    except Exception as exc:
        return {"error": str(exc), "formulaires": []}


def create_deployment_response(params: FormulaireParams):
    formulaire_id = str(uuid.uuid4())

    create_deployment(formulaire_id, params.model_dump())
    playbook_result = generer_playbook(formulaire_id)

    job_template_id = None
    if playbook_result.get("awx_integration", {}).get("success"):
        job_template_id = playbook_result["awx_integration"]["job_template_id"]

    return {
        "message": "Formulaire enregistre avec succes.",
        "formulaire_id": formulaire_id,
        "playbook_result": playbook_result,
        "job_template_id": job_template_id,
        "can_deploy": job_template_id is not None,
    }


@router.post("/api/formulaire", status_code=status.HTTP_201_CREATED)
def creer_formulaire(params: FormulaireParams):
    return create_deployment_response(params)


@router.get("/api/deployments")
def list_api_deployments():
    return {"deployments": list_deployments()}


@router.get("/api/deployments/{deployment_id}")
def get_api_deployment(deployment_id: str):
    deployment = get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")
    return deployment


@router.post("/api/deployments", status_code=status.HTTP_201_CREATED)
def create_api_deployment(params: FormulaireParams):
    return create_deployment_response(params)
