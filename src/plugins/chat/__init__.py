from ..llm import (
    ChatSession, 
    download_image_to_b64, 
    tts, 
    ChatSessionResponse, 
    api_provider_mgr, 
    translate_text, 
    get_model_preset,
)
from ..utils import *
from ..llm.translator import Translator, TranslationResult
from datetime import datetime, timedelta
import openai
import copy
from tenacity import retry, stop_after_attempt, wait_fixed
from ..record.sql import query_recent_msg
from ..code.run import run as run_code
from .autochat import *


config = Config('chat.chat')
logger = get_logger("Chat")
file_db = get_file_db("data/chat/db.json", logger)
gwl = get_group_white_list(file_db, logger, 'chat')

at_trigger_chat_gbl = get_group_black_list(file_db, logger, 'atchat', is_service=False)

chat_cd = ColdDown(file_db, logger, config.item('chat_cd'), cold_down_name="chat_cd")
tts_cd = ColdDown(file_db, logger, config.item('tts_cd'), cold_down_name="tts_cd")
img_trans_cd = ColdDown(file_db, logger, config.item('img_trans_cd'), cold_down_name="img_trans_cd")


SESSION_LEN_LIMIT_CFG = config.item('session_len_limit')

SYSTEM_PROMPT_PATH       = "config/chat/system_prompt.txt"
SYSTEM_PROMPT_TOOLS_PATH = "config/chat/system_prompt_tools.txt"
TOOLS_TRIGGER_WORDS_PATH = "config/chat/tools_trigger_words.txt"
SYSTEM_PROMPT_PYTHON_RET = "config/chat/system_prompt_python_ret.txt"

CLEANCHAT_TRIGGER_WORDS = ["cleanchat", "clean_chat", "cleanmode", "clean_mode"]
IMAGE_RESPONSE_TRIGGER_WORDS = ['生成图片', '图片生成', 'imagen', 'Imagen', 'IMAGEN']


# 使用工具 返回需要添加到回复的额外信息
async def use_tool(ctx: HandlerContext, session: ChatSession, type: str, data: Any) -> str:
    if type == "python":
        logger.info(f"使用python工具, data: {data}")
        await ctx.asend_fold_msg_adaptive(f"正在执行python代码:\n\n{data}")
        try:
            str_code = "py\n" + data
            res = await run_code(str_code)
        except Exception as e:
            logger.print_exc(f"请求运行代码失败")
            res = f"运行代码失败: {get_exc_desc(e)}"
        logger.info(f"python执行结果: {res}")
        system_prompt_ret = Path(SYSTEM_PROMPT_PYTHON_RET).read_text(encoding="utf-8")
        session.append_system_content(system_prompt_ret.format(res=res))
        return res
    
    else:
        raise Exception(f"unknown tool type")

# ------------------------------------------ 聊天记录总结逻辑 ------------------------------------------ #

image_caption_db = get_file_db("data/chat/image_caption_db.json", logger)
IMAGE_CAPTION_LIMIT_CFG = config.item('image_caption.limit')
IMAGE_CAPTION_TIMEOUT_SEC_CFG = config.item('image_caption.timeout_sec')
IMAGE_CAPTION_TEMPLATE_PATH = "config/chat/image_caption_prompt.txt"

# 获取图片caption
async def get_image_caption(mdata: dict, model_name: str, timeout: int, use_llm: bool):
    summary = mdata.get("summary", '')
    url = mdata.get("url", None)
    file_unique = mdata.get("file_unique", '')
    sub_type = mdata.get("sub_type", 0)
    sub_type = "图片" if sub_type == 0 else "表情"
    caption = image_caption_db.get(file_unique)
    if not caption:
        logger.info(f"chat尝试总结图片: file_unique={file_unique} url={url} summary={summary} subtype={sub_type}")
        try:
            if not use_llm:
                return f"[{sub_type}(加载失败)]" if not summary else f"[{sub_type}:{summary}]"

            prompt = Path(IMAGE_CAPTION_TEMPLATE_PATH).read_text(encoding="utf-8").format(sub_type=sub_type)
            img = await download_image_to_b64(url)
            session = ChatSession()
            session.append_user_content(prompt, imgs=[img], verbose=False)
            resp = await session.get_response(model_name=model_name, timeout=timeout)
            caption = truncate(resp.result.strip(), 512)
            assert caption, "图片总结为空"

            logger.info(f"图片总结成功: {caption}")
            image_caption_db.set(file_unique, caption)
            keys = image_caption_db.get('keys', [])
            keys.append(file_unique)
            while len(keys) > IMAGE_CAPTION_LIMIT_CFG.get():
                key = keys.pop(0)
                image_caption_db.delete(key)
                logger.info(f"删除图片caption: {key}")
            image_caption_db.set('keys', keys)
        
        except Exception as e:
            logger.print_exc(f"总结图片 url={url} 失败")
            return f"[{sub_type}(加载失败)]" if not summary else f"[{sub_type}:{summary}]"
        
    return f"[{sub_type}:{caption}]"

