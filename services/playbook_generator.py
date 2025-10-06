from fastapi import APIRouter, HTTPException, status
from pathlib import Path
import os
import re
import subprocess
import tempfile
import yaml
from dotenv import load_dotenv
from .deployment_store import get_deployment, update_deployment
from .awx_integration import create_job_template

load_dotenv()

router = APIRouter()
PLAYBOOK_DIR = Path(os.getenv("PLAYBOOK_WORKTREE", ".runtime/ansible-playbooks")).resolve()
APP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{1,39}$")

GIT_TOKEN = os.getenv("GIT_TOKEN")
GIT_REPO = os.getenv("GIT_REPO")
GIT_BRANCH = os.getenv("GIT_BRANCH", "main")


def normalize_git_repo_url(repo_url):
    if repo_url.startswith(("https://", "http://", "git@")):
        return repo_url
    return f"https://{repo_url}"


def create_git_askpass_script():
    if os.name == "nt":
        script = tempfile.NamedTemporaryFile("w", suffix=".bat", delete=False)
        script.write(
            "@echo off\n"
            "echo %~1 | findstr /I \"username\" >nul\n"
            "if not errorlevel 1 (\n"
            "  echo x-access-token\n"
            ") else (\n"
            "  echo %GIT_ASKPASS_TOKEN%\n"
            ")\n"
        )
    else:
        script = tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False)
        script.write(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *sername*) printf '%s\\n' 'x-access-token' ;;\n"
            "  *) printf '%s\\n' \"$GIT_ASKPASS_TOKEN\" ;;\n"
            "esac\n"
        )
    script.close()
    if os.name != "nt":
        os.chmod(script.name, 0o700)
    return script.name


def git_env_with_token(askpass_script):
    env = os.environ.copy()
    env["GIT_ASKPASS"] = askpass_script
    env["GIT_ASKPASS_TOKEN"] = GIT_TOKEN
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def run_git(args, env=None, cwd=None):
    git_cwd = cwd or PLAYBOOK_DIR
    return subprocess.run(
        ["git", "-C", str(git_cwd), *args],
        check=True,
        env=env,
    )


def has_staged_git_changes():
    result = subprocess.run(
        ["git", "-C", str(PLAYBOOK_DIR), "diff", "--cached", "--quiet"],
        check=False,
    )
    return result.returncode != 0


def ensure_playbook_worktree(repo_url, git_env):
    repo_parent = PLAYBOOK_DIR.parent
    repo_parent.mkdir(parents=True, exist_ok=True)

    if (PLAYBOOK_DIR / ".git").exists():
        run_git(["remote", "set-url", "origin", repo_url])
        run_git(["fetch", "origin", GIT_BRANCH], env=git_env)
        run_git(["checkout", GIT_BRANCH])
        run_git(["pull", "--ff-only", "origin", GIT_BRANCH], env=git_env)
        return

    if PLAYBOOK_DIR.exists() and any(PLAYBOOK_DIR.iterdir()):
        raise HTTPException(
            status_code=500,
            detail=f"Playbook worktree exists but is not a Git repository: {PLAYBOOK_DIR}"
        )

    subprocess.run(
        ["git", "clone", "--branch", GIT_BRANCH, repo_url, str(PLAYBOOK_DIR)],
        check=True,
        env=git_env,
    )


def build_playbook_path(app_name):
    if not APP_NAME_PATTERN.fullmatch(app_name):
        raise HTTPException(
            status_code=400,
            detail="Nom d'application invalide. Utilisez 2 a 40 caracteres alphanumeriques ou tirets."
        )

    playbook_filename = f"{app_name}_playbook.yml"
    playbook_path = (PLAYBOOK_DIR / playbook_filename).resolve()

    try:
        playbook_path.relative_to(PLAYBOOK_DIR)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Chemin de playbook invalide."
        ) from exc

    return playbook_filename, playbook_path

