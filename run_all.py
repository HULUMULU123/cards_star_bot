import threading

from api_server import run_api_server
from bot import main


def start_api_thread():
    thread = threading.Thread(target=run_api_server, daemon=True)
    thread.start()
    return thread


if __name__ == "__main__":
    start_api_thread()
    main()
