"""Celery application and the Beat schedule.

Three periodic jobs:
  * ``flush_event_buffer``     every 10s — Redis buffer -> Postgres, in bulk
  * ``reconcile_vector_store`` every 15m — re-push any product whose Qdrant copy
                                           is stale (the dual-write safety net)
  * ``send_daily_digest``      once a day — proactive personalised email
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "smartreco",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    task_time_limit=300,
    task_soft_time_limit=240,
    broker_connection_retry_on_startup=True,
)

celery_app.conf.beat_schedule = {
    "flush-event-buffer": {
        "task": "smartreco.flush_event_buffer",
        "schedule": 10.0,
        "options": {"expires": 30},
    },
    "reconcile-vector-store": {
        "task": "smartreco.reconcile_vector_store",
        "schedule": 900.0,
        "options": {"expires": 600},
    },
    "daily-digest": {
        "task": "smartreco.send_daily_digest",
        "schedule": crontab(hour=settings.digest_hour, minute=settings.digest_minute),
        "options": {"expires": 3600},
    },
}


@worker_process_init.connect
def _init_worker(**_kwargs) -> None:
    """Turn on LangSmith tracing inside each worker process."""
    from app.llm.mesh import configure_tracing

    configure_tracing()
    logger.info("SmartReco worker ready (chat model: %s)", settings.mesh_chat_model)
