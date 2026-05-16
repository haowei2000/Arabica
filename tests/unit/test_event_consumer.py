from typing import Any, cast

import pytest

from structure.services.events.event_consumer import EventConsumer


class _FakeRedis:
    async def xread(
        self, **kwargs: object
    ) -> list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]]:
        return [
            (
                b"run:test:events",
                [
                    (
                        b"42-0",
                        {
                            b"event_type": b"agent.token",
                            b"payload": b'{"token":"hello"}',
                            b"sequence": b"7",
                        },
                    )
                ],
            )
        ]


@pytest.mark.asyncio
async def test_subscribe_run_includes_redis_stream_id():
    consumer = EventConsumer(cast(Any, _FakeRedis()))
    stream = consumer.subscribe_run("test", last_id="0-0", timeout_ms=1)

    event = await anext(stream)
    await stream.aclose()

    assert event["stream_id"] == "42-0"
    assert event["sequence"] == 7
    assert event["payload"]["token"] == "hello"
