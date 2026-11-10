"""Linear structures: Stack, Queue and RingBuffer.

* ``Stack`` - last in, first out, on a dynamic array. push / pop / peek are O(1) amortised.
  Used for *undo last swipe*: every keep / skip is pushed, undo pops the most recent one.
* ``Queue`` - first in, first out, on a singly linked list with head and tail pointers, so both
  enqueue and dequeue are O(1) (a Python list would pay O(n) to remove from the front).
  Used as the frontier of breadth-first search in :mod:`app.dsa.graph`.
* ``RingBuffer`` - a fixed-size circular array that overwrites its oldest item when full.
  append is O(1) and memory never grows. Used as the chatbot's short-term memory: only the last
  N messages are sent to the model.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Generic, TypeVar

T = TypeVar("T")


class Stack(Generic[T]):
    """LIFO stack. Space O(n)."""

    __slots__ = ("_items",)

    def __init__(self, items: Iterable[T] = ()) -> None:
        self._items: list[T] = list(items)

    def push(self, item: T) -> None:
        self._items.append(item)

    def pop(self) -> T:
        if not self._items:
            raise IndexError("pop from an empty stack")
        return self._items.pop()

    def peek(self) -> T:
        if not self._items:
            raise IndexError("peek at an empty stack")
        return self._items[-1]

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __iter__(self) -> Iterator[T]:
        """Top of the stack first."""
        return reversed(self._items)


class _Node(Generic[T]):
    __slots__ = ("next", "value")

    def __init__(self, value: T) -> None:
        self.value = value
        self.next: _Node[T] | None = None


class Queue(Generic[T]):
    """FIFO queue on a singly linked list. enqueue / dequeue O(1), space O(n)."""

    __slots__ = ("_head", "_size", "_tail")

    def __init__(self, items: Iterable[T] = ()) -> None:
        self._head: _Node[T] | None = None
        self._tail: _Node[T] | None = None
        self._size = 0
        for item in items:
            self.enqueue(item)

    def enqueue(self, item: T) -> None:
        node = _Node(item)
        if self._tail is None:
            self._head = self._tail = node
        else:
            self._tail.next = node
            self._tail = node
        self._size += 1

    def dequeue(self) -> T:
        if self._head is None:
            raise IndexError("dequeue from an empty queue")
        node = self._head
        self._head = node.next
        if self._head is None:
            self._tail = None
        self._size -= 1
        return node.value

    def peek(self) -> T:
        if self._head is None:
            raise IndexError("peek at an empty queue")
        return self._head.value

    def __len__(self) -> int:
        return self._size

    def __bool__(self) -> bool:
        return self._size > 0

    def __iter__(self) -> Iterator[T]:
        node = self._head
        while node is not None:
            yield node.value
            node = node.next


class RingBuffer(Generic[T]):
    """Fixed-capacity circular buffer; appending to a full buffer drops the oldest item.

    append O(1), iteration O(capacity), space O(capacity).
    """

    __slots__ = ("_data", "_size", "_start", "capacity")

    def __init__(self, capacity: int, items: Iterable[T] = ()) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._data: list[T | None] = [None] * capacity
        self._start = 0  # index of the oldest item
        self._size = 0
        for item in items:
            self.append(item)

    def append(self, item: T) -> T | None:
        """Add ``item``; returns the item that was overwritten, if any."""
        end = (self._start + self._size) % self.capacity
        dropped = None
        if self._size == self.capacity:
            dropped = self._data[self._start]
            self._start = (self._start + 1) % self.capacity
        else:
            self._size += 1
        self._data[end] = item
        return dropped

    def __len__(self) -> int:
        return self._size

    def __iter__(self) -> Iterator[T]:
        """Oldest item first."""
        for offset in range(self._size):
            yield self._data[(self._start + offset) % self.capacity]  # type: ignore[misc]

    def to_list(self) -> list[T]:
        return list(self)