# json消息段转换为纯文本
def json_msg_to_readable_text(mdata: dict):
    try:
        data = loads_json(mdata['data'])
        title = data["meta"]["detail_1"]["title"]
        desc = truncate(data["meta"]["detail_1"]["desc"], 32)
        url = data["meta"]["detail_1"]["qqdocurl"]
        return f"[{title}分享:{desc}]"
    except:
        try:
            return f"[转发消息:{data['prompt']}]"
        except:
            return "[转发消息(加载失败)]"

# 转发聊天记录转换到文本
async def get_forward_msg_text(bot: Bot, model: str, forward_seg, indent: int = 0) -> str:
    logger.info(f"chat开始总结聊天记录: {forward_seg['data']['id']}")
    
    forward_id = forward_seg['data']['id']
    forward_content = forward_seg['data'].get("content")
    if not forward_content:
        forward_msg = await get_forward_msg(bot, forward_id)
        if not forward_msg:
            logger.warning(f"chat获取聊天记录失败: {forward_id}")
            return "[转发消息(加载失败)]"
        forward_content = forward_msg['messages']

    text = " " * indent + f"聊天记录```\n"
    for msg_obj in forward_content:
        sender_name = msg_obj['sender']['nickname']
        segs = msg_obj['message']
        text += " " * indent + f"{sender_name}: "
        for seg in segs:
            mtype, mdata = seg['type'], seg['data']
            if mtype == "text":
                text += f"{mdata['text']}"
            elif mtype == "face":
                text += f"[表情]"
            elif mtype == "image":
                use_llm = mdata.get("sub_type", 0) == 0
                text += await get_image_caption(mdata, model, IMAGE_CAPTION_TIMEOUT_SEC_CFG.get(), use_llm=use_llm)
            elif mtype == "video":
                text += f"[视频]"
            elif mtype == "audio":
                text += f"[音频]"
            elif mtype == "file":
                text += f"[文件]"
            elif mtype == "at":
                text += f"[@{mdata['qq']}]"
            elif mtype == "reply":
                text += f"[reply={mdata['id']}]"
            elif mtype == "forward":
                text += await get_forward_msg_text(bot, model, seg, indent + 4)
            elif mtype == "json":
                text += json_msg_to_readable_text(mdata)
        text += "\n"
    text += " " * indent + "```\n"
    return text
        

# ------------------------------------------ 模型选择逻辑 ------------------------------------------ #

def trigger_chat_help_condition(text: str) -> bool:
    if "/chat" not in text:
        return False
    text = text.strip().replace("/chat", "")
    return text in ["help", "帮助"]

# 获取某个群组当前的模型名
def get_group_model_name(group_id, mode):
    group_model_dict = file_db.get("group_chat_model_dict", {})
    default = get_model_preset("chat.group")
    return group_model_dict.get(str(group_id), default).get(mode, default[mode])

# 获取某个用户私聊当前的模型名
def get_private_model_name(user_id, mode):
    private_model_dict = file_db.get("private_chat_model_dict", {})
    default = get_model_preset("chat.private")
    return private_model_dict.get(str(user_id), default).get(mode, default[mode])

# 获取某个event的模型名
def get_model_name(event, mode) -> Union[str, List[str]]:
    if is_group_msg(event):
        ret = get_group_model_name(event.group_id, mode)
    else:
        ret = get_private_model_name(event.user_id, mode)
    if not isinstance(ret, str) and len(ret) == 1:
        ret = ret[0]
    return ret

# 清空某个群组当前的模型名
def clear_group_model_name(group_id):
    group_model_dict = file_db.get("group_chat_model_dict", {})
    group_model_dict.pop(str(group_id), None)
    file_db.set("group_chat_model_dict", group_model_dict)

