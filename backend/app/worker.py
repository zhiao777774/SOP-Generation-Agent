from __future__ import annotations

import multiprocessing

from redis import Redis
from rq import Queue, Worker

from backend.app.core.config import load_config
from backend.app.services.artifact_service import ArtifactService
from backend.app.services.task_queue import TaskQueue


def main() -> None:
    config = load_config()
    artifacts = ArtifactService(config.data_root)
    TaskQueue(config, artifacts).reconcile_interrupted_jobs()

    if config.worker_count <= 1:
        _run_worker()
        return

    processes = [
        multiprocessing.Process(target=_run_worker, args=(index == 0,), name=f"sop-rq-worker-{index + 1}")
        for index in range(config.worker_count)
    ]
    for process in processes:
        process.start()
    try:
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        for process in processes:
            process.terminate()
        for process in processes:
            process.join()


def _run_worker(with_scheduler: bool = True) -> None:
    config = load_config()
    connection = Redis.from_url(config.queue_redis_url)
    queues = [Queue(name, connection=connection) for name in config.worker_queues]
    Worker(queues, connection=connection).work(with_scheduler=with_scheduler)


if __name__ == "__main__":
    main()
