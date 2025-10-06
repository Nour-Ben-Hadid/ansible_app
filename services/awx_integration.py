import logging
import os
import time

import requests
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AWX_URL = os.getenv("AWX_URL")
AWX_TOKEN = os.getenv("AWX_TOKEN")
GIT_REPO = os.getenv("GIT_REPO")
GIT_BRANCH = os.getenv("GIT_BRANCH", "main")
AWX_PROJECT_NAME = os.getenv("AWX_PROJECT_NAME", "azure-generated-playbooks")
AWX_AZURE_CREDENTIAL_NAME = os.getenv("AWX_AZURE_CREDENTIAL_NAME", "azure-service-principal")
AWX_SCM_CREDENTIAL_NAME = os.getenv("AWX_SCM_CREDENTIAL_NAME")
AWX_INVENTORY_ID = int(os.getenv("AWX_INVENTORY_ID", "1"))
AWX_REQUEST_TIMEOUT = int(os.getenv("AWX_REQUEST_TIMEOUT", "30"))
AWX_SYNC_ATTEMPTS = int(os.getenv("AWX_SYNC_ATTEMPTS", "120"))


def normalize_git_repo_url(repo_url):
    if repo_url.startswith(("https://", "http://", "git@")):
        return repo_url
    return f"https://{repo_url}"


def build_headers():
    return {
        "Authorization": f"Bearer {AWX_TOKEN}",
        "Content-Type": "application/json",
    }


def response_error(response):
    try:
        detail = response.json()
    except ValueError:
        detail = response.text

    return str(detail)[:1000]


def get_credential_by_name(credential_name):
    headers = build_headers()

    try:
        response = requests.get(
            f"{AWX_URL}/api/v2/credentials/?name={credential_name}",
            headers=headers,
            timeout=AWX_REQUEST_TIMEOUT,
        )
    except Exception as exc:
        logger.error(f"Exception while looking up AWX credential: {str(exc)}")
        return None

    if response.status_code != 200:
        logger.error(f"AWX credential lookup failed: {response.status_code}")
        return None

    credentials = response.json()
    if credentials.get("count", 0) == 0:
        return None

    return credentials["results"][0]["id"]


def get_azure_credential():
    credential_id = get_credential_by_name(AWX_AZURE_CREDENTIAL_NAME)
    if credential_id:
        logger.info(f"Azure credential found: ID {credential_id}")
        return credential_id

    logger.error(f"Azure credential '{AWX_AZURE_CREDENTIAL_NAME}' not found in AWX.")
    return None


def get_scm_credential():
    if not AWX_SCM_CREDENTIAL_NAME:
        return None

    credential_id = get_credential_by_name(AWX_SCM_CREDENTIAL_NAME)
    if credential_id:
        logger.info(f"SCM credential found: ID {credential_id}")
        return credential_id

    logger.error(f"SCM credential '{AWX_SCM_CREDENTIAL_NAME}' not found in AWX.")
    return None


def project_payload():
    payload = {
        "name": AWX_PROJECT_NAME,
        "description": "Generated Azure deployment playbooks",
        "scm_type": "git",
        "scm_url": normalize_git_repo_url(GIT_REPO),
        "scm_branch": GIT_BRANCH,
        "scm_update_on_launch": True,
    }

    scm_credential_id = get_scm_credential()
    if scm_credential_id:
        payload["credential"] = scm_credential_id

    return payload


def sync_project(project_id):
    headers = build_headers()
    response = requests.post(
        f"{AWX_URL}/api/v2/projects/{project_id}/update/",
        headers=headers,
        timeout=AWX_REQUEST_TIMEOUT,
    )

    if response.status_code != 202:
        error = f"Project sync launch failed: {response.status_code} - {response_error(response)}"
        logger.warning(error)
        return {"success": False, "error": error}

    update_url = response.json().get("url")
    if not update_url:
        error = "Project sync did not return an update URL."
        logger.warning(error)
        return {"success": False, "error": error}

    for attempt in range(AWX_SYNC_ATTEMPTS):
        status_response = requests.get(
            f"{AWX_URL}{update_url}",
            headers=headers,
            timeout=AWX_REQUEST_TIMEOUT,
        )
        if status_response.status_code != 200:
            error = f"Project sync status failed: {status_response.status_code} - {response_error(status_response)}"
            logger.warning(error)
            return {"success": False, "error": error}

        project_update = status_response.json()
        status = project_update.get("status")
        if status == "successful":
            logger.info("Project sync completed successfully.")
            return {"success": True}
        if status in {"failed", "error", "canceled"}:
            explanation = project_update.get("job_explanation") or project_update.get("result_traceback") or ""
            error = f"Project sync failed with status: {status}. {explanation}".strip()
            logger.error(error)
            return {"success": False, "error": error}

        logger.info(f"Project sync pending ({attempt + 1}/{AWX_SYNC_ATTEMPTS}): {status}")
        time.sleep(2)

    error = "Project sync timed out."
    logger.warning(error)
    return {"success": False, "error": error}