# 清空某个用户的私聊当前的模型名
def clear_private_model_name(user_id):
    private_model_dict = file_db.get("private_chat_model_dict", {})
    private_model_dict.pop(str(user_id), None)
    file_db.set("private_chat_model_dict", private_model_dict)

# 清空某个event的模型名
def clear_model_name(event):
    if is_group_msg(event):
        clear_group_model_name(event.group_id)
    else:
        clear_private_model_name(event.user_id)

# 修改某个群组当前的模型名
def change_group_model_name(group_id, model_name: str, mode):
    ChatSession.check_model_name(model_name, mode)
    group_model_dict = file_db.get("group_chat_model_dict", {})
    default = get_model_preset("chat.group")
    if str(group_id) not in group_model_dict:
        group_model_dict[str(group_id)] = copy.deepcopy(default)
    group_model_dict[str(group_id)][mode] = model_name
    file_db.set("group_chat_model_dict", group_model_dict)

# 修改某个用户的私聊当前的模型名
def change_private_model_name(user_id, model_name: str, mode):
    ChatSession.check_model_name(model_name, mode)
    private_model_dict = file_db.get("private_chat_model_dict", {})
    default = get_model_preset("chat.private")
    if str(user_id) not in private_model_dict:
        private_model_dict[str(user_id)] = copy.deepcopy(default)
    private_model_dict[str(user_id)][mode] = model_name
    file_db.set("private_chat_model_dict", private_model_dict)

# 根据event修改模型名
def change_model_name(event, model_name: str, mode):
    model_name = api_provider_mgr.find_model(model_name).get_full_name()
    if is_group_msg(event):
        change_group_model_name(event.group_id, model_name, mode)
    else:
        change_private_model_name(event.user_id, model_name, mode)
    return model_name

# ------------------------------------------ 聊天逻辑 ------------------------------------------ #

# 会话过期时间
SESSION_EXPIRE_TIME = timedelta(hours=12)
# 会话列表 索引为最后一次消息的id
sessions: Dict[str, ChatSession] = {}
# 询问的消息id集合
query_msg_ids = set()

