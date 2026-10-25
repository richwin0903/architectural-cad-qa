"""Bounded FIFO admission for a single-worker CAD service; no question data stored here."""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import HTTPException


@dataclass
class Ticket:
    request_id: str
    uid: str
    username: str
    state: str = "queued"
    created: float = field(default_factory=time.monotonic)
    started: float | None = None
    finished: float | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)


class QAQueue:
    def __init__(self, concurrency=2, max_waiting=8, wait_seconds=90):
        if not 1 <= concurrency <= 3 or not 0 <= max_waiting <= 20 or not 0 < wait_seconds <= 90:
            raise ValueError("Invalid bounded QA queue settings")
        self.concurrency = concurrency
        self.max_waiting = max_waiting
        self.wait_seconds = wait_seconds
        self.active = 0
        self.waiting = deque()
        self.tickets = {}
        self.accounts = set()

    def admit(self, request_id, user):
        # Called on the app event loop; no await between checks and mutation.
        now = time.monotonic()
        completed = sorted(
            (t for t in self.tickets.values() if t.finished is not None),
            key=lambda t: t.finished,
        )
        for ticket in completed:
            if ticket.finished < now - 600 or len(self.tickets) >= 256:
                self.tickets.pop(ticket.request_id, None)
        if request_id in self.tickets:
            raise HTTPException(409, "这个请求已提交，请勿重复发送。")
        if user["username"] in self.accounts:
            raise HTTPException(429, "当前账号已有问答正在处理或排队；本次未扣额度。")
        if self.active >= self.concurrency and len(self.waiting) >= self.max_waiting:
            raise HTTPException(429, "问答等待队列已满，请稍后重试；本次未扣额度。")
        ticket = Ticket(request_id, user["uid"], user["username"])
        self.tickets[request_id] = ticket
        self.accounts.add(ticket.username)
        self.waiting.append(ticket)
        self._promote()
        return ticket

    def _promote(self):
        while self.waiting and self.active < self.concurrency:
            ticket = self.waiting.popleft()
            ticket.state = "running"
            ticket.started = time.monotonic()
            self.active += 1
            ticket.ready.set()

    async def wait(self, ticket, require_live):
        while ticket.state == "queued":
            await require_live()
            remaining = self.wait_seconds - (time.monotonic() - ticket.created)
            if remaining <= 0:
                raise HTTPException(504, "排队等待超时，本次未扣额度，请稍后重试。")
            try:
                await asyncio.wait_for(ticket.ready.wait(), timeout=min(1, remaining))
            except TimeoutError:
                continue
        # Promotion and logout/expiry may race; always check again before charging.
        await require_live()

    def finish(self, ticket, state):
        if ticket.finished is not None:
            return
        if ticket.state == "running":
            self.active -= 1
        elif ticket in self.waiting:
            self.waiting.remove(ticket)
        ticket.state = state
        ticket.finished = time.monotonic()
        self.accounts.discard(ticket.username)
        self._promote()

    def status(self, request_id, user):
        ticket = self.tickets.get(request_id)
        if ticket is None or ticket.uid != user["uid"]:
            raise HTTPException(404, "请求不存在或已过期。")
        return {
            "state": ticket.state,
            "position": list(self.waiting).index(ticket) + 1 if ticket in self.waiting else 0,
            "active": self.active,
            "concurrency": self.concurrency,
        }
