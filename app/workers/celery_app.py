from celery import Celery

from app.core.config import settings

celery_app = Celery("lygo", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_routes = {
    "app.workers.ingest.*": {"queue": "ingest"},
    "app.workers.ai.*": {"queue": "ai"},
    "app.workers.notify.*": {"queue": "notify"},
    "app.workers.sms.*": {"queue": "sms"},
}
celery_app.conf.task_acks_late = True
celery_app.conf.worker_prefetch_multiplier = 1