# RÃ©gions disponibles pour les Web Apps
REGIONS_WEB_APP = {
    "East US": "eastus",
    "East US 2": "eastus2",
    "West US": "westus",
    "West US 2": "westus2",
    "West US 3": "westus3",
    "Central US": "centralus",
    "North Central US": "northcentralus",
    "South Central US": "southcentralus",
    "West Central US": "westcentralus",
    "Canada Central": "canadacentral",
    "Canada East": "canadaeast",
    "Brazil South": "brazilsouth",
    "North Europe": "northeurope",
    "West Europe": "westeurope",
    "UK South": "uksouth",
    "UK West": "ukwest",
    "France Central": "francecentral",
    "Germany West Central": "germanywestcentral",
    "Switzerland North": "switzerlandnorth",
    "Norway East": "norwayeast",
    "Southeast Asia": "southeastasia",
    "East Asia": "eastasia",
    "Australia East": "australiaeast",
    "Australia Southeast": "australiasoutheast",
    "Central India": "centralindia",
    "South India": "southindia",
    "West India": "westindia",
    "Japan East": "japaneast",
    "Japan West": "japanwest",
    "Korea Central": "koreacentral",
    "Korea South": "koreasouth"
}


def generate_python_web_app_playbook(config, unique_app_name):
    """GÃ©nÃ¨re un playbook pour une Web App Python sur Linux"""

    startup_file = config['params'].get('startup_file', None)
    python_version = config['params'].get('python_version', '3.11')

    # DÃ©terminer la commande de dÃ©marrage Python
    startup_command = ""
    if startup_file and startup_file.strip():
        # L'utilisateur a spÃ©cifiÃ© un fichier de dÃ©marrage
        clean_startup_file = startup_file.strip().lstrip('./')
        # DÃ©tecter si c'est un module ou un fichier
        if clean_startup_file.endswith('.py'):
            module_name = clean_startup_file.replace('.py', '')
            startup_command = f"gunicorn {module_name}:app"
        elif ':' in clean_startup_file:
            startup_command = f"gunicorn {clean_startup_file}"
        else:
            # Module sans spÃ©cification de l'objet app, on assume :app
            startup_command = f"gunicorn {clean_startup_file}:app"
    else:
        # Valeur par dÃ©faut pour Python : utiliser gunicorn avec app.py
        startup_command = "gunicorn app:app"

    return [
        {
            "name": f"Deploy {unique_app_name} Python App to Azure Linux App Service",
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "resource_group": config['params']['resource_group'],
                "app_name": unique_app_name,
                "original_app_name": config['params']['nom_app'],
                "location": config['params']['location'],
                "app_repo_url": config['params']['app_repo_url'],
                "app_repo_branch": config['params']['app_repo_branch'],
                "startup_file": config['params'].get('startup_file', 'auto-detect'),
                "startup_command": startup_command,
                "python_version": python_version,
                "azure_subscription_id": "{{ lookup('env', 'AZURE_SUBSCRIPTION_ID') }}",
                "azure_client_id": "{{ lookup('env', 'AZURE_CLIENT_ID') }}",
                "azure_secret": "{{ lookup('env', 'AZURE_SECRET') }}",
                "azure_tenant": "{{ lookup('env', 'AZURE_TENANT') }}"
            },
            "tasks": [
                {
                    "name": "Get Azure access token",
                    "ansible.builtin.uri": {
                        "url": "https://login.microsoftonline.com/{{ azure_tenant }}/oauth2/v2.0/token",
                        "method": "POST",
                        "body_format": "form-urlencoded",
                        "body": {
                            "grant_type": "client_credentials",
                            "client_id": "{{ azure_client_id }}",
                            "client_secret": "{{ azure_secret }}",
                            "scope": "https://management.azure.com/.default"
                        }
                    },
                    "register": "auth_response",
                    "no_log": True
                },
                {
                    "name": "Set access token fact",
                    "ansible.builtin.set_fact": {
                        "access_token": "{{ auth_response.json.access_token }}"
                    },
                    "no_log": True
                },
                {
                    "name": "Create or update Resource Group",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourcegroups/{{ resource_group }}?api-version=2021-04-01",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "body_format": "json",
                        "body": {
                            "location": "{{ location }}",
                            "tags": {
                                "Environment": "Production",
                                "CreatedBy": "Ansible-Playbook",
                                "Platform": "Linux",
                                "Language": "Python"
                            }
                        },
                        "status_code": [200, 201]
                    },
                    "register": "rg_response"
                },
                {
                    "name": "Create Linux App Service Plan",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/serverfarms/{{ app_name }}-plan?api-version=2021-02-01",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "body_format": "json",
                        "body": {
                            "location": "{{ location }}",
                            "kind": "linux",
                            "properties": {
                                "name": "{{ app_name }}-plan",
                                "reserved": True
                            },
                            "sku": {
                                "name": "F1",
                                "tier": "Free"
                            }
                        },
                        "status_code": [200, 201]
                    },
                    "register": "asp_response"
                },
                {
                    "name": "Create Linux Python Web App",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/sites/{{ app_name }}?api-version=2021-02-01",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "body_format": "json",
                        "body": {
                            "location": "{{ location }}",
                            "kind": "app,linux",
                            "properties": {
                                "serverFarmId": "/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/serverfarms/{{ app_name }}-plan",
                                "reserved": True,
                                "siteConfig": {
                                    "linuxFxVersion": "PYTHON|{{ python_version }}",
                                    "appCommandLine": "{{ startup_command }}",
                                    "appSettings": [
                                        {
                                            "name": "WEBSITES_ENABLE_APP_SERVICE_STORAGE",
                                            "value": "false"
                                        },
                                        {
                                            "name": "SCM_DO_BUILD_DURING_DEPLOYMENT",
                                            "value": "true"
                                        },
                                        {
                                            "name": "ENABLE_ORYX_BUILD",
                                            "value": "true"
                                        },
                                        {
                                            "name": "PYTHONPATH",
                                            "value": "/home/site/wwwroot"
                                        },
                                        {
                                            "name": "DJANGO_SETTINGS_MODULE",
                                            "value": "settings"
                                        },
                                        {
                                            "name": "PORT",
                                            "value": "8000"
                                        },
                                        {
                                            "name": "GUNICORN_CMD_ARGS",
                                            "value": "--bind=0.0.0.0:8000 --timeout 600 --access-logfile '-' --error-logfile '-'"
                                        }
                                    ],
                                    "alwaysOn": False,
                                    "ftpsState": "Disabled",
                                    "minTlsVersion": "1.2",
                                    "http20Enabled": True
                                }
                            }
                        },
                        "status_code": [200, 201]
                    },
                    "register": "webapp_response"
                },
                {
                    "name": "Configure advanced Python settings",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/sites/{{ app_name }}/config/web?api-version=2021-02-01",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "body_format": "json",
                        "body": {
                            "properties": {
                                "numberOfWorkers": 1,
                                "linuxFxVersion": "PYTHON|{{ python_version }}",
                                "appCommandLine": "{{ startup_command }}",
                                "requestTracingEnabled": True,
                                "httpLoggingEnabled": True,
                                "logsDirectorySizeLimit": 35,
                                "detailedErrorLoggingEnabled": True,
                                "scmType": "GitHub",
                                "alwaysOn": False,
                                "http20Enabled": True,
                                "minTlsVersion": "1.2",
                                "ftpsState": "Disabled",
                                "localMySqlEnabled": False,
                                "managedPipelineMode": "Integrated",
                                "remoteDebuggingEnabled": False,
                                "use32BitWorkerProcess": True
                            }
                        },
                        "status_code": [200, 201]
                    },
                    "register": "webapp_config_response"
                },
                {
                    "name": "Verify Python configuration",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/sites/{{ app_name }}/config/web?api-version=2021-02-01",
                        "method": "GET",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "status_code": [200]
                    },
                    "register": "webapp_config_check"
                },
                {
                    "name": "Display Python configuration status",
                    "ansible.builtin.debug": {
                        "msg": [
                            "=== LINUX PYTHON WEB APP CONFIGURATION VERIFICATION ===",
                            "Platform: Linux Container",
                            "Runtime Stack: {{ webapp_config_check.json.properties.linuxFxVersion | default('Not specified') }}",
                            "Startup Command: {{ webapp_config_check.json.properties.appCommandLine | default('Non spÃ©cifiÃ©') }}",
                            "Reserved (Linux): {{ webapp_config_check.json.properties.reserved | default(false) }}",
                            "64-bit Platform: {{ not webapp_config_check.json.properties.use32BitWorkerProcess | default(false) }}",
                            "Status:  Linux Python App Service successfully configured"
                        ]
                    }
                },
                {
                    "name": "Remove existing deployment source",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/sites/{{ app_name }}/sourcecontrols/web?api-version=2021-02-01",
                        "method": "DELETE",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "status_code": [200, 204, 404]
                    },
                    "register": "remove_deployment_response",
                    "ignore_errors": True
                },
                {
                    "name": "Wait for Web App to be ready",
                    "ansible.builtin.pause": {
                        "seconds": 30
                    }
                },
                {
                    "name": "Configure deployment source (GitHub)",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/sites/{{ app_name }}/sourcecontrols/web?api-version=2021-02-01",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "body_format": "json",
                        "body": {
                            "properties": {
                                "repoUrl": "{{ app_repo_url }}",
                                "branch": "{{ app_repo_branch }}",
                                "isManualIntegration": True,
                                "deploymentRollbackEnabled": False,
                                "isMercurial": False
                            }
                        },
                        "status_code": [200, 201, 202],
                        "timeout": 60
                    },
                    "register": "deployment_response",
                    "retries": 3,
                    "delay": 10
                },
                {
                    "name": "Display Python deployment information",
                    "ansible.builtin.debug": {
                        "msg": [
                            "=== LINUX PYTHON WEB APP DEPLOYMENT COMPLETED SUCCESSFULLY! ===",
                            "App Name: {{ app_name }}",
                            "Resource Group: {{ resource_group }}",
                            "Location: {{ location }}",
                            "App URL: https://{{ app_name }}.azurewebsites.net",
                            "Repository: {{ app_repo_url }}",
                            "Branch: {{ app_repo_branch }}",
                            "--- Python Configuration ---",
                            "Platform: Linux Container (Ubuntu)",
                            "Runtime: Python {{ python_version }} on Linux",
                            "Build System: Oryx (automatic pip install)",
                            "Architecture: 32-bit (Free tier limitation)",
                            "Startup Command: {{ startup_command }}",

                        ]
                    }
                }
            ]
        }
    ]


