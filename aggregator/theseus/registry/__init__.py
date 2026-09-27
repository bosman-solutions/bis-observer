"""
Entity registry — stable identity for every watched thing, plus stored links.

Wire in:
    from .registry import init_app as init_registry
    init_registry(app, prom_url=..., ext_url=..., prom_uid=callable, db_path=...)
"""
import logging
import os
import threading
import time

from . import db
from .api import bp, close_conn
from .discovery import discover

logger = logging.getLogger(__name__)


def _loop(prom_url, db_path, interval):
    while True:
        try:
            conn = db.connect(db_path)
            try:
                logger.info("registry discovery: %s", discover(conn, prom_url))
            finally:
                conn.close()
        except Exception as e:  # keep the loop alive; next tick retries
            logger.warning("registry discovery failed: %s", e)
        time.sleep(interval)


def init_app(app, *, prom_url, ext_url, prom_uid, db_path, interval=300, stale_after=900):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    db.connect(db_path).close()  # create schema up front; fail at boot, not first request
    app.config["REGISTRY"] = {
        "prom_url": prom_url, "ext_url": ext_url, "prom_uid": prom_uid,
        "db_path": db_path, "stale_after": stale_after,
    }
    app.teardown_appcontext(close_conn)
    app.register_blueprint(bp)
    if interval > 0:
        threading.Thread(target=_loop, args=(prom_url, db_path, interval),
                         daemon=True, name="registry-discovery").start()
        logger.info("registry discovery every %ss -> %s", interval, db_path)
