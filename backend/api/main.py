from fastapi import FastAPI
from .routers import games, results, bets, seasons
from .core.config import DEFAULT_SEASON
from .core import config
from .services.betting import BetError
from fastapi.responses import JSONResponse


app = FastAPI(title="NBA Betting Simulator (Clean)")


@app.exception_handler(BetError)
async def bet_error(request, exc):
    return JSONResponse(status_code=exc.status, content={"detail": {
        "season_id": request.query_params.get("season", DEFAULT_SEASON), "message": str(exc)}})


@app.exception_handler(OSError)
async def storage_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": {
        "season_id": request.query_params.get("season", DEFAULT_SEASON),
        "message": "Storage is unavailable. Retry the same confirmation; do not reset the wallet."}})

@app.get("/health")
def health():
    return {"status": "ok", "season_id": DEFAULT_SEASON, "read_only": config.MAINTENANCE_READ_ONLY}

app.include_router(games.router)
app.include_router(results.router)
app.include_router(bets.router)
app.include_router(seasons.router)
