from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from routes import formulaire, awx
from services import playbook_generator
from fastapi.staticfiles import StaticFiles


app = FastAPI()

app.include_router(formulaire.router)
app.include_router(awx.router)
app.include_router(playbook_generator.router)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def get_formulaire():
    with open("static/formulaire.html", "r", encoding="utf-8") as f:
        return f.read()
