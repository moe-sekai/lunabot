from .config import *
from datetime import datetime, timedelta
import traceback


LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR']

# 日志输出
class Logger:
    def __init__(self, name):
        self.name = name

    def log(self, msg, flush=True, end='\n', level='INFO', real_level=None):
        real_level = real_level or level
        if real_level not in LOG_LEVELS:
            raise Exception(f'未知日志等级 {real_level}')
        log_level = global_config.get('log_level').upper()
        if LOG_LEVELS.index(real_level) < LOG_LEVELS.index(log_level):
            return
        time = datetime.now().strftime("%m-%d %H:%M:%S.%f")[:-3]
        print(f'{time} {level} [{self.name}] {msg}', flush=flush, end=end)
    
    def debug(self, msg, flush=True, end='\n'):
        self.log(msg, flush=flush, end=end, level='DEBUG')
    
    def info(self, msg, flush=True, end='\n'):
        self.log(msg, flush=flush, end=end, level='INFO')
    
    def warning(self, msg, flush=True, end='\n'):
        self.log(msg, flush=flush, end=end, level='WARNING')

    def error(self, msg, flush=True, end='\n'):
        self.log(msg, flush=flush, end=end, level='ERROR')

    def profile(self, msg, flush=True, end='\n'):
        self.log(msg, flush=flush, end=end, level='PROFILE', real_level='INFO')

    def print_exc(self, msg=None):
        self.error(msg)
        time = datetime.now().strftime("%m-%d %H:%M:%S.%f")[:-3]
        print(f'{time} ERROR [{self.name}] ', flush=True, end='')
        traceback.print_exc()


class NumLimitLogger(Logger):
    """
    发送一定次数后会停止发送的Logger
    """
    _last_log_time_and_count: Dict[str, tuple[datetime, int]] = {}

    def __init__(
        self, 
        name: str, 
        key: str, 
        limit: int = 5, 
        recover_after: timedelta = timedelta(minutes=10),
    ):
        super().__init__(name)
        self.key = f"{name}__{key}"
        self.limit = limit
        self.recover_after = recover_after

    def _check_can_log(self, update: bool) -> str:
        """
        检查是否可以发送日志，并更新最后发送时间
        返回 'ok' 表示可以发送，'limit' 表示达到限制，'final' 表示最后一次发送
        """
        last_time, last_count = self._last_log_time_and_count.get(self.key, (None, 0))
        if self.recover_after is not None and last_time is not None \
            and datetime.now() - last_time > self.recover_after:
            # 如果超过恢复时间，则重置计数
            last_time, last_count = None, 0
            self._last_log_time_and_count.pop(self.key, None)
        if update:
            self._last_log_time_and_count[self.key] = (datetime.now(), last_count + 1)
        if last_count > self.limit:
            return 'limit'
        if last_count == self.limit:
            return 'final'
        return 'ok'

    def recover(self, verbose=True):
        """
        立刻恢复日志发送
        """
        can_log = self._check_can_log(update=False)
        if can_log == 'limit':
            self._last_log_time_and_count.pop(self.key, None)
            if verbose:
                super().info(f"{self.key} 日志发送限制已恢复")

    def log(self, msg, flush=True, end='\n', level='INFO'):
        can_log = self._check_can_log(update=True)
        if can_log == 'limit': return
        if can_log == 'final':
            msg += f" (已达到发送限制{self.limit}次，暂停发送)"
        super().log(msg, flush=flush, end=end, level=level)
    
    def print_exc(self, msg=None):
        can_log = self._check_can_log(update=True)
        if can_log == 'limit': return
        if can_log == 'final':
            msg += f" (已达到发送限制{self.limit}次，暂停发送)"
        super().print_exc(msg)


_loggers: Dict[str, Logger] = {}
def get_logger(name: str) -> Logger:
    global _loggers
    if name not in _loggers:
        _loggers[name] = Logger(name)
    return _loggers[name]