def create_or_get_project():
    headers = build_headers()
    payload = project_payload()

    try:
        response = requests.get(
            f"{AWX_URL}/api/v2/projects/?name={AWX_PROJECT_NAME}",
            headers=headers,
            timeout=AWX_REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            error = f"Project lookup failed: {response.status_code} - {response_error(response)}"
            logger.error(error)
            return {"success": False, "error": error}

        projects = response.json()
        if projects.get("count", 0) > 0:
            project_id = projects["results"][0]["id"]
            patch_response = requests.patch(
                f"{AWX_URL}/api/v2/projects/{project_id}/",
                headers=headers,
                json=payload,
                timeout=AWX_REQUEST_TIMEOUT,
            )
            if patch_response.status_code not in {200, 202}:
                error = f"Project update failed: {patch_response.status_code} - {response_error(patch_response)}"
                logger.error(error)
                return {"success": False, "error": error}

            sync_result = sync_project(project_id)
            if not sync_result["success"]:
                return sync_result
            return {"success": True, "project_id": project_id}

        create_response = requests.post(
            f"{AWX_URL}/api/v2/projects/",
            headers=headers,
            json=payload,
            timeout=AWX_REQUEST_TIMEOUT,
        )

        if create_response.status_code != 201:
            error = f"Project creation failed: {create_response.status_code} - {response_error(create_response)}"
            logger.error(error)
            return {"success": False, "error": error}

        project = create_response.json()
        project_id = project["id"]
        sync_result = sync_project(project_id)
        if not sync_result["success"]:
            return sync_result
        return {"success": True, "project_id": project_id}
    except Exception as exc:
        error = f"Exception while creating or getting AWX project: {str(exc)}"
        logger.error(error)
        return {"success": False, "error": error}


def create_job_template(app_name, playbook_filename):
    logger.info(f"Creating AWX job template for {app_name}")
    logger.info(f"Playbook: {playbook_filename}")

    project_result = create_or_get_project()
    if not project_result["success"]:
        return {
            "success": False,
            "error": project_result["error"],
        }
    project_id = project_result["project_id"]

    azure_credential_id = get_azure_credential()
    if not azure_credential_id:
        return {
            "success": False,
            "error": "Credential Azure AWX introuvable.",
        }

    headers = build_headers()
    job_template_data = {
        "name": f"Deploy-{app_name}",
        "description": f"Deployment job for {app_name}",
        "playbook": playbook_filename,
        "project": project_id,
        "inventory": AWX_INVENTORY_ID,
        "job_type": "run",
        "verbosity": 1,
        "credentials": [azure_credential_id],
        "extra_vars": "{}",
    }

    try:
        response = requests.post(
            f"{AWX_URL}/api/v2/job_templates/",
            headers=headers,
            json=job_template_data,
            timeout=AWX_REQUEST_TIMEOUT,
        )

        logger.info(f"AWX job template response: {response.status_code}")
        if response.status_code == 201:
            job_template = response.json()
            return {
                "success": True,
                "job_template_id": job_template["id"],
                "job_template_name": job_template["name"],
                "message": "Job template cree avec succes avec credential Azure AWX.",
            }

        return {
            "success": False,
            "error": f"Erreur AWX: {response.status_code} - {response_error(response)}",
        }
    except Exception as exc:
        logger.error(f"Exception while creating AWX job template: {str(exc)}")
        return {
            "success": False,
            "error": f"Erreur de connexion AWX: {str(exc)}",
        }


def launch_job(job_template_id):
    headers = build_headers()

    try:
        logger.info(f"Launching AWX job template ID: {job_template_id}")
        response = requests.post(
            f"{AWX_URL}/api/v2/job_templates/{job_template_id}/launch/",
            headers=headers,
            timeout=10,
        )

        logger.info(f"AWX launch response: {response.status_code}")
        if response.status_code == 201:
            job = response.json()
            return {
                "success": True,
                "job_id": job["id"],
                "status": job.get("status", "pending"),
                "job_name": job.get("name", "Unknown"),
                "url": job.get("url", ""),
                "message": "Job lance avec succes",
            }

        return {
            "success": False,
            "error": f"Erreur lors du lancement: {response.status_code}",
        }
    except Exception as exc:
        logger.error(f"Exception while launching AWX job: {str(exc)}")
        return {
            "success": False,
            "error": f"Erreur de connexion: {str(exc)}",
        }


def get_job_status(job_id):
    headers = build_headers()

    try:
        response = requests.get(
            f"{AWX_URL}/api/v2/jobs/{job_id}/",
            headers=headers,
            timeout=10,
        )

        logger.info(f"AWX job status response: {response.status_code}")
        if response.status_code == 200:
            job = response.json()
            return {
                "success": True,
                "job_id": job["id"],
                "status": job.get("status"),
                "failed": job.get("failed", False),
                "finished": job.get("finished"),
                "started": job.get("started"),
                "url": job.get("url", ""),
            }

        return {
            "success": False,
            "error": f"Erreur statut job AWX: {response.status_code}",
        }
    except Exception as exc:
        logger.error(f"Exception while reading AWX job status: {str(exc)}")
        return {
            "success": False,
            "error": f"Erreur de connexion: {str(exc)}",
        }
