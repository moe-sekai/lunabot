from ...llm.api_provider import *
from openai import AsyncOpenAI
import os


class AiyydsApiProvider(ApiProvider):
    def __init__(self):
        super().__init__(name="ai-yyds", code="ay")
        self.cookies_save_path = "data/llm/aiyyds_quota_check_cookies.json"

    def get_client(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=self.get_api_key(),
            base_url=self.get_base_url(),
        )
        
    async def _update_sync_quota_web_cookies(self):
        try:
            async with PlaywrightPage() as page:
                login_url = self.config.get("login_url")
                await page.goto(login_url, wait_until="load")
                await page.fill('input[name="username"]', self.config.get("username"))
                await page.fill('input[name="password"]', self.config.get("password"))
                await page.click('xpath=//button[text()="登录"]')
                await page.wait_for_timeout(1000)
                all_cookies = await page.context.cookies()
                
                target_cookies = None
                for c in all_cookies:
                    if c['name'] == 'session':
                        target_cookies = { "session": c['value'] }
                        break
    
                if not target_cookies:
                    raise Exception("No aiyyds cookie found")
                os.makedirs(os.path.dirname(self.cookies_save_path), exist_ok=True)
                dump_json(target_cookies, self.cookies_save_path)
                return target_cookies

        except Exception as e:
            utils_logger.error(f"获取cookies失败: {e}")  
            return None

    async def sync_quota(self):
        api_url = self.config.get("quota_url")
        while True:
            # 读取本地cookies，如果读取失败则重新获取
            try:
                cookies = load_json(self.cookies_save_path)
            except:
                logger.warning("未找到本地AI-YYDScookies文件, 重新获取")
                cookies = await self._update_sync_quota_web_cookies()

            # 查询额度
            async with get_client_session().get(api_url) as response:
                if response.status == 401:
                    logger.info("AI-YYDS登录失效, 删除并重新获取cookies")
                    os.remove(self.cookies_save_path)
                    continue
                if response.status != 200:
                    raise Exception(f"请求失败: {response.status}")
                res = await response.json()
                quota = res['data']['quota'] / 500000
                return quota



