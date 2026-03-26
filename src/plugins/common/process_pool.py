import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
import asyncio
import setproctitle

def init_worker_process(name: str | None = None):
    if name is None:
        setproctitle.setproctitle(f'lunabot-worker')
    else:
        setproctitle.setproctitle(f'lunabot-worker-{name}')

class ProcessPool:
    _process_pools: list['ProcessPool'] = []

    def __init__(self, max_workers: int, name: str | None = None):
        executor = ProcessPoolExecutor(
            max_workers=max_workers, 
            mp_context=mp.get_context('spawn'), 
            initializer=init_worker_process, 
            initargs=(name,)
        )
        self.executor = executor
        ProcessPool._process_pools.append(self)

    def submit(self, fn, *args, **kwargs):
        return asyncio.get_event_loop().run_in_executor(self.executor, fn, *args, **kwargs)
    
    @staticmethod
    def shutdown_all():
        for pool in ProcessPool._process_pools:
            pool.executor.shutdown(wait=False, cancel_futures=True)

def is_main_process():
    return mp.current_process().name == 'MainProcess'

if is_main_process():
    setproctitle.setproctitle('lunabot-main')





