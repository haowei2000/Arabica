from aiwen.utils.json_utils import dumps as json_dumps


def sse(event: str, data) -> str:
    """
    Create a Server-Sent Event (SSE) formatted string.

    Args:
        event: Event name
        data: Event data (can include UUID, datetime, etc.)

    Returns:
        SSE formatted string
    """
    return f"data: {json_dumps({'event': event, 'data': data})}\n\n"
