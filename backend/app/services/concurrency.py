from __future__ import annotations

import os
import time
from contextlib import contextmanager
from uuid import uuid4

from redis import Redis
from redis.exceptions import RedisError


_STAGE_ENV = {
    "analyze": ("SOP_MAX_CONCURRENT_ANALYZE", 3),
    "generate": ("SOP_MAX_CONCURRENT_GENERATE", 2),
    "ocr": ("SOP_MAX_CONCURRENT_OCR", 2),
    "embedding": ("SOP_MAX_CONCURRENT_EMBEDDING", 4),
    "llm": ("SOP_MAX_CONCURRENT_LLM", 2),
    "vlm": ("SOP_MAX_CONCURRENT_VLM", 1),
}

_ACQUIRE_SCRIPT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1] - ARGV[3])
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[2]) then
  redis.call('ZADD', KEYS[1], ARGV[1], ARGV[4])
  redis.call('EXPIRE', KEYS[1], ARGV[3])
  return 1
end
return 0
"""


def limited_post(stage: str, post_func, *args, **kwargs):
    with stage_limit(stage):
        return post_func(*args, **kwargs)


@contextmanager
def stage_limit(stage: str):
    token = _acquire(stage)
    try:
        yield
    finally:
        if token:
            _release(stage, token)


def _acquire(stage: str) -> str:
    env_name, default = _STAGE_ENV.get(stage, (f"SOP_MAX_CONCURRENT_{stage.upper()}", 1))
    limit = max(_int_env(env_name, default), 1)
    redis_url = os.getenv("SOP_QUEUE_REDIS_URL", "redis://redis:6379/0")
    key = f"sop:limit:{stage}"
    token = uuid4().hex
    ttl_seconds = 900
    client = Redis.from_url(redis_url, socket_connect_timeout=0.2, socket_timeout=0.5)
    while True:
        try:
            now = time.time()
            acquired = client.eval(_ACQUIRE_SCRIPT, 1, key, now, limit, ttl_seconds, token)
            if acquired:
                return token
        except RedisError:
            return ""
        time.sleep(0.2)


def _release(stage: str, token: str) -> None:
    redis_url = os.getenv("SOP_QUEUE_REDIS_URL", "redis://redis:6379/0")
    try:
        Redis.from_url(redis_url, socket_connect_timeout=0.2, socket_timeout=0.5).zrem(
            f"sop:limit:{stage}",
            token,
        )
    except RedisError:
        return


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
