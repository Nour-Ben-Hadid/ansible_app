import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from routes.formulaire import FormulaireParams
from routes import awx as awx_routes
from services import awx_integration
from services import deployment_store
from services.playbook_generator import build_playbook_path


def valid_payload(**overrides):
    payload = {
        "nom_app": "demo-app",
        "resource_group": "rg-demo-app",
        "app_repo_url": "https://github.com/example/demo-app.git",
        "app_repo_branch": "main",
        "description": "demo",
        "location": "eastus",
        "app_type": "web_app",
        "app_language": "python",
        "python_version": "3.11",
        "startup_file": "app.py",
    }
    payload.update(overrides)
    return payload


class SecurityBaselineTests(unittest.TestCase):
    def test_valid_form_payload_is_accepted(self):
        params = FormulaireParams(**valid_payload())

        self.assertEqual(params.nom_app, "demo-app")
        self.assertEqual(params.startup_file, "app.py")

    def test_invalid_app_name_is_rejected(self):
        with self.assertRaises(ValidationError):
            FormulaireParams(**valid_payload(nom_app="../../bad"))

    def test_startup_path_traversal_is_rejected(self):
        with self.assertRaises(ValidationError):
            FormulaireParams(**valid_payload(startup_file="../app.py"))

    def test_playbook_path_rejects_traversal_name(self):
        with self.assertRaises(HTTPException):
            build_playbook_path("../bad")

    def test_awx_template_payload_does_not_include_azure_secret(self):
        captured_payload = {}

        class Response:
            status_code = 201

            def json(self):
                return {"id": 123, "name": "Deploy-demo-app"}

        def fake_post(url, headers=None, json=None, timeout=None):
            captured_payload.update(json)
            return Response()

        with patch.object(awx_integration, "create_or_get_project", return_value={"success": True, "project_id": 10}), \
                patch.object(awx_integration, "get_azure_credential", return_value=42), \
                patch.object(awx_integration.requests, "post", side_effect=fake_post):
            result = awx_integration.create_job_template("demo-app", "demo-app_playbook.yml")

        self.assertTrue(result["success"])
        self.assertEqual(captured_payload["credentials"], [42])
        self.assertEqual(captured_payload["extra_vars"], "{}")
        self.assertNotIn("azure_secret", json.dumps(captured_payload))

    def test_awx_project_payload_uses_shared_repo_and_scm_credential(self):
        with patch.object(awx_integration, "GIT_REPO", "github.com/example/ansible-playbooks.git"), \
                patch.object(awx_integration, "GIT_BRANCH", "generated-playbooks"), \
                patch.object(awx_integration, "AWX_PROJECT_NAME", "azure-generated-playbooks"), \
                patch.object(awx_integration, "AWX_SCM_CREDENTIAL_NAME", "github-ansible-playbooks"), \
                patch.object(awx_integration, "get_credential_by_name", return_value=99):
            payload = awx_integration.project_payload()

        self.assertEqual(payload["name"], "azure-generated-playbooks")
        self.assertEqual(payload["scm_url"], "https://github.com/example/ansible-playbooks.git")
        self.assertEqual(payload["scm_branch"], "generated-playbooks")
        self.assertEqual(payload["credential"], 99)

    def test_deployment_store_persists_form_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                patch.object(deployment_store, "DATABASE_URL", Path(temp_dir) / "deployments.db"):
            deployment_store.create_deployment("deployment-1", valid_payload())
            deployment = deployment_store.get_deployment("deployment-1")
            deployments = deployment_store.list_deployments()

        self.assertEqual(deployment["id"], "deployment-1")
        self.assertEqual(deployment["params"]["nom_app"], "demo-app")
        self.assertEqual(deployment["status"], "CREATED")
        self.assertEqual(len(deployments), 1)

    def test_launch_deployment_uses_stored_job_template_id(self):
        updates = []

        with patch.object(awx_routes, "get_deployment", return_value={"awx_template_id": 123}), \
                patch.object(awx_routes, "update_deployment", side_effect=lambda *args, **kwargs: updates.append((args, kwargs))), \
                patch.object(awx_routes, "launch_job", return_value={"success": True, "job_id": 456, "status": "pending"}):
            result = awx_routes.launch_deployment("deployment-1")

        self.assertEqual(result["job_id"], 456)
        self.assertEqual(updates[0][1]["status"], "AWX_PENDING")
        self.assertEqual(updates[1][1]["awx_job_id"], 456)

    def test_refresh_deployment_status_maps_awx_running(self):
        updates = []
        deployment = {"id": "deployment-1", "awx_job_id": 456}
        refreshed = {"id": "deployment-1", "awx_job_id": 456, "status": "AWX_RUNNING"}

        with patch.object(awx_routes, "get_deployment", side_effect=[deployment, refreshed]), \
                patch.object(awx_routes, "update_deployment", side_effect=lambda *args, **kwargs: updates.append((args, kwargs))), \
                patch.object(awx_routes, "get_job_status", return_value={"success": True, "job_id": 456, "status": "running"}):
            result = awx_routes.refresh_deployment_status("deployment-1")

        self.assertEqual(updates[0][1]["status"], "AWX_RUNNING")
        self.assertEqual(result["awx"]["status"], "running")


if __name__ == "__main__":
    unittest.main()