def generate_nodejs_web_app_playbook(config, unique_app_name):
    """GÃ©nÃ¨re un playbook pour une Web App Node.js sur Linux"""

    startup_file = config['params'].get('startup_file', None)

    startup_command = ""
    if startup_file:
        clean_startup_file = startup_file.lstrip('./')
        startup_command = f"node {clean_startup_file}"
    else:
        startup_command = "npm start"  # Commande par dÃ©faut pour Linux

    return [
        {
            "name": f"Deploy {unique_app_name} to Azure Linux App Service",
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "resource_group": config['params']['resource_group'],
                "app_name": unique_app_name,
                "original_app_name": config['params']['nom_app'],
                "location": config['params']['location'],
                "app_repo_url": config['params']['app_repo_url'],
                "app_repo_branch": config['params']['app_repo_branch'],
                "startup_file": config['params'].get('startup_file', 'auto-detect'),
                "startup_command": startup_command,
                "azure_subscription_id": "{{ lookup('env', 'AZURE_SUBSCRIPTION_ID') }}",
                "azure_client_id": "{{ lookup('env', 'AZURE_CLIENT_ID') }}",
                "azure_secret": "{{ lookup('env', 'AZURE_SECRET') }}",
                "azure_tenant": "{{ lookup('env', 'AZURE_TENANT') }}"
            },
            "tasks": [
                {
                    "name": "Get Azure access token",
                    "ansible.builtin.uri": {
                        "url": "https://login.microsoftonline.com/{{ azure_tenant }}/oauth2/v2.0/token",
                        "method": "POST",
                        "body_format": "form-urlencoded",
                        "body": {
                            "grant_type": "client_credentials",
                            "client_id": "{{ azure_client_id }}",
                            "client_secret": "{{ azure_secret }}",
                            "scope": "https://management.azure.com/.default"
                        }
                    },
                    "register": "auth_response",
                    "no_log": True
                },
                {
                    "name": "Set access token fact",
                    "ansible.builtin.set_fact": {
                        "access_token": "{{ auth_response.json.access_token }}"
                    },
                    "no_log": True
                },
                {
                    "name": "Create or update Resource Group",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourcegroups/{{ resource_group }}?api-version=2021-04-01",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "body_format": "json",
                        "body": {
                            "location": "{{ location }}",
                            "tags": {
                                "Environment": "Production",
                                "CreatedBy": "Ansible-Playbook",
                                "Platform": "Linux"
                            }
                        },
                        "status_code": [200, 201]
                    },
                    "register": "rg_response"
                },
                {
                    "name": "Create Linux App Service Plan",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/serverfarms/{{ app_name }}-plan?api-version=2021-02-01",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "body_format": "json",
                        "body": {
                            "location": "{{ location }}",
                            "kind": "linux",
                            "properties": {
                                "name": "{{ app_name }}-plan",
                                "reserved": True
                            },
                            "sku": {
                                "name": "F1",
                                "tier": "Free"
                            }
                        },
                        "status_code": [200, 201]
                    },
                    "register": "asp_response"
                },
                {
                    "name": "Create Linux Web App",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/sites/{{ app_name }}?api-version=2021-02-01",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "body_format": "json",
                        "body": {
                            "location": "{{ location }}",
                            "kind": "app,linux",
                            "properties": {
                                "serverFarmId": "/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/serverfarms/{{ app_name }}-plan",
                                "reserved": True,
                                "siteConfig": {
                                    "linuxFxVersion": "NODE|18-lts",
                                    "appCommandLine": "{{ startup_command }}",
                                    "appSettings": [
                                        {
                                            "name": "WEBSITES_ENABLE_APP_SERVICE_STORAGE",
                                            "value": "false"
                                        },
                                        {
                                            "name": "SCM_DO_BUILD_DURING_DEPLOYMENT",
                                            "value": "true"
                                        },
                                        {
                                            "name": "ENABLE_ORYX_BUILD",
                                            "value": "true"
                                        },
                                        {
                                            "name": "NODE_ENV",
                                            "value": "production"
                                        }
                                    ],
                                    "alwaysOn": False,
                                    "ftpsState": "Disabled",
                                    "minTlsVersion": "1.2",
                                    "http20Enabled": True
                                }
                            }
                        },
                        "status_code": [200, 201]
                    },
                    "register": "webapp_response"
                },
                {
                    "name": "Configure advanced Linux settings",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/sites/{{ app_name }}/config/web?api-version=2021-02-01",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "body_format": "json",
                        "body": {
                            "properties": {
                                "numberOfWorkers": 1,
                                "linuxFxVersion": "NODE|18-lts",
                                "appCommandLine": "{{ startup_command }}",
                                "requestTracingEnabled": True,
                                "httpLoggingEnabled": True,
                                "logsDirectorySizeLimit": 35,
                                "detailedErrorLoggingEnabled": True,
                                "scmType": "GitHub",
                                "alwaysOn": False,
                                "http20Enabled": True,
                                "minTlsVersion": "1.2",
                                "ftpsState": "Disabled",
                                "localMySqlEnabled": False,
                                "managedPipelineMode": "Integrated",
                                "remoteDebuggingEnabled": False,
                                "use32BitWorkerProcess": True
                            }
                        },
                        "status_code": [200, 201]
                    },
                    "register": "webapp_config_response"
                },
                {
                    "name": "Verify Linux configuration",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/sites/{{ app_name }}/config/web?api-version=2021-02-01",
                        "method": "GET",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "status_code": [200]
                    },
                    "register": "webapp_config_check"
                },
                {
                    "name": "Display Linux configuration status",
                    "ansible.builtin.debug": {
                        "msg": [
                            "=== LINUX WEB APP CONFIGURATION VERIFICATION ===",
                            "Platform: Linux Container",
                            "Runtime Stack: {{ webapp_config_check.json.properties.linuxFxVersion | default('Not specified') }}",
                            "Startup Command: {{ webapp_config_check.json.properties.appCommandLine | default('Default npm start') }}",
                            "Reserved (Linux): {{ webapp_config_check.json.properties.reserved | default(false) }}",
                            "64-bit Platform: {{ not webapp_config_check.json.properties.use32BitWorkerProcess | default(false) }}",
                            "Status:  Linux App Service successfully configured"
                        ]
                    }
                },
                {
                    "name": "Remove existing deployment source",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/sites/{{ app_name }}/sourcecontrols/web?api-version=2021-02-01",
                        "method": "DELETE",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "status_code": [200, 204, 404]
                    },
                    "register": "remove_deployment_response",
                    "ignore_errors": True
                },
                {
                    "name": "Wait for Web App to be ready",
                    "ansible.builtin.pause": {
                        "seconds": 30
                    }
                },
                {
                    "name": "Configure deployment source (GitHub)",
                    "ansible.builtin.uri": {
                        "url": "https://management.azure.com/subscriptions/{{ azure_subscription_id }}/resourceGroups/{{ resource_group }}/providers/Microsoft.Web/sites/{{ app_name }}/sourcecontrols/web?api-version=2021-02-01",
                        "method": "PUT",
                        "headers": {
                            "Authorization": "Bearer {{ access_token }}",
                            "Content-Type": "application/json"
                        },
                        "body_format": "json",
                        "body": {
                            "properties": {
                                "repoUrl": "{{ app_repo_url }}",
                                "branch": "{{ app_repo_branch }}",
                                "isManualIntegration": True,
                                "deploymentRollbackEnabled": False,
                                "isMercurial": False
                            }
                        },
                        "status_code": [200, 201, 202],
                        "timeout": 60
                    },
                    "register": "deployment_response",
                    "retries": 3,
                    "delay": 10
                },
                {
                    "name": "Display deployment information",
                    "ansible.builtin.debug": {
                        "msg": [
                            "=== LINUX WEB APP DEPLOYMENT COMPLETED SUCCESSFULLY! ===",
                            "App Name: {{ app_name }}",
                            "Resource Group: {{ resource_group }}",
                            "Location: {{ location }}",
                            "App URL: https://{{ app_name }}.azurewebsites.net",
                            "Repository: {{ app_repo_url }}",
                            "Branch: {{ app_repo_branch }}",
                            "--- Linux Configuration ---",
                            "Platform: Linux Container (Ubuntu)",
                            "Runtime: Node.js 18 LTS on Linux",
                            "Build System: Oryx (automatic npm install)",
                            "Architecture: 32-bit (Free tier limitation)",

                        ]
                    }
                }
            ]
        }
    ]


