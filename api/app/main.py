"""FloodSafe v2 API.

Serves the endpoints described in contracts/. For now only GET /health is
implemented; it returns {"status": "ok"} so the app and deploys can check the
server is up.

Inputs: none yet. Outputs: JSON over HTTP.
Data source: none yet (each endpoint names its source when it is added).
Run from api/: uvicorn app.main:app --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="FloodSafe v2")

# The React app runs on another laptop and origin, so allow every origin.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"status": "ok"}
