"""Cancellation-safe ownership of synchronous workers."""

import asyncio


async def await_blocking(call, *args, **kwargs):
    """Wait for a blocking worker to actually exit when its caller is cancelled."""
    worker = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(worker)
        except Exception:  # noqa: BLE001
            pass
        raise


async def await_committing(call, *args, **kwargs):
    """A state commit cannot be rolled back merely because HTTP disconnects."""
    worker = asyncio.create_task(asyncio.to_thread(call, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        return await asyncio.shield(worker)
