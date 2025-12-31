from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


# 创建一个简单的测试应用，不包含数据库初始化
@pytest.fixture
def test_app():
    app = FastAPI()

    @app.get("/")
    def read_root():
        return {"Hello": "World"}

    return app

@pytest.fixture
def test_client(test_app):
    with TestClient(test_app) as client:
        yield client