# 询问
CHAT_CMDS = ["/chat", ]
chat_request = CmdHandler(
    [""], logger, block=False, 
    help_command="/chat", help_trigger_condition=trigger_chat_help_condition,
)
@chat_request.handle()
async def _(ctx: HandlerContext):
    bot, event = ctx.bot, ctx.event
    global sessions, query_msg_ids
    session = None
    try:
        # 群组名单检测
        if not gwl.check(event, allow_private=True, allow_super=True): return

        # 自己回复指令的消息不回复
        if check_self_reply(event): return

        # 获取内容
        query_msg = ctx.get_msg()
        query_text = extract_text(query_msg)
        query_imgs = extract_image_url(query_msg)
        query_cqs = extract_cq_code(query_msg)
        reply_msg = ctx.get_reply_msg()
        reply_id = ctx.get_reply_msg_id()

        # 是否是/chat触发的消息
        triggered_by_chat_cmd = False
        for chat_cmd in CHAT_CMDS:
            if query_text.strip().startswith(chat_cmd):
                query_text = query_text.strip().removeprefix(chat_cmd)
                triggered_by_chat_cmd = True
                break

        # 如果当前群组正在自动聊天或者关闭@触发，只有通过/chat触发的消息才回复
        if is_group_msg(event) and (autochat_gwl.check_id(event.group_id) or not at_trigger_chat_gbl.check(event)):
            if not triggered_by_chat_cmd:
                return
            
        # /开头的消息不回复
        if query_text.strip().startswith("/"):
            return

        bot_name = await get_group_member_name(event.group_id, bot.self_id)

        # 空消息不回复
        if query_text.replace(f"@{bot_name}", "").strip() == "" or query_text is None:
            return

        # 如果不是/chat触发的消息，并且在群组内或者自己对自己的私聊，则只有at机器人的消息才会被回复
        has_true_at = False
        has_text_at = False
        if "at" in query_cqs:
            for cq in query_cqs["at"]:
                if cq["qq"] == bot.self_id:
                    has_true_at = True
                    break
        if "text" in query_cqs:
            for cq in query_cqs["text"]:
                if f"@{bot_name}" in cq['text']:
                    has_text_at = True
                    break
        if not triggered_by_chat_cmd and (is_group_msg(event) or check_self(event)):
            if not (has_true_at or has_text_at): return
        
        # cd检测
        if not (await chat_cd.check(event)): return
        
        logger.log(f"收到询问: {query_msg}")
        query_msg_ids.add(event.message_id)

        # 用于备份的session_id
        session_id_backup = None
        model_name = None

        # 清除文本形式的at
        if has_text_at:
            query_text = query_text.replace(f"@{bot_name}", "")

        # 如果在对话中指定模型名
        if "model:" in query_text:
            if is_group_msg(event) and not check_superuser(event): 
                return await ctx.asend_reply_msg("非超级用户不允许自定义模型")
            model_name = query_text.split("model:")[1].strip().split(" ")[0]
            try:
                ChatSession.check_model_name(model_name)
            except Exception as e:
                return await ctx.asend_reply_msg(f"{e}")
            query_text = query_text.replace(f"model:{model_name}", "").strip()     
            logger.info(f"使用指定模型: {model_name}")  

        # 是否是cleanchat
        if any([word in query_text for word in CLEANCHAT_TRIGGER_WORDS]):
            for word in CLEANCHAT_TRIGGER_WORDS:
                query_text = query_text.replace(word, "")
            need_tools = False
            system_prompt = None
            logger.info(f"使用CleanChat模式")
        else:
            # 是否需要使用工具
            tools_trigger_words = []
            with open(TOOLS_TRIGGER_WORDS_PATH, "r", encoding="utf-8") as f:
                tools_trigger_words = f.read().split()
            need_tools = any([word and word in query_text for word in tools_trigger_words])
            logger.info(f"使用工具: {need_tools}")

            # 系统prompt
            system_prompt_path = SYSTEM_PROMPT_TOOLS_PATH if need_tools else SYSTEM_PROMPT_PATH
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                system_prompt = f.read().format(
                    bot_name=bot_name,
                    current_date=datetime.now().strftime("%Y-%m-%d")
                )

        # 是否生成图片
        enable_image_response = False
        if any([word in query_text for word in IMAGE_RESPONSE_TRIGGER_WORDS]):
            for word in IMAGE_RESPONSE_TRIGGER_WORDS:
                query_text = query_text.replace(word, "")
            enable_image_response = True
            query_text += "\n生成图片作为回复"
            system_prompt = None
            logger.info(f"使用生成图片模式")
            
        # 收集回复消息的内容
        if reply_msg is not None:
            # 回复模式，检测是否在历史会话中
            logger.info(f"回复模式：{reply_id}")

            if str(reply_id) in sessions:
                # 在历史会话中，直接沿用会话
                session = sessions[str(reply_id)]
                sessions.pop(str(reply_id))
                session_id_backup = reply_id
                logger.info(f"沿用会话{session.id}, 长度:{len(session)}")
            else:
                # 不在历史会话中，使用新会话，并加入回复的内容
                reply_text = extract_text(reply_msg)
                reply_cqs = extract_cq_code(reply_msg)
                reply_imgs = extract_image_url(reply_msg)
                reply_uid = ctx.get_reply_sender().user_id
                logger.info(f"获取回复消息: {reply_id}, uid:{reply_uid}")
                # 不支持的回复类型
                if any([t in reply_cqs for t in ["json", "video"]]):
                    # return await ctx.asend_reply_msg("不支持的消息类型")
                    return
                session = ChatSession(system_prompt)
                # 回复折叠内容
                if "forward" in reply_cqs:
                    logger.info(reply_cqs["forward"][0]["id"])
                    forward_text = await get_forward_msg_text(ctx.bot, get_model_preset('chat.image_caption'), find_by(reply_msg, 'type', "forward"))
                    session.append_user_content(forward_text)
                # 回复普通内容
                elif len(reply_imgs) > 0 or reply_text.strip() != "":
                    reply_imgs = [await download_image_to_b64(img) for img in reply_imgs]
                    # 自身
                    if str(reply_uid) == str(bot.self_id):
                        if reply_imgs:
                            # 因为部分模型不支持模型自身消息记录为图片，所以改为用户消息
                            session.append_user_content(reply_text, reply_imgs)
                        else:
                            session.append_bot_content(reply_text)
                    # 其他人
                    else:
                        session.append_user_content(reply_text, reply_imgs)
        else:
            session = ChatSession(system_prompt)

        # 推入询问内容
        query_imgs = [await download_image_to_b64(img) for img in query_imgs]
        session.append_user_content(query_text, query_imgs)

        # 检查是否为空
        if len(session) == 0:
            return

        # 如果未指定模型，根据配置和消息类型获取模型
        if not model_name:
            mode = "text"
            if enable_image_response:
                mode = "image"
            elif need_tools:
                mode = "tool"
            elif session.has_multimodal_content():
                mode = "mm"
            model_name = get_model_name(event, mode)
        
        # 进行询问
        total_seconds, total_ptokens, total_ctokens, total_cost = 0, 0, 0, 0
        tools_additional_info = ""
        rest_quota = 0
        reasoning = None
        resp_model = None

        for _ in range(3):
            t = datetime.now()
            resp = await session.get_response(
                model_name=model_name, 
                image_response=enable_image_response,
                timeout=300,
            )

            res_text = ""
            for part in resp.result_list:
                if isinstance(part, str):
                    res_text += part
                else:
                    res_text += await get_image_cq(part)
            res_text = res_text.strip()

            total_ptokens += resp.prompt_tokens
            total_ctokens += resp.completion_tokens
            total_cost += resp.cost
            total_seconds += (datetime.now() - t).total_seconds()
            rest_quota = resp.quota
            resp_model = resp.model
            reasoning = resp.reasoning

            # 如果回复时关闭则取消回复
            if not gwl.check(event, allow_private=True, allow_super=True): return

            if not need_tools: break
            try:
                # 调用工具
                tool_args = loads_json(res_text)
                tool_ret = await use_tool(ctx, session, tool_args["tool"], tool_args["data"])
                tools_additional_info += f"[工具{tool_args['tool']}返回结果: {tool_ret.strip()}]\n" 
            except Exception as exc:
                logger.info(f"工具调用失败: {exc}")
                break

    except openai.APIError as e:
        logger.print_exc(f'会话 {session.id} 失败')
        if session_id_backup:
            sessions[session_id_backup] = session
        ret = truncate(f"会话失败: {e.message}", 128)
        return await ctx.asend_reply_msg(ret)

    except Exception as error:
        if session:
            logger.print_exc(f'会话 {session.id} 失败')
            if session_id_backup:
                sessions[session_id_backup] = session
            ret = truncate(f"会话失败: {error}", 128)
            return await ctx.asend_reply_msg(ret)
        else:
            return

    # 思考内容
    reasoning_text = ""
    if reasoning and reasoning.strip():
        if config.get('output_reasoning_content'):
            reasoning_text = f"【思考】\n{reasoning}\n【回答】\n"
        else:
            reasoning_text = f"(已思考{len(reasoning)}字)\n"
    
    # 添加额外信息
    additional_info = f"{resp_model.get_full_name()} | {total_seconds:.1f}s, {total_ptokens}+{total_ctokens} tokens"
    if rest_quota > 0:
        price_unit = resp_model.get_price_unit()
        if total_cost == 0.0:
            additional_info += f" | 0/{rest_quota:.2f}{price_unit}"
        elif total_cost >= 0.0001:
            additional_info += f" | {total_cost:.4f}/{rest_quota:.2f}{price_unit}"
        else:
            additional_info += f" | <0.0001/{rest_quota:.2f}{price_unit}"
    additional_info = f"\n({additional_info})"
    final_text = tools_additional_info + reasoning_text + res_text + additional_info

    # 进行回复
    ret = await ctx.asend_fold_msg_adaptive(final_text)

    # 加入会话历史
    if ret:
        ret_id = str(ret["message_id"])
        sessions[ret_id] = session
        logger.info(f"会话{session.id}加入会话历史:{ret_id}, 长度:{len(session)}")
        session.limit_length(SESSION_LEN_LIMIT_CFG.get())

    # 检查过期会话
    for k, v in list(sessions.items()):
        if datetime.now() - v.update_time > SESSION_EXPIRE_TIME:
            sessions.pop(k)
            logger.info(f"删除过期的会话{k}")