@router.get("/api/regions/{app_type}")
def get_regions_for_app_type(app_type: str):
    """RÃ©cupÃ¨re les rÃ©gions disponibles pour les Web Apps"""
    return {"regions": REGIONS_WEB_APP}


@router.get("/api/languages")
def get_supported_languages():
    """RÃ©cupÃ¨re la liste des langages supportÃ©s pour les applications"""
    return {
        "languages": {
            "nodejs": {
                "name": "Node.js",
                "description": "Applications JavaScript/TypeScript avec Node.js",
                "runtime_versions": ["18-lts", "16-lts", "14-lts"],
                "frameworks": ["Express", "Next.js", "React", "Vue", "Angular"],
                "startup_commands": ["npm start", "node app.js", "node server.js"]
            },
            "python": {
                "name": "Python",
                "description": "Applications Python avec Flask, Django, FastAPI",
                "runtime_versions": ["3.11", "3.10", "3.9", "3.8"],
                "frameworks": ["Flask", "Django", "FastAPI", "Pyramid"],
                "startup_commands": ["gunicorn app:app", "gunicorn wsgi:application", "python app.py"]
            }
        }
    }


def generate_playbook_from_config(formulaire_id, config, git_enabled):
    update_deployment(formulaire_id, status="VALIDATING")

    app_name = config['params']['nom_app']
    playbook_filename, playbook_path = build_playbook_path(app_name)

    import datetime
    timestamp = datetime.datetime.now().strftime("%m%d-%H%M")
    unique_app_name = f"{app_name}-{timestamp}"

    app_type = config['params'].get('app_type', 'web_app')
    app_language = config['params'].get('app_language', None)

    if not app_language:
        update_deployment(
            formulaire_id,
            status="FAILED",
            error_message="Le champ app_language est obligatoire.",
        )
        raise HTTPException(
            status_code=400,
            detail="Le champ 'app_language' est obligatoire."
        )

    if app_language.lower() == 'python':
        playbook_content = generate_python_web_app_playbook(
            config, unique_app_name)
    elif app_language.lower() == 'nodejs':
        playbook_content = generate_nodejs_web_app_playbook(
            config, unique_app_name)
    else:
        update_deployment(
            formulaire_id,
            status="FAILED",
            error_message=f"Langage non supporte: {app_language}",
        )
        raise HTTPException(
            status_code=400,
            detail=f"Langage '{app_language}' non supporte."
        )

    if git_enabled:
        repo_url = normalize_git_repo_url(GIT_REPO)
        askpass_script = create_git_askpass_script()
        git_env = git_env_with_token(askpass_script)

        try:
            ensure_playbook_worktree(repo_url, git_env)

            with open(playbook_path, "w") as f:
                yaml.dump(playbook_content, f, sort_keys=False)

            update_deployment(
                formulaire_id,
                status="PLAYBOOK_GENERATED",
                playbook_filename=playbook_filename,
            )

            run_git(["add", playbook_filename])
            if has_staged_git_changes():
                run_git(["commit", "-m", f"Add Linux playbook for {app_name}"])
                run_git(["push", "-u", "origin", GIT_BRANCH], env=git_env)
                commit_url = f"Playbook committed to {GIT_REPO} on branch {GIT_BRANCH}"
            else:
                commit_url = f"No playbook changes to commit on branch {GIT_BRANCH}"

            update_deployment(formulaire_id, status="GIT_PUSHED")
        except subprocess.CalledProcessError as e:
            update_deployment(
                formulaire_id,
                status="FAILED",
                error_message=f"Erreur git: {e}",
            )
            raise HTTPException(status_code=500, detail=f"Erreur git : {e}")
        finally:
            try:
                os.unlink(askpass_script)
            except OSError:
                pass
    else:
        PLAYBOOK_DIR.mkdir(parents=True, exist_ok=True)

        with open(playbook_path, "w") as f:
            yaml.dump(playbook_content, f, sort_keys=False)

        update_deployment(
            formulaire_id,
            status="PLAYBOOK_GENERATED",
            playbook_filename=playbook_filename,
        )
        commit_url = "Git operations disabled - no GIT_TOKEN or GIT_REPO configured"

    awx_result = create_job_template(unique_app_name, playbook_filename)

    job_template_id = None
    if awx_result.get("success"):
        job_template_id = awx_result["job_template_id"]
        update_deployment(
            formulaire_id,
            status="AWX_READY",
            awx_template_id=job_template_id,
            app_url=f"https://{unique_app_name}.azurewebsites.net",
            error_message=None,
        )
    else:
        update_deployment(
            formulaire_id,
            status="FAILED",
            error_message=awx_result.get("error", "Erreur AWX inconnue."),
        )

    runtime_details = "Node.js 18 LTS on Linux"
    if app_language.lower() in ['python', 'py']:
        python_version = config['params'].get('python_version', '3.11')
        runtime_details = f"Python {python_version} on Linux"

    return {
        "message": f"Azure Linux deployment playbook genere avec succes pour {app_language.upper()}.",
        "fichier": playbook_filename,
        "playbook_path": str(playbook_path),
        "commit": commit_url,
        "git_branch": GIT_BRANCH if git_enabled else "N/A",
        "app_url": f"https://{unique_app_name}.azurewebsites.net",
        "deployment_details": {
            "app_name": unique_app_name,
            "original_app_name": app_name,
            "app_type": app_type,
            "app_language": app_language,
            "platform": "Linux",
            "resource_group": config['params']['resource_group'],
            "location": config['params']['location'],
            "repo_url": config['params']['app_repo_url'],
            "branch": config['params']['app_repo_branch'],
            "startup_file": config['params'].get('startup_file', 'auto-detect'),
            "runtime": runtime_details
        },
        "git_enabled": git_enabled,
        "awx_integration": awx_result,
        "validation_errors": [],
    }


@router.post("/api/generer-playbook", status_code=status.HTTP_201_CREATED)
def generer_playbook(formulaire_id: str):
    git_enabled = GIT_TOKEN and GIT_REPO
    config = get_deployment(formulaire_id)
    if not config:
        raise HTTPException(status_code=404, detail="Formulaire non trouve.")

    return generate_playbook_from_config(formulaire_id, config, git_enabled)

