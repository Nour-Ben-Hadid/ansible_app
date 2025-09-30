from fastapi import APIRouter, HTTPException, status
import json
import os
import yaml
import subprocess
from dotenv import load_dotenv
from .awx_integration import create_job_template

load_dotenv()

router = APIRouter()
FICHIER_JSON = "data/formulaires.json"
PLAYBOOK_DIR = "playbooks/"

GIT_TOKEN = os.getenv("GIT_TOKEN")
GIT_REPO = os.getenv("GIT_REPO")
GIT_BRANCH = os.getenv("GIT_BRANCH", "main")

# Régions disponibles pour les Web Apps
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
    """Génère un playbook pour une Web App Python sur Linux"""

    startup_file = config['params'].get('startup_file', None)
    python_version = config['params'].get('python_version', '3.11')

    # Déterminer la commande de démarrage Python
    startup_command = ""
    if startup_file and startup_file.strip():
        # L'utilisateur a spécifié un fichier de démarrage
        clean_startup_file = startup_file.strip().lstrip('./')
        # Détecter si c'est un module ou un fichier
        if clean_startup_file.endswith('.py'):
            module_name = clean_startup_file.replace('.py', '')
            startup_command = f"gunicorn {module_name}:app"
        elif ':' in clean_startup_file:
            startup_command = f"gunicorn {clean_startup_file}"
        else:
            # Module sans spécification de l'objet app, on assume :app
            startup_command = f"gunicorn {clean_startup_file}:app"
    else:
        # Valeur par défaut pour Python : utiliser gunicorn avec app.py
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
                "python_version": python_version
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
                    "register": "auth_response"
                },
                {
                    "name": "Set access token fact",
                    "ansible.builtin.set_fact": {
                        "access_token": "{{ auth_response.json.access_token }}"
                    }
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
                            "Startup Command: {{ webapp_config_check.json.properties.appCommandLine | default('Non spécifié') }}",
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
    """Génère un playbook pour une Web App Node.js sur Linux"""

    startup_file = config['params'].get('startup_file', None)

    startup_command = ""
    if startup_file:
        clean_startup_file = startup_file.lstrip('./')
        startup_command = f"node {clean_startup_file}"
    else:
        startup_command = "npm start"  # Commande par défaut pour Linux

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
                "startup_command": startup_command
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
                    "register": "auth_response"
                },
                {
                    "name": "Set access token fact",
                    "ansible.builtin.set_fact": {
                        "access_token": "{{ auth_response.json.access_token }}"
                    }
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
    """Récupère les régions disponibles pour les Web Apps"""
    return {"regions": REGIONS_WEB_APP}


@router.get("/api/languages")
def get_supported_languages():
    """Récupère la liste des langages supportés pour les applications"""
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


@router.post("/api/generer-playbook", status_code=status.HTTP_201_CREATED)
def generer_playbook(formulaire_id: str):
    git_enabled = GIT_TOKEN and GIT_REPO

    if not os.path.exists(FICHIER_JSON):
        raise HTTPException(
            status_code=404, detail="Aucune configuration trouvée.")

    with open(FICHIER_JSON, "r") as f:
        configurations = json.load(f)

    config = next(
        (c for c in configurations if c["id"] == formulaire_id), None)
    if not config:
        raise HTTPException(status_code=404, detail="Formulaire non trouvé.")

    import datetime
    timestamp = datetime.datetime.now().strftime("%m%d-%H%M")
    unique_app_name = f"{config['params']['nom_app']}-{timestamp}"

    app_type = config['params'].get('app_type', 'web_app')
    app_language = config['params'].get('app_language', None)

    if not app_language:
        raise HTTPException(
            status_code=400,
            detail="Le champ 'app_language' est obligatoire. L'utilisateur doit choisir explicitement 'python' ou 'nodejs' dans le formulaire."
        )

    # DEBUG: Affichage des valeurs pour diagnostiquer le problème
    print(f" DEBUG - app_type: {app_type}")
    print(f" DEBUG - app_language: {app_language}")
    print(f" DEBUG - config params: {config['params']}")

    # Générer le playbook selon le langage choisi EXPLICITEMENT par l'utilisateur
    if app_language.lower() == 'python':
        print(" SELECTION: Python Web App playbook")
        playbook_content = generate_python_web_app_playbook(
            config, unique_app_name)
    elif app_language.lower() == 'nodejs':
        print(" SELECTION: Node.js Web App playbook")
        playbook_content = generate_nodejs_web_app_playbook(
            config, unique_app_name)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Langage '{app_language}' non supporté."
        )

    os.makedirs(PLAYBOOK_DIR, exist_ok=True)
    playbook_filename = f"{config['params']['nom_app']}_playbook.yml"
    playbook_path = os.path.join(PLAYBOOK_DIR, playbook_filename)

    with open(playbook_path, "w") as f:
        yaml.dump(playbook_content, f, sort_keys=False)

    # Git operations
    if git_enabled:
        repo_url_token = f"https://{GIT_TOKEN}@{GIT_REPO}"

        # Initialisation git si nécessaire
        if not os.path.exists(os.path.join(PLAYBOOK_DIR, ".git")):
            subprocess.run(["git", "-C", PLAYBOOK_DIR, "init"], check=True)
            subprocess.run(["git", "-C", PLAYBOOK_DIR, "remote",
                            "add", "origin", GIT_REPO], check=True)

        try:
            # Pull first to avoid conflicts
            subprocess.run(["git", "-C", PLAYBOOK_DIR, "pull", "origin", GIT_BRANCH],
                           check=False)  # Don't fail if pull has conflicts

            # Met à jour l'URL du remote avec token pour le push
            subprocess.run(["git", "-C", PLAYBOOK_DIR, "remote",
                           "set-url", "origin", repo_url_token], check=True)

            subprocess.run(["git", "-C", PLAYBOOK_DIR, "checkout",
                            "-B", GIT_BRANCH], check=True)
            subprocess.run(["git", "-C", PLAYBOOK_DIR, "add",
                            playbook_filename], check=True)
            subprocess.run(["git", "-C", PLAYBOOK_DIR, "commit", "-m",
                            f"Add Linux playbook for {config['params']['nom_app']}"], check=True)
            subprocess.run(["git", "-C", PLAYBOOK_DIR, "push",
                            "-u", "origin", GIT_BRANCH], check=True)

            commit_url = f"Playbook committed to {GIT_REPO} on branch {GIT_BRANCH}"
        except subprocess.CalledProcessError as e:
            raise HTTPException(status_code=500, detail=f"Erreur git : {e}")
    else:
        commit_url = "Git operations disabled - no GIT_TOKEN or GIT_REPO configured"

    # Créer automatiquement le job template dans AWX
    awx_result = create_job_template(
        unique_app_name, playbook_filename)

    # Déterminer les détails runtime selon le langage
    runtime_details = "Node.js 18 LTS on Linux"

    if app_language.lower() in ['python', 'py']:
        python_version = config['params'].get('python_version', '3.11')
        runtime_details = f"Python {python_version} on Linux"

    return {
        "message": f"Azure Linux deployment playbook généré avec succès pour {app_language.upper()}.",
        "fichier": playbook_filename,
        "playbook_path": playbook_path,
        "commit": commit_url,
        "git_branch": GIT_BRANCH if git_enabled else "N/A",
        "app_url": f"https://{unique_app_name}.azurewebsites.net",
        "deployment_details": {
            "app_name": unique_app_name,
            "original_app_name": config['params']['nom_app'],
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
        "validation_errors": []
    }
