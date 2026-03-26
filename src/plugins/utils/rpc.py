from .utils import *
import aiorpcx

_rpc_service_tokens: dict[str, str | ConfigItem] = {}
_rpc_handlers: dict[str, Callable] = {}

def rpc_method(service_name: str, method_name: str):
    """
    装饰器，用于注册RPC方法处理程序。
    """
    def decorator(func):
        _rpc_handlers[service_name + "." + method_name] = func
        return func
    return decorator


class RpcSession(aiorpcx.RPCSession):
    def __init__(
        self, 
        name: str, 
        logger: Logger, 
        *args, 
        on_connect: Callable = None,
        on_disconnect: Callable = None,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.id = str(self.remote_address())
        self.name = name
        self._logger = logger
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.processing_timeout = 300.0
        self.sent_request_timeout = 60.0

        self.on_connect(self)
        logger.info(f'{self.name}RPC服务的客户端 {self.id} 连接成功')

    async def connection_lost(self):
        await super().connection_lost()
        self.on_disconnect(self)
        self._logger.info(f'{self.name}RPC服务的客户端 {self.id} 断开连接')

    async def handle_request(self, request):
        self._logger.debug(f'收到{self.name}RPC服务的客户端 {self.id} 的请求 {request.method}')
        handler_fn = _rpc_handlers.get(self.name + "." + request.method)
        token = get_cfg_or_value(_rpc_service_tokens[self.name])

        if not request.args or request.args[0] != token:
            self._logger.warning(f'{self.name}RPC服务的客户端 {self.id} 提供了无效或缺失的令牌')
            await asyncio.sleep(1.0)
            raise aiorpcx.RPCError(-32000, 'Invalid or missing token')

        args = request.args[1:]
        request.args = [self.id] + args
        resp = await aiorpcx.handler_invocation(handler_fn, request)()
        self._logger.debug(f'{self.name}RPC服务的客户端 {self.id} 的请求 {request.method} {args} 返回: {resp}')
        return resp
    

def get_session_factory(
    name: str, 
    logger: Logger, 
    on_connect: Callable = None,
    on_disconnect: Callable = None,
):
    def factory(*args, **kwargs):
        return RpcSession(name, logger, *args, on_connect=on_connect, on_disconnect=on_disconnect, **kwargs)
    return factory


@staticmethod
def start_rpc_service(
    host: str, 
    port: int,
    name: str, 
    token: str | ConfigItem,
    logger: Logger, 
    on_connect: Callable = None,
    on_disconnect: Callable = None, 
):
    """
    启动RPC服务。
    Parameters:
        name (str): 服务名称。
        token (str | ConfigItem): 用于身份验证的令牌，客户端请求时需要作为第一个参数输入
        logger (Logger): 用于输出日志的Logger实例。
        on_connect (Callable): 客户端连接时调用的回调函数，接受一个参数（会话实例）。
        on_disconnect (Callable): 客户端断开连接时调用的回调函数，接受一个参数（会话实例）。
        host (str): 服务器主机地址。
        port (int): 服务器端口号。
    """
    _rpc_service_tokens[name] = token
    @async_task(f'{name}RPC服务', logger)
    async def _():
        try:
            async with aiorpcx.serve_ws(
                get_session_factory(name, logger, on_connect, on_disconnect), 
            host, port):
                logger.info(f'{name}RPC服务已启动 ws://{host}:{port}')
                await asyncio.sleep(1e9)
        except asyncio.exceptions.CancelledError:
            logger.info(f'{name}RPC服务已关闭')