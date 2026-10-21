"""
Combined runner — starts GoldBot + QuiverWatcher in separate threads.
Both restart automatically if they crash.
"""

import threading, time, logging, os, sys

# Allow imports from this directory
sys.path.insert(0, os.path.dirname(__file__))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(threadName)s] %(message)s")
log = logging.getLogger(__name__)

QUIVER_API_KEY = os.getenv("QUIVER_API_KEY")


def _run_forever(name: str, fn, restart_delay: int = 30):
    while True:
        try:
            log.info("▶ %s starting", name)
            fn()
        except Exception as e:
            log.error("✖ %s crashed: %s — restarting in %ds", name, e, restart_delay)
            time.sleep(restart_delay)


def main():
    from gold_signal_bot import run as gold_run

    bots = [
        threading.Thread(
            target=_run_forever,
            args=("GoldBot", gold_run),
            name="GoldBot",
            daemon=True
        ),
    ]

    if QUIVER_API_KEY:
        from quiver_watcher import run as quiver_run
        bots.append(threading.Thread(
            target=_run_forever,
            args=("QuiverWatcher", quiver_run),
            name="QuiverWatcher",
            daemon=True
        ))
        log.info("QuiverWatcher enabled")
    else:
        log.warning("QUIVER_API_KEY not set — QuiverWatcher disabled")

    for t in bots:
        t.start()

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
