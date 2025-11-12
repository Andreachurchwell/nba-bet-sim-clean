from fastapi import FastAPI
from .routers import games, results, bets


app = FastAPI(title="NBA Betting Simulator (Clean)")

@app.get("/health")
def health():
    return {"status": "ok"}

app.include_router(games.router)
app.include_router(results.router)
app.include_router(bets.router)