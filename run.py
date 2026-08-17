"""Local entry point. Reads .env, then serves on http://localhost:8000"""
import os
from pathlib import Path

env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

if __name__ == "__main__":
    import uvicorn
    print("\n  Lingua → http://localhost:8000\n")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
