from fastapi import FastAPI

from ingest.routes import router as ingest_router

app = FastAPI(title="TokenLens ingest API")
app.include_router(ingest_router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


def main() -> None:
    print("Hello from backend!")


if __name__ == "__main__":
    main()
