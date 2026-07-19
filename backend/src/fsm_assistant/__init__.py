def main() -> None:
    import os

    import uvicorn

    from .api import create_default_app

    port = int(os.environ.get("FSM_PORT", "8000"))
    uvicorn.run(create_default_app(), host="127.0.0.1", port=port)
