import os
import sys
import uvicorn

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.tools import init_db, seed_historical_data


def start_server(host: str = "127.0.0.1", port: int = 8000, reload: bool = True):
    """
    Initializes database tables and starts the FastAPI web server using uvicorn.
    """
    print("=" * 80)
    print("  MICRO-ENTREPRENEUR GROWTH WORKER - WEB APPLICATION 🇮🇳")
    print(f"  Starting server at: http://{host}:{port}")
    print("=" * 80)
    
    # Initialize SQLite database and baseline memory
    init_db("data/memory.db")
    seed_historical_data("data/memory.db")

    uvicorn.run("src.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from src.state import create_initial_state
        from src.graph import create_multi_agent_graph
        print("Running in CLI mode...")
        app = create_multi_agent_graph()
        initial_state = create_initial_state("data/sales.csv")
        final_state = app.invoke(initial_state)
        print("CLI Execution Completed.")
    else:
        start_server()
