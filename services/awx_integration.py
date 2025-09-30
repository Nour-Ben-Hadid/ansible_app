import requests
import os
import json
from dotenv import load_dotenv
import logging

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AWX_URL = os.getenv("AWX_URL")
AWX_TOKEN = os.getenv("AWX_TOKEN")
GIT_REPO = os.getenv("GIT_REPO")

# Variables Azure du .env pour les job templates
AZURE_SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID")


def get_azure_credential():
    """Récupère le credential Azure existant dans AWX (créé manuellement)"""

    headers = {
        "Authorization": f"Bearer {AWX_TOKEN}",
        "Content-Type": "application/json"
    }

    credential_name = "azure-service-principal"

    try:
        # Récupérer le credential existant
        response = requests.get(
            f"{AWX_URL}/api/v2/credentials/?name={credential_name}",
            headers=headers
        )

        if response.status_code == 200:
            credentials = response.json()
            if credentials["count"] > 0:
                logger.info(
                    f" Credential Azure trouvé: ID {credentials['results'][0]['id']}")
                return credentials["results"][0]["id"]
            else:
                logger.error(
                    f" Credential '{credential_name}' non trouvé. Veuillez le créer manuellement dans AWX.")
                return None
        else:
            logger.error(
                f" Erreur lors de la récupération du credential: {response.status_code}")
            return None

    except Exception as e:
        logger.error(
            f" Exception lors de la récupération du credential: {str(e)}")
        return None