# 获取或修改当前私聊或群聊使用的模型
change_model = CmdHandler([
    "/模型", "/聊天模型",
    "/chat_model", "/chat model", "/chatmodel",
], logger)
change_model.check_cdrate(chat_cd).check_wblist(gwl)
@change_model.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    # 查看
    if not args:
        text_model_name = get_model_name(ctx.event, "text")
        mm_model_name = get_model_name(ctx.event, "mm")
        tool_model_name = get_model_name(ctx.event, "tool")
        image_model_name = get_model_name(ctx.event, "image")
        return await ctx.asend_reply_msg(f"文本模型: {text_model_name}\n多模态模型: {mm_model_name}\n工具模型: {tool_model_name}\n图片生成模型: {image_model_name}")
    # 修改
    else:
        # 群聊中只有超级用户可以修改模型
        if is_group_msg(ctx.event) and not check_superuser(ctx.event): return
        # 只修改文本模型
        if "text" in args:
            last_model_name = get_model_name(ctx.event, "text")
            args = args.replace("text", "").strip()
            name = change_model_name(ctx.event, args, "text")
            return await ctx.asend_reply_msg(f"已切换文本模型: {last_model_name} -> {name}")
        # 只修改多模态模型
        elif "mm" in args:
            last_model_name = get_model_name(ctx.event, "mm")
            args = args.replace("mm", "").strip()
            name = change_model_name(ctx.event, args, "mm")
            return await ctx.asend_reply_msg(f"已切换多模态模型: {last_model_name} -> {name}")
        # 只修改工具模型
        elif "tool" in args:
            last_model_name = get_model_name(ctx.event, "tool")
            args = args.replace("tool", "").strip()
            name = change_model_name(ctx.event, args, "tool")
            return await ctx.asend_reply_msg(f"已切换工具模型: {last_model_name} -> {name}")
        # 只修改图片生成模型
        elif "image" in args:
            last_model_name = get_model_name(ctx.event, "image")
            args = args.replace("image", "").strip()
            name = change_model_name(ctx.event, args, "image")
            return await ctx.asend_reply_msg(f"已切换图片生成模型: {last_model_name} -> {name}")
        # 同时修改文本和多模态模型
        else:
            msg = ""
            try:
                last_mm_model_name = get_model_name(ctx.event, "mm")
                name = change_model_name(ctx.event, args, "mm")  
                msg += f"已切换多模态模型: {last_mm_model_name} -> {name}\n"
            except Exception as e:
                msg += f"{e}, 仅切换文本模型\n"
            last_text_model_name = get_model_name(ctx.event, "text")
            name = change_model_name(ctx.event, args, "text")
            msg += f"已切换文本模型: {last_text_model_name} -> {name}"
            return await ctx.asend_reply_msg(msg.strip())


