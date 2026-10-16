import os
import sys

if __name__ == "__main__":
    if sys.argv[1:2] == ["--asr-worker"]:
        from backend.asr_worker import main
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        raise SystemExit(main())
    import uvicorn
    from backend.main import app
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("VMM_PORT", "8787")), reload=False, loop="asyncio", http="h11", ws="none")
