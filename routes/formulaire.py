from fastapi import APIRouter, status
from pydantic import BaseModel
import uuid
import json
import os
from services.playbook_generator import generer_playbook


router = APIRouter()

FICHIER_JSON = "data/formulaires.json"


class FormulaireParams(BaseModel):
    nom_app: str
    resource_group: str
    app_repo_url: str
    app_repo_branch: str
    description: str
    location: str
    app_type: str = "web_app"  # Seulement "web_app" supporté
    app_language: str  # "nodejs" ou "python" - OBLIGATOIRE, aucune valeur par défaut
    python_version: str = "3.11"  # Version Python (pour apps Python)
    startup_file: str = None  # Fichier de démarrage optionnel


def sauvegarder_formulaire(data):
    # Créer le répertoire s'il n'existe pas
    os.makedirs(os.path.dirname(FICHIER_JSON), exist_ok=True)

    # Créer le fichier s'il n'existe pas encore
    if not os.path.exists(FICHIER_JSON):
        with open(FICHIER_JSON, "w") as f:
            json.dump([], f)

    # Charger les configurations existantes
    try:
        with open(FICHIER_JSON, "r") as f:
            content = f.read().strip()
            if content:
                configurations = json.loads(content)
            else:
                configurations = []
    except (json.JSONDecodeError, FileNotFoundError):
        configurations = []

    # Ajouter la nouvelle configuration
    configurations.append(data)

    # Réécrire le fichier complet avec la nouvelle liste
    with open(FICHIER_JSON, "w") as f:
        json.dump(configurations, f, indent=4)


@router.get("/api/formulaires")
def lister_formulaires():
    """Récupérer tous les formulaires enregistrés"""
    try:
        if not os.path.exists(FICHIER_JSON):
            return {"formulaires": []}

        with open(FICHIER_JSON, "r") as f:
            content = f.read().strip()
            if content:
                configurations = json.loads(content)
            else:
                configurations = []

        return {"formulaires": configurations}
    except Exception as e:
        return {"error": str(e), "formulaires": []}


@router.post("/api/formulaire", status_code=status.HTTP_201_CREATED)
def creer_formulaire(params: FormulaireParams):
    formulaire_id = str(uuid.uuid4())

    data_to_store = {
        "id": formulaire_id,
        "params": params.dict()
    }

    # Sauvegarder dans le fichier JSON
    sauvegarder_formulaire(data_to_store)
    playbook_result = generer_playbook(formulaire_id)

    # Extraire le job template ID s'il existe
    job_template_id = None
    if playbook_result.get("awx_integration", {}).get("success"):
        job_template_id = playbook_result["awx_integration"]["job_template_id"]

    return {
        "message": "Formulaire enregistré avec succès.",
        "formulaire_id": formulaire_id,
        "playbook_result": playbook_result,
        "job_template_id": job_template_id,
        "can_deploy": job_template_id is not None
    }