def create_or_get_project(app_name):
    """Crée ou récupère le projet AWX spécifique pour cette application"""

    headers = {
        "Authorization": f"Bearer {AWX_TOKEN}",
        "Content-Type": "application/json"
    }

    # Nom de projet unique par application
    project_name = f"project-{app_name}"

    try:
        # Vérifier si le projet existe déjà
        response = requests.get(
            f"{AWX_URL}/api/v2/projects/?name={project_name}",
            headers=headers
        )

        if response.status_code == 200:
            projects = response.json()
            if projects["count"] > 0:
                logger.info(
                    f" Projet existant trouvé pour {app_name}: ID {projects['results'][0]['id']}")
                return projects["results"][0]["id"]

        # Créer un nouveau projet spécifique à cette application
        logger.info(f" Création d'un nouveau projet AWX pour {app_name}...")
        project_data = {
            "name": project_name,
            "description": f"Projet automatique pour l'application {app_name}",
            "scm_type": "git",
            "scm_url": f"https://{GIT_REPO}",
            "scm_branch": "main",
            "scm_update_on_launch": True
        }

        response = requests.post(
            f"{AWX_URL}/api/v2/projects/",
            headers=headers,
            json=project_data
        )

        if response.status_code == 201:
            project = response.json()
            logger.info(f" Projet créé avec succès! ID: {project['id']}")

            # Synchroniser le projet pour récupérer les playbooks
            sync_response = requests.post(
                f"{AWX_URL}/api/v2/projects/{project['id']}/update/",
                headers=headers
            )

            if sync_response.status_code == 202:
                logger.info(f" Synchronisation du projet lancée...")
                # Attendre la synchronisation avec vérification du statut
                import time
                for i in range(10):  # Attendre jusqu'à 10 secondes
                    time.sleep(1)
                    status_response = requests.get(
                        f"{AWX_URL}/api/v2/projects/{project['id']}/",
                        headers=headers
                    )
                    if status_response.status_code == 200:
                        project_status = status_response.json()
                        if project_status.get("status") == "successful":
                            logger.info(
                                f" Synchronisation terminée avec succès!")
                            break
                        elif project_status.get("status") == "failed":
                            logger.error(f" Synchronisation échouée!")
                            break
                    logger.info(f" Synchronisation en cours... ({i+1}/10)")
                else:
                    logger.warning(
                        f" Synchronisation timeout après 10 secondes")
            else:
                logger.warning(
                    f" Synchronisation échouée: {sync_response.status_code}")

            return project["id"]
        else:
            logger.error(
                f" Erreur création projet: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        logger.error(f" Exception lors de la création du projet: {str(e)}")
        return None


def create_job_template(app_name, playbook_filename):
    """Crée un job template dans AWX pour le playbook généré avec variables Azure"""

    logger.info(f" Tentative de création du job template AWX pour {app_name}")
    logger.info(f"AWX URL: {AWX_URL}")
    logger.info(f"Playbook: {playbook_filename}")

    # D'abord créer/récupérer le projet spécifique à cette application
    project_id = create_or_get_project(app_name)
    if not project_id:
        return {
            "success": False,
            "error": f"Impossible de créer ou récupérer le projet AWX pour {app_name}"
        }

    headers = {
        "Authorization": f"Bearer {AWX_TOKEN}",
        "Content-Type": "application/json"
    }

    # Variables Azure à injecter dans le job template
    azure_extra_vars = {
        "azure_client_id": AZURE_CLIENT_ID,
        "azure_secret": AZURE_CLIENT_SECRET,
        "azure_tenant": AZURE_TENANT_ID,
        "azure_subscription_id": AZURE_SUBSCRIPTION_ID
    }

    # Données du job template avec variables Azure
    job_template_data = {
        "name": f"Deploy-{app_name}",
        "description": f"Deployment job for {app_name}",
        "playbook": playbook_filename,
        "project": project_id,
        "inventory": 1,
        "job_type": "run",
        "verbosity": 1,
        "extra_vars": json.dumps(azure_extra_vars)  # Convertir en JSON string
    }

    try:
        logger.info(f" Envoi de la requête vers AWX...")
        # Créer le job template
        response = requests.post(
            f"{AWX_URL}/api/v2/job_templates/",
            headers=headers,
            json=job_template_data,
            timeout=10
        )

        logger.info(f" Réponse AWX: Status {response.status_code}")
        logger.info(f" Contenu réponse: {response.text}")

        if response.status_code == 201:
            job_template = response.json()
            job_template_id = job_template['id']
            logger.info(
                f" Job template créé avec succès! ID: {job_template_id}")

            return {
                "success": True,
                "job_template_id": job_template_id,
                "job_template_name": job_template["name"],
                "message": f"Job template créé avec succès avec variables Azure"
            }
        else:
            logger.error(
                f" Erreur AWX: {response.status_code} - {response.text}")
            return {
                "success": False,
                "error": f"Erreur AWX: {response.status_code} - {response.text}"
            }

    except Exception as e:
        logger.error(f" Exception lors de la connexion AWX: {str(e)}")
        return {
            "success": False,
            "error": f"Erreur de connexion AWX: {str(e)}"
        }


def launch_job(job_template_id):
    """Lance un job à partir du job template"""

    headers = {
        "Authorization": f"Bearer {AWX_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        logger.info(f" Lancement du job template ID: {job_template_id}")
        response = requests.post(
            f"{AWX_URL}/api/v2/job_templates/{job_template_id}/launch/",
            headers=headers
        )

        logger.info(f" Réponse AWX Launch: Status {response.status_code}")
        logger.info(f" Contenu réponse: {response.text}")

        if response.status_code == 201:
            job = response.json()
            logger.info(f" Job lancé avec succès! ID: {job['id']}")
            return {
                "success": True,
                "job_id": job["id"],
                "status": job.get("status", "pending"),
                "job_name": job.get("name", "Unknown"),
                "url": job.get("url", ""),
                "message": "Job lancé avec succès"
            }
        else:
            logger.error(
                f" Erreur lors du lancement: {response.status_code} - {response.text}")
            return {
                "success": False,
                "error": f"Erreur lors du lancement: {response.status_code} - {response.text}"
            }

    except Exception as e:
        logger.error(f" Exception lors du lancement: {str(e)}")
        return {
            "success": False,
            "error": f"Erreur de connexion: {str(e)}"
        }
