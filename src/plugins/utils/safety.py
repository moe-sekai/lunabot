from .utils import *
from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.common.exception.tencent_cloud_sdk_exception import TencentCloudSDKException
from tencentcloud.ims.v20201229 import ims_client, models


SUGGEST_PASS = 'Pass'
SUGGEST_REVIEW = 'Review'
SUGGEST_BLOCK = 'Block'

@dataclass
class SafetyCheckResult:
    suggestion: str     # 'Pass', 'Review', 'Block'
    label: str
    sub_label: str
    score: int
    message: str = ''

    def suggest_block(self) -> bool:
        return self.suggestion == SUGGEST_BLOCK
    def suggest_review(self) -> bool:
        return self.suggestion == SUGGEST_REVIEW
    def suggest_pass(self) -> bool:
        return self.suggestion == SUGGEST_PASS


def get_tencentcloud_client():
    cred = credential.Credential(
        global_config.get('safety_check.secret_id'),
        global_config.get('safety_check.secret_key'),
    )
    httpProfile = HttpProfile()
    httpProfile.endpoint = global_config.get('safety_check.endpoint')
    clientProfile = ClientProfile()
    clientProfile.httpProfile = httpProfile
    client = ims_client.ImsClient(cred, "ap-guangzhou", clientProfile)
    return client

    
async def image_safety_check(image: Union[Image.Image, str]) -> SafetyCheckResult:
    """
    图片安全检查
    """
    enabled = global_config.get('safety_check.enabled')
    if not enabled:
        return SafetyCheckResult(
            suggestion=SUGGEST_PASS,
            label='Normal',
            sub_label='',
            score=100,
            message='安全检查未启用，默认通过'
        )

    try:
        def check():
            data = { 'BizType': 'bot' }
            if isinstance(image, str):
                data['FileUrl'] = image
            else:
                data['FileContent'] = base64.b64encode(image.tobytes()).decode('utf-8')
            req = models.ImageModerationRequest()
            req.from_json_string(dumps_json(data))
            resp = get_tencentcloud_client().ImageModeration(req)
            resp = loads_json(resp.to_json_string())

            suggestion = resp['Suggestion']
            label = resp['Label']
            sub_label = resp.get('SubLabel', None)
            full_label = f'{label}-{sub_label}' if sub_label else label
            score = resp.get('Score', 0)

            if suggestion == SUGGEST_PASS:
                message = '通过'
            elif suggestion == SUGGEST_REVIEW:
                message = f'建议人工复审({full_label}:{score})'
            else:
                message = f'未通过({full_label}:{score})'

            return SafetyCheckResult(
                suggestion=suggestion,
                label=label,
                sub_label=sub_label,
                score=score,
                message=message
            )
  
        return await run_in_pool(check)
    except Exception as e:
        raise Exception(f"图片安全检查失败: {get_exc_desc(e)}")
    