# 清空当前私聊或群聊使用的模型
clear_model = CmdHandler([
    "/重置模型", "/清空模型",
    "/clear model", "/reset model", "/model reset", "/model clear",
], logger)
clear_model.check_cdrate(chat_cd).check_wblist(gwl)
@clear_model.handle()
async def _(ctx: HandlerContext):
    # 群聊中只有超级用户可以清空模型
    if is_group_msg(ctx.event) and not check_superuser(ctx.event): return
    clear_model_name(ctx.event)
    return await ctx.asend_reply_msg("已清空模型设置")


# 获取所有可用的模型名
all_model = CmdHandler([
    "/模型列表",
    "/model_list", "/model list", "/modellist",
    "/allmodel", "/all model", "/all_model",
], logger)
all_model.check_cdrate(chat_cd).check_wblist(gwl)
@all_model.handle()
async def _(ctx: HandlerContext):
    msg = "可用模型列表:\n"
    for model in api_provider_mgr.get_all_models():
        msg += f"{model.get_full_name()} "
        if model.input_pricing + model.output_pricing < 1e-9:
            msg += "🆓"
        if model.is_multimodal:
            msg += "🏞️"
        if model.image_response:
            msg += "🎨"
        msg += "\n"
    return await ctx.asend_fold_msg_adaptive(msg.strip())


# 获取所有可用的供应商名
chat_providers = CmdHandler([
    "/供应商", "/chat_provider", "/chat provider", "/chatprovider"
], logger)
chat_providers.check_cdrate(chat_cd).check_wblist(gwl)
@chat_providers.handle()
async def _(ctx: HandlerContext):
    providers = api_provider_mgr.get_all_providers()
    msg = ""
    for provider in providers:
        quota = await provider.aget_current_quota()
        msg += f"{provider.name}({provider.code}) {quota:.4f}{provider.get_price_unit()}\n"
    return await ctx.asend_reply_msg(msg.strip())


