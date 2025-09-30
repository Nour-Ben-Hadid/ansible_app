from fastapi import APIRouter, status, HTTPException
from pydantic import BaseModel
from services.awx_integration import launch_job

router = APIRouter()


@router.post("/api/launch-job/{job_template_id}")
def launch_awx_job(job_template_id: int):
    """Lance un job AWX à partir de son ID"""
    result = launch_job(job_template_id)

    if result["success"]:
        return result
    else:
        raise HTTPException(status_code=500, detail=result["error"])
