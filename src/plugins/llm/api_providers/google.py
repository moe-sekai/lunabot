from ...llm.api_provider import *
import aiohttp
import asyncio
import json
import base64
from io import BytesIO
from PIL import Image
from typing import List, Dict, Any, Optional

# 定义常量
HARM_CATEGORIES = [
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
    "HARM_CATEGORY_CIVIC_INTEGRITY",
]

class GenaiCompletions:
    def __init__(self, api_key: str, http_options: dict):
        self.api_key = api_key
        self.http_options = http_options
        
        # 1. 处理 Base URL
        # 默认为 Google 官方地址，如果 config 中有配置则覆盖
        base_url = self.http_options.get("base_url")
        if not base_url:
            base_url = "https://generativelanguage.googleapis.com"
        self.base_url = base_url.rstrip("/")
        
        # 2. 处理 API Version
        self.api_version = self.http_options.get("api_version") or "v1beta"

        # 3. 预先构建安全设置
        self.safety_settings = [
            {"category": category, "threshold": "BLOCK_NONE"}
            for category in HARM_CATEGORIES
        ]

    def _get_endpoint(self, model: str, task: str = "generateContent"):
        """
        构建 API 请求地址
        格式通常为: BASE_URL/VERSION/models/MODEL:TASK
        """
        # 防止模型名称中重复包含 'models/' 前缀
        if model.startswith("models/"):
            model = model.replace("models/", "", 1)
            
        # 检查 base_url 是否已经包含了 version (某些反代地址可能会这样配置)
        url = self.base_url
        if f"/{self.api_version}" not in url:
             url = f"{url}/{self.api_version}"
        
        # 拼接 models 路径
        # 注意：如果 base_url 是类似 .../v1beta/models 结尾的，需要适配，但标准用法通常只到域名或版本
        if "/models" not in url:
            url = f"{url}/models"
            
        return f"{url}/{model}:{task}?key={self.api_key}"

    async def create(
        self,
        model: str, 
        messages: list, 
        extra_body: dict = None, 
        max_tokens: int = None,
        thinking_config: dict = {}
    ):
        extra_body = extra_body or {}
        image_response = extra_body.get("image_response", False)

        # --- 1. 构建请求 Payload ---
        system_instruction = None
        contents = []

        for item in messages:
            role = item['role']
            content = item['content']
            
            if role == "system_prompt":
                system_instruction = {"parts": [{"text": str(content)}]}
            else:
                api_role = "model" if role == "assistant" else "user"
                parts = []
                
                if isinstance(content, str):
                    parts.append({"text": content})
                elif isinstance(content, list):
                    for part in content:
                        if part['type'] == "text":
                            parts.append({"text": part['text']})
                        elif part['type'] == "image_url":
                            image_url = part['image_url']['url']
                            if image_url.startswith("data:"):
                                header, b64_data = image_url.split(",", 1)
                                mime_type = header.split(":")[1].split(";")[0]
                                parts.append({
                                    "inline_data": {
                                        "mime_type": mime_type,
                                        "data": b64_data
                                    }
                                })
                
                if parts:
                    contents.append({"role": api_role, "parts": parts})

        generation_config = {
            "maxOutputTokens": max_tokens,
            "responseModalities": ['IMAGE', 'TEXT'] if image_response else ['TEXT'],
        }
        
        if thinking_config:
            generation_config["thinkingConfig"] = thinking_config

        payload = {
            "contents": contents,
            "safetySettings": self.safety_settings,
            "generationConfig": generation_config
        }
        
        if system_instruction:
            payload["system_instruction"] = system_instruction

        # --- 2. 发起异步请求 ---
        url = self._get_endpoint(model, "generateContent")
        
        proxy = self.http_options.get('proxy')
        timeout = aiohttp.ClientTimeout(total=self.http_options.get('timeout', 300))

        try:
            async with get_client_session().post(
                url, 
                json=payload, 
                proxy=proxy, 
                timeout=timeout
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    # 抛出包含 URL 的异常以便调试
                    raise Exception(f"Google API Error {response.status} at {url}: {error_text}")
                
                resp_json = await response.json()
        except Exception as e:
            # 捕获网络层面的错误
            raise Exception(f"请求 Google API 失败: {str(e)}")

        # --- 3. 解析响应 ---
        if "candidates" not in resp_json or not resp_json["candidates"]:
            prompt_feedback = resp_json.get("promptFeedback", {})
            block_reason = prompt_feedback.get("blockReason")
            if block_reason:
                raise Exception(f"生成被屏蔽，原因: {block_reason}")
            raise Exception("生成失败，API 返回了空候选项")

        candidate = resp_json["candidates"][0]
        content_parts = candidate.get("content", {}).get("parts", [])
        
        usage_meta = resp_json.get("usageMetadata", {})
        prompt_tokens = usage_meta.get("promptTokenCount", 0)
        completion_tokens = usage_meta.get("candidatesTokenCount", 0)

        result = { 
            "choices": [{
                "message": {
                    "content": [],
                    "reasoning_content": "",
                }
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }
        }

        if not content_parts:
            finish_reason = candidate.get("finishReason")
            if finish_reason and finish_reason != "STOP":
                 raise Exception(f"生成结束异常，原因: {finish_reason}")
            raise Exception("返回内容为空")

        for part in content_parts:
            is_thought = part.get("thought", False)
            text_val = part.get("text")
            inline_data = part.get("inlineData")

            if is_thought:
                if text_val:
                    result["choices"][0]["message"]['reasoning_content'] += text_val
            else:
                if text_val is not None:
                    result["choices"][0]["message"]['content'].append(text_val)
                elif inline_data is not None:
                    img_data = base64.b64decode(inline_data["data"])
                    img = Image.open(BytesIO(img_data))
                    result["choices"][0]["message"]['content'].append(img)
        
        return result


class GenaiEmbeddings:
    def __init__(self, api_key: str, http_options: dict):
        self.api_key = api_key
        self.http_options = http_options
        
        base_url = self.http_options.get("base_url")
        if not base_url:
            base_url = "https://generativelanguage.googleapis.com"
        self.base_url = base_url.rstrip("/")
        
        self.api_version = self.http_options.get("api_version") or "v1beta"

    def _get_endpoint(self, model: str):
        if model.startswith("models/"):
            model = model.replace("models/", "", 1)
        
        url = self.base_url
        if f"/{self.api_version}" not in url:
             url = f"{url}/{self.api_version}"
        
        if "/models" not in url:
            url = f"{url}/models"
            
        return f"{url}/{model}:batchEmbedContents?key={self.api_key}"

    async def create(self, input: List[str], model: str, encoding_format: str = 'float'):
        url = self._get_endpoint(model)
        
        payload = {
            "requests": [
                {
                    "content": {
                        "parts": [{"text": text}]
                    },
                    "model": f"models/{model}" if not model.startswith("models/") else model
                } for text in input
            ]
        }

        proxy = self.http_options.get('proxy')
        timeout = aiohttp.ClientTimeout(total=self.http_options.get('timeout', 60))

        async with get_client_session().post(url, json=payload, proxy=proxy, timeout=timeout) as response:
            if response.status != 200:
                error_text = await response.text()
                raise Exception(f"Google Embeddings API Error {response.status} at {url}: {error_text}")
            data = await response.json()

        embeddings = []
        if "embeddings" in data:
            for item in data["embeddings"]:
                embeddings.append(item.get("values", []))
        return embeddings


class GenaiChat:
    def __init__(self, api_key: str, http_options: dict):
        self.completions = GenaiCompletions(api_key, http_options)


class GenaiAsyncClient:
    def __init__(self, http_options: dict, api_key: str):
        self.chat = GenaiChat(api_key=api_key, http_options=http_options)
        self.embeddings = GenaiEmbeddings(api_key=api_key, http_options=http_options)


class GoogleApiProvider(ApiProvider):
    def __init__(self):
        super().__init__(name="google", code="gg")

    def get_client(self) -> GenaiAsyncClient:
        return GenaiAsyncClient(
            http_options=self.config.get('http_options', {}),
            api_key=self.get_api_key(),
        )
    
    async def sync_quota(self):
        return None