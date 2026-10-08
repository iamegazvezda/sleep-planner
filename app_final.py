"""Sleep Planner: расчёты по упрощённой модели сна, без хранения пользовательских данных."""
from fastapi import FastAPI

def create_app():
    application = FastAPI(title="Sleep Planner", version="1.0.0")

    @application.get("/")
    def root():
        return {"service": "Sleep Planner", "author": "Alex Kryuchatov", "docs": "/docs"}

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/version")
    def version():
        return {"version": "1.0.0"}

    return application

app = create_app()
