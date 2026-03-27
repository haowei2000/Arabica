"""Generic pipeline abstraction.

A Pipeline is an ordered sequence of Stage instances.  Each Stage receives a
context object, transforms it in-place, and returns it.  The context type T
is a plain dataclass defined by the caller — the pipeline itself is unaware of
the domain.

Typical usage::

    @dataclass
    class MyContext:
        value: int
        errors: list[str] = field(default_factory=list)

    class DoubleStage(Stage[MyContext]):
        def process(self, ctx: MyContext) -> MyContext:
            ctx.value *= 2
            return ctx

    pipeline = Pipeline(DoubleStage(), DoubleStage())
    ctx = pipeline.run(MyContext(value=3))
    assert ctx.value == 12
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

T = TypeVar("T")


class Stage(ABC, Generic[T]):
    """A single processing step in a Pipeline.

    Implement :meth:`process` to read and write fields on *ctx*, then return
    it.  Stages should never replace the context object — mutate in place so
    that all stages share the same instance.
    """

    @abstractmethod
    def process(self, ctx: T) -> T:
        """Transform *ctx* and return it."""


class Pipeline(Generic[T]):
    """Ordered chain of :class:`Stage` instances.

    All stages are always executed.  A stage that encounters an error should
    record it in a dedicated field on the context (e.g. ``ctx.errors``) rather
    than raising, so that downstream stages (such as a fallback builder) can
    still run.

    Args:
        *stages: One or more :class:`Stage` instances, executed in order.
    """

    def __init__(self, *stages: Stage[T]) -> None:
        self.stages: list[Stage[T]] = list(stages)

    def run(self, ctx: T) -> T:
        """Run all stages in order and return the final context."""
        for stage in self.stages:
            ctx = stage.process(ctx)
        return ctx
