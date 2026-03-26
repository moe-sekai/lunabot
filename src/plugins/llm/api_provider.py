from ..utils import *
from openai import AsyncOpenAI


logger = get_logger("Llm")
file_db = get_file_db("data/llm/db.json", logger)


@dataclass
class LlmModel:
    """
    LLM模型
    """
    name: str
    input_pricing: float = 0.
    output_pricing: float = 0.
    max_token: int = 128000
    is_multimodal: bool = False
    model_id: Optional[str] = None
    image_response: bool = False
    allow_online: bool = False
    provider: "ApiProvider" = None

    data: dict = field(default_factory=dict)
    client_kwargs: dict = field(default_factory=dict)
    extra_body: dict = field(default_factory=dict)

    def calc_price(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens * self.input_pricing + output_tokens * self.output_pricing
    
    def get_model_id(self) -> str:
        return self.model_id or self.name

    def get_price_unit(self) -> str:
        return self.provider.get_price_unit()
    
    def get_full_name(self) -> str:
        return f"{self.provider.name}:{self.name}"



@dataclass
class ApiProvider:
    """
    兼容OpenAI请求的API供应方，包含多个LLM模型

    子类需要重写以下函数:
    - get_client() 获取openai的API客户端
    - sync_quota() 同步剩余额度
    """

    def __init__(
        self, 
        name: str, 
        code: str,
    ):
        self.name = name
        self.code = code

        self.config = Config(f'llm.providers.{name}')
        self.models: List[LlmModel] = []
        self.models_mtime = None

        self.cur_query_ts = 0
        self.cur_sec_query_count = 0

        self.local_quota_key = f"api_provider_{name}_local_quota"
        self.last_quota_sync_time = datetime.now()


    def get_qps_limit(self) -> int:
        return self.config.get('qps_limit')
    
    def get_quota_sync_interval_sec(self) -> int:
        return parse_cfg_num(self.config.get('quota_sync_interval_sec'))
    
    def get_price_unit(self) -> str:
        return self.config.get('price_unit')

    def get_api_key(self) -> str:
        return self.config.get('api_key')
    
    def get_base_url(self) -> str:
        return self.config.get('base_url')


    def update_models(self):
        mtime = self.config.mtime()
        if self.models_mtime != mtime:
            def parse_price(d, k):
                if not isinstance(d.get(k), str):
                    return
                if '/' in d[k]:
                    nums = d[k].split('/', 1)
                else:
                    nums = [d[k], '1']
                nums = [float(num) for num in nums]
                d[k] = nums[0] / nums[1]
            self.models = []
            for model_config in self.config.get('models', []):
                parse_price(model_config, 'input_pricing')
                parse_price(model_config, 'output_pricing')
                self.models.append(LlmModel(**model_config))
            for model in self.models:
                model.provider = self
            self.models_mtime = mtime
            logger.info(f"API供应方 {self.name} 模型列表更新成功 (共 {len(self.models)} 个模型)")
        
    def check_qps_limit(self):
        """
        检查QPS限制，超出限制则抛出异常
        """
        now_ts = int(datetime.now().timestamp())
        if now_ts > self.cur_query_ts:
            self.cur_query_ts = now_ts
            self.cur_sec_query_count = 0
        qps_limit = self.get_qps_limit()
        if self.cur_sec_query_count >= qps_limit:
            logger.warning(f"API供应方 {self.name} QPS限制 {qps_limit} 已超出")
            raise Exception(f"API供应方 {self.name} QPS限制 {qps_limit} 已超出，请稍后再试")
        self.cur_sec_query_count += 1

    async def aupdate_quota(self, delta: float) -> float:
        """
        异步更新剩余额度，返回更新后的剩余额度
        """
        local_quota = file_db.get(self.local_quota_key, 0.0)
        if not isinstance(local_quota, (int, float)):
            local_quota = 0.0
        last_quota = local_quota
        local_quota += delta
        file_db.set(self.local_quota_key, local_quota)
        new_quota = await self.aget_current_quota()
        price_unit = self.get_price_unit()
        logger.info(f"API供应方 {self.name} 更新剩余额度成功: {last_quota}{price_unit} -> {new_quota}{price_unit}")
        return new_quota

    async def aget_current_quota(self) -> float:
        """
        异步获取当前剩余额度
        """
        if (datetime.now() - self.last_quota_sync_time).total_seconds() > self.get_quota_sync_interval_sec():
            try:
                new_quota = await self.sync_quota()
                if new_quota is not None:
                    file_db.set(self.local_quota_key, new_quota)
                    logger.info(f"API供应方 {self.name} 同步剩余额度成功: {new_quota}{self.get_price_unit()}")
            except:
                logger.print_exc(f"API供应方 {self.name} 同步剩余额度失败")
            self.last_quota_sync_time = datetime.now()
        return file_db.get(self.local_quota_key, 0.0)


    def get_client(self) -> AsyncOpenAI:
        """
        获取API客户端，返回OpenAPI异步客户端，由子类实现
        """
        raise NotImplementedError()

    async def sync_quota(self):
        """
        异步的方式同步剩余额度，返回同步后的额度，由子类实现
        返回None表示不支持同步额度
        """
        raise NotImplementedError()


    

    
