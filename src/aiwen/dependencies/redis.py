from fastapi import FastAPI
from redis import Redis


def get_global_redis(app: FastAPI) -> Redis:
    """
    从 app.state 获取全局 Redis 实例
    """
    if not hasattr(app.state, 'redis') or app.state.redis is None:
        raise RuntimeError("Redis instance not initialized in app.state")
    return app.state.redis
