"""Optional per-process CPU profiles for the isolated HTTP benchmark."""
import asyncio
import atexit
import json
import os
import re
import signal
import time
from collections import Counter
from contextvars import ContextVar
from multiprocessing.util import Finalize
from pathlib import Path

_started = False
_finish = None
_request_work = ContextVar('stress_request_work', default=None)


def task_origin():
    """Attribute CPU across ORM greenlets and post-response continuations."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    work = _request_work.get()
    if work is not None:
        route = work['scope'].get('route')
        path = getattr(route, 'path', None) or re.sub(r'\d+', ':id', work['scope']['path'])
        phase = ('http' if not work['sent'] else 'after_response') if task is work['owner'] else 'spawned'
        return f'{phase}:{work["scope"]["method"]} {path}'
    coroutine = task.get_coro() if task else None
    code = getattr(coroutine, 'cr_code', None) or getattr(coroutine, 'gi_code', None)
    if code is None:
        return 'event_loop'
    path = code.co_filename.replace('\\', '/')
    return 'task:'+path.split('/backend/')[-1].rsplit('/site-packages/', 1)[-1]+':'+code.co_name


class ProcessTelemetry:
    def __init__(self, app, finish):
        self.app, self.finish = app, finish
        self.profiling = os.getenv('STRESS_PROFILE') == '1'

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and self.profiling:
            work = {'scope': scope, 'owner': asyncio.current_task(), 'sent': False}
            token = _request_work.set(work)

            async def response_sent(message):
                await send(message)
                if message['type'] == 'http.response.body' and not message.get('more_body', False):
                    work['sent'] = True
            try:
                return await self.app(scope, receive, response_sent)
            finally:
                _request_work.reset(token)
        if scope['type'] != 'lifespan':
            return await self.app(scope, receive, send)

        async def sent(message):
            if message['type'] in ('lifespan.shutdown.complete', 'lifespan.shutdown.failed'):
                self.finish()
            await send(message)

        return await self.app(scope, receive, sent)


def record_process(role, metrics):
    global _started, _finish
    if _started:
        return _finish
    _started = True
    folder = Path(os.environ['DATA_DIR']).parent
    profiling = os.getenv('STRESS_PROFILE') == '1'
    samples = Counter()
    origins = Counter()
    cpu_start, wall_start = time.process_time(), time.monotonic()
    previous_handler = None
    if profiling:
        def sample(_signal, frame):
            stack = []
            while frame is not None and len(stack) < 80:
                code = frame.f_code
                stack.append(f'{code.co_filename}:{frame.f_lineno}:{code.co_name}')
                frame = frame.f_back
            # SQLAlchemy runs synchronous ORM work in a greenlet. Its Python
            # backtrace omits the awaiting application frames; retain that
            # task's coroutine chain to attribute CPU to the actual caller.
            try:
                task = asyncio.current_task()
            except RuntimeError:
                task = None
            coroutine = task.get_coro() if task else None
            for _ in range(100):
                code = getattr(coroutine, 'cr_code', None) or getattr(coroutine, 'gi_code', None)
                frame = getattr(coroutine, 'cr_frame', None) or getattr(coroutine, 'gi_frame', None)
                if code is not None and frame is not None:
                    stack.append(f'await:{code.co_filename}:{frame.f_lineno}:{code.co_name}')
                coroutine = getattr(coroutine, 'cr_await', None) or getattr(coroutine, 'gi_yieldfrom', None)
                if coroutine is None:
                    break
            samples[tuple(stack)] += 1
            origins[(int(time.time()), task_origin())] += 1

        previous_handler = signal.signal(signal.SIGPROF, sample)
        signal.setitimer(signal.ITIMER_PROF, .01, .01)
    finished = False

    def finish():
        nonlocal finished
        if finished:
            return
        finished = True
        if profiling:
            signal.setitimer(signal.ITIMER_PROF, 0)
            signal.signal(signal.SIGPROF, previous_handler)
            (folder / f'cpu-{role}-{os.getpid()}.json').write_text(json.dumps({
                'cpu_seconds': time.process_time()-cpu_start, 'wall_seconds': time.monotonic()-wall_start,
                'interval_seconds': .01, 'stacks': [
                    {'frames': frames, 'samples': count} for frames, count in samples.most_common()],
                'origins': [{'second': second, 'origin': origin, 'samples': count}
                    for (second, origin), count in sorted(origins.items())],
            }))
        (folder / f'metrics-{role}-{os.getpid()}.json').write_text(json.dumps(metrics()))

    atexit.register(finish)
    Finalize(None, finish, exitpriority=1)
    _finish = finish
    return finish