# TTS
tts_request = CmdHandler(["/tts"], logger)
tts_request.check_cdrate(tts_cd).check_wblist(gwl)
@tts_request.handle()
async def _(ctx: HandlerContext):
    text = ctx.get_args().strip()
    if not text: return
    with TempFilePath("mp3", remove_after=timedelta(minutes=3)) as path:
        await tts(text, path)
        return await ctx.asend_msg(f"[CQ:record,file=file://{path}]")


translator = Translator()

# 翻译图片
trans = CmdHandler(["/trans", "/translate", "/翻译"], logger)
trans.check_cdrate(img_trans_cd).check_wblist(gwl)
@trans.handle()
async def _(ctx: HandlerContext):
    reply_msg = ctx.get_reply_msg()

    # 翻译当前消息内的文本
    if not reply_msg:
        text = ctx.get_args().strip()
        assert_and_reply(text, "请输入要翻译的文本，或回复要翻译的文本/图片")
        return await ctx.asend_fold_msg_adaptive(await translate_text(text, cache=False))

    cqs = extract_cq_code(reply_msg)
    imgs = cqs.get("image", [])

    # 翻译回复消息内的文本
    if not imgs:
        text = extract_text(reply_msg)
        assert_and_reply(text, "请输入要翻译的文本，或回复要翻译的文本/图片")
        return await ctx.asend_fold_msg_adaptive(await translate_text(text, cache=False))

    raise ReplyException("图片翻译器已废弃，请直接使用聊天功能翻译图片")

    args = ctx.get_args().strip()
    debug = False
    if 'debug' in args:
        debug = True
        args = args.replace('debug', '').strip()

    lang = None
    if args:
        assert_and_reply(args in translator.langs, f"支持语言:{translator.langs}, 指定语言仅影响文本检测，不影响翻译")
        lang = args
    
    img_url = cqs['image'][0]['url']
    img = await download_image(img_url)
    
    try:
        if not translator.model_loaded:
            logger.info("加载翻译模型")
            translator.load_model()

        res: TranslationResult = await translator.translate(img, lang=lang, debug=debug)

        msg = await get_image_cq(res.img)
        msg += f"{res.total_time:.1f}s {res.total_cost:.4f}$"
        msg += " | "
        msg += f"检测 {res.ocr_time:.1f}s"
        msg += " | "
        msg += f"合并"
        if res.merge_time: msg += f" {res.merge_time:.1f}s"
        if res.merge_cost: msg += f" {res.merge_cost:.4f}$"
        msg += " | "
        msg += f"翻译"
        if res.trans_time: msg += f" {res.trans_time:.1f}s"
        if res.trans_cost: msg += f" {res.trans_cost:.4f}$"
        msg += " | "
        msg += f"校对"
        if res.correct_time: msg += f" {res.correct_time:.1f}s"
        if res.correct_cost: msg += f" {res.correct_cost:.4f}$"
        await ctx.asend_reply_msg(msg.strip())

    except Exception as e:
        raise Exception(f"翻译失败: {e}")


# 查询autochat用户记忆
autochat_usermemory = CmdHandler([
    "/autochat um", "/um", "/autochat usermemory", "/usermemory"
], logger)
autochat_usermemory.check_cdrate(chat_cd).check_wblist(autochat_gwl)
@autochat_usermemory.handle()
async def _(ctx: HandlerContext):
    qids = ctx.get_at_qids()
    if not qids:
        qid = ctx.user_id
    else:
        qid = qids[0]

    nickname = await get_group_member_name(ctx.group_id, qid)

    um = None
    path = f"data/chat/autochat/memory_{ctx.group_id}.json"
    if os.path.exists(path):
        mem = load_json(path)
        um = mem.get("ums", {}).get(str(qid), {})
    
    if not um:
        return await ctx.asend_reply_msg(f"对@{nickname}的记忆: 无")

    um_text = f"对@{nickname}的记忆\n"
    if names := um.get('names'):
        um_text += f"🏷️ 【曾用名】\n{', '.join(names)}\n"
    if profile := um.get('profile'):
        um_text += f"👤 【用户画像】\n{profile}\n"
    if recent_events := um.get('recent_events'):
        um_text += f"📅 【近期事件】\n"
        for time, event in recent_events:
            formated_time = datetime.fromtimestamp(time).strftime("%m-%d %H:%M")
            um_text += f"[{formated_time}] {event}\n"

    return await ctx.asend_fold_msg_adaptive(um_text.strip())

