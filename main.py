from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import SECRET_KEY
from database import init_db
from services import tuya
from services.scheduler import load_schedules, start as start_scheduler, shutdown as stop_scheduler
from routers import auth, admin, devices, schedules


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        tuya.connect()
    except Exception as e:
        print(f"Tuya connection failed: {e}")
    await init_db()
    await load_schedules()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Gecko Home", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(devices.router)
app.include_router(schedules.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=80)
