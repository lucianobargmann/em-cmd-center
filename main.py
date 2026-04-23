"""EM Command Center — FastAPI entry point.

Starts the server, scheduler, and optionally opens the browser.
"""

import logging
import threading
import webbrowser
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.agent import router as agent_router
from api.agent import set_config
from api.goals import router as goals_router
from api.goals import set_goals_config
from api.metrics import router as metrics_router
from api.metrics import set_metrics_config
from api.reports import router as reports_router
from api.status_board import router as status_board_router
from api.status_board import set_status_board_config
from api.task_actions import router as task_actions_router
from api.tasks import router as tasks_router
from config import CONFIG
from database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and scheduler on startup."""
    init_db()
    logger.info("Database initialized")

    set_config(CONFIG)
    set_goals_config(CONFIG)
    set_metrics_config(CONFIG)
    set_status_board_config(CONFIG)

    from agent.scheduler import setup_scheduler
    scheduler = setup_scheduler(CONFIG)
    logger.info("Scheduler started")

    if CONFIG["AUTO_OPEN_BROWSER"]:
        url = f"http://localhost:{CONFIG['APP_PORT']}"
        threading.Timer(1.5, webbrowser.open, args=[url]).start()
        logger.info(f"Opening browser at {url}")

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(title="EM Command Center", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

app.include_router(tasks_router)
app.include_router(task_actions_router)
app.include_router(agent_router)
app.include_router(goals_router)
app.include_router(reports_router)
app.include_router(metrics_router)
app.include_router(status_board_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Serve the single-page UI."""
    return templates.TemplateResponse("index.html", {"request": request})


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=CONFIG["APP_PORT"],
        reload=False,
    )
