from fastapi import APIRouter, HTTPException

from services.awx_integration import get_job_status, launch_job
from services.deployment_store import get_deployment, update_deployment

router = APIRouter()


def map_awx_status(status):
    if status in {"pending", "waiting", "new"}:
        return "AWX_PENDING"
    if status in {"running"}:
        return "AWX_RUNNING"
    if status == "successful":
        return "AWX_SUCCESS"
    if status in {"failed", "error", "canceled"}:
        return "FAILED"
    return "AWX_PENDING"


@router.post("/api/deployments/{deployment_id}/launch")
def launch_deployment(deployment_id: str):
    deployment = get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")

    job_template_id = deployment.get("awx_template_id")
    if not job_template_id:
        raise HTTPException(status_code=400, detail="Deployment is not ready to launch.")

    update_deployment(deployment_id, status="AWX_PENDING")
    result = launch_job(job_template_id)

    if result["success"]:
        update_deployment(
            deployment_id,
            status="AWX_PENDING",
            awx_job_id=result["job_id"],
            error_message=None,
        )
        return result

    update_deployment(
        deployment_id,
        status="FAILED",
        error_message=result["error"],
    )
    raise HTTPException(status_code=500, detail=result["error"])


@router.get("/api/deployments/{deployment_id}/refresh")
def refresh_deployment_status(deployment_id: str):
    deployment = get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found.")

    awx_job_id = deployment.get("awx_job_id")
    if not awx_job_id:
        return deployment

    result = get_job_status(awx_job_id)
    if not result["success"]:
        update_deployment(
            deployment_id,
            status="FAILED",
            error_message=result["error"],
        )
        raise HTTPException(status_code=500, detail=result["error"])

    deployment_status = map_awx_status(result.get("status"))
    error_message = None
    if deployment_status == "FAILED":
        error_message = f"AWX job ended with status: {result.get('status')}"

    update_deployment(
        deployment_id,
        status=deployment_status,
        error_message=error_message,
    )

    refreshed = get_deployment(deployment_id)
    return {
        **refreshed,
        "awx": result,
    }


@router.post("/api/launch-job/{job_template_id}")
def launch_awx_job(job_template_id: int):
    """Lance un job AWX à partir de son ID"""
    result = launch_job(job_template_id)

    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=500, detail=result["error"])
