from nonebot import on_message, on_notice
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent, Bot, Event, NoticeEvent
from datetime import datetime
from ..utils import *
from .sql import *

config = Config('record')
logger = get_logger("Record")
file_db = get_file_db("data/record/db.json", logger)
gbl = get_group_black_list(file_db, logger, "record")
cd = ColdDown(file_db, logger)

record_msg_gbl = get_group_black_list(file_db, logger, "record_msg", is_service=False)


# 防止神秘原因导致的重复消息
message_id_set = set()


# 记录消息前的钩子: 异步函数 hook(bot, event)
before_record_hook_funcs = []
def before_record_hook(func):
    before_record_hook_funcs.append(func)
    return func

# 记录消息后的钩子: 异步函数 hook(bot, event)
after_record_hook_funcs = []
def after_record_hook(func):
    after_record_hook_funcs.append(func)
    return func


# 缩减部分类型的消息用于日志输出
def simplify_msg(msg):
    try:
        for seg in msg:
            t = seg['type']
            if t == 'image':
                del seg['data']['file']
                del seg['data']['file_id']
            if t == 'forward':
                del seg['data']['content']
    except:
        pass
    return msg


# 待插入数据库消息缓存
msgs_to_insert: list[dict] = []

# 记录消息
async def record_message(bot: Bot, event: GroupMessageEvent):
    if event.message_id in message_id_set: 
        return
    if not is_group_msg(event) and event.user_id == event.self_id: 
        return
    if on_safe_mode() and not check_superuser(event):
        return
    
    message_id_set.add(event.message_id)

    with ProfileTimer("record.total"):
        with ProfileTimer("record.hooks.before"):
            before_hook_tasks = []
            for hook in before_record_hook_funcs:
                async def run_before_hook(hook, bot, event):
                    try: await hook(bot, event)
                    except: logger.print_exc(f"记录消息前hook {hook.__name__} 执行失败")
                before_hook_tasks.append(run_before_hook(hook, bot, event))
            await asyncio.gather(*before_hook_tasks)

        if record_msg_gbl.check(event, allow_super=False) or event.user_id == event.self_id:
            with ProfileTimer("record.format_msg"):
                time = datetime.fromtimestamp(event.time)

                msg = get_msg(event)
                msg_id = event.message_id
                user_id = event.user_id
                is_group = is_group_msg(event)

                if is_group:
                    group_id = event.group_id
                else:
                    group_id = 0
                user_name = get_user_name_by_event(event)

                with ProfileTimer("record.get_group_name"):
                    if is_group:
                        try: group_name = truncate(await get_group_name(bot, group_id), 16)
                        except: group_name = "未知群聊"

                msg_for_log = simplify_msg(msg)
                if not is_group:
                    logger.info(f"[{msg_id}] {user_name}({user_id}): {str(msg_for_log)}")
                elif check_self_reply(event):
                    logger.info(f"[{msg_id}] {group_name}({group_id}) 自身回复: {str(msg_for_log)}")
                elif check_self(event):
                    logger.info(f"[{msg_id}] {group_name}({group_id}) 自身消息: {str(msg_for_log)}")
                else:
                    logger.info(f"[{msg_id}] {group_name}({group_id}) {user_name}({user_id}): {str(msg_for_log)}")

            if record_msg_gbl.check(event, allow_super=False):
                msgs_to_insert.append(dict(
                    group_id=group_id,
                    time=time,
                    msg_id=msg_id,
                    user_id=user_id,
                    nickname=user_name,
                    msg=msg,
                ))

        for hook in after_record_hook_funcs:
            async def run_after_hook(hook, bot, event):
                try: await hook(bot, event)
                except: logger.print_exc(f"记录消息后hook {hook.__name__} 执行失败")
            asyncio.create_task(run_after_hook(hook, bot, event))

# 插入数据库消息定时任务
@repeat_with_interval(config.item('insert_msg_loop_interval_seconds'), '插入消息到数据库', logger)
async def insert_msg_task():
    try:
        if msgs_to_insert:
            await insert_msgs(msgs_to_insert)
    except Exception as e:
        logger.print_exc(f"插入 {len(msgs_to_insert)} 条消息到数据库失败")
    finally:
        msgs_to_insert.clear()

# 记录消息
add = on_message(block=False, priority=-1)
@add.handle()
async def _(bot: Bot, event: MessageEvent):
    if not gbl.check(event, allow_private=True, allow_super=False): return
    await record_message(bot, event)
    


# 检查消息
check = CmdHandler(["/check"], logger)
check.check_superuser()
@check.handle()
async def _(ctx: HandlerContext):
    reply = ctx.event.reply
    if not reply:
        raise Exception("请回复一条消息")
    await ctx.asend_reply_msg(str(reply))


# 获取用户在群聊中用过的昵称
check = CmdHandler(["/nickname"], logger)
check.check_wblist(gbl).check_cdrate(cd)
@check.handle()
async def _(ctx: HandlerContext):
    user_id = None

    try:
        cqs = extract_cq_code(ctx.get_msg())
        if 'at' in cqs:
            user_id = cqs['at'][0]['qq']
        else:
            user_id = int(ctx.get_args())
    except:
        user_id = ctx.user_id

    if not user_id:
        return await ctx.asend_reply_msg("请回复用户或指定用户的QQ号")

    recs = await query_msg_by_user_id(ctx.group_id, user_id)
    recs = sorted(recs, key=lambda x: x['time'])
    if not recs:
        return await ctx.asend_reply_msg(f"用户{user_id}在群{ctx.group_id}中没有发过言")

    nicknames = []
    cur_name = None
    for rec in recs:
        name = rec['nickname']
        time = rec['time'].strftime("%Y-%m-%d")
        if name != cur_name:
            cur_name = name
            nicknames.append((time, name))

    msg = f"{user_id} 用过的群名片:\n"
    nicknames = nicknames[-50:]
    for time, name in nicknames:
        msg += f"({time}) {name}\n"
    
    return await ctx.asend_fold_msg_adaptive(msg.strip())


# 私聊转发
private_forward = CmdHandler(["/forward"], logger)
private_forward.check_private().check_superuser()
@private_forward.handle()
async def _(ctx: HandlerContext):
    private_forward_list = file_db.get('private_forward_list', [])
    user_id = ctx.user_id
    if user_id in private_forward_list:
        private_forward_list.remove(user_id)
        file_db.set('private_forward_list', private_forward_list)
        return await ctx.asend_reply_msg("私聊转发已关闭")
    else:
        private_forward_list.append(user_id)
        file_db.set('private_forward_list', private_forward_list)
        return await ctx.asend_reply_msg("私聊转发已开启")


# 私聊转发hook
@before_record_hook
async def private_forward_hook(bot: Bot, event: MessageEvent):
    user_id = event.sender.user_id
    nickname = event.sender.nickname
    msg = get_msg(event)
    if is_group_msg(event):
        return

    for forward_user_id in file_db.get('private_forward_list', []):
        if user_id == forward_user_id:
            continue
        await send_private_msg_by_bot(forward_user_id, f"来自{nickname}({user_id})的私聊消息:")
        await send_private_msg_by_bot(forward_user_id, msg)


# log各种事件消息
misc_notice_log = on_notice(block=False)
@misc_notice_log.handle()
async def _(bot: Bot, event: NoticeEvent):
    # 群消息撤回
    if event.notice_type == 'group_recall':
        logger.info(f"群 {event.group_id} 的用户 {event.operator_id} 撤回了用户 {event.user_id} 发送的消息 {event.message_id}")
    # 好友消息撤回
    if event.notice_type == 'friend_recall':
        logger.info(f"用户 {event.user_id} 撤回了自己的私聊消息 {event.message_id}")
    # 群消息点赞
    if event.notice_type == 'group_msg_emoji_like':
        for like in event.likes:
            logger.info(f"群 {event.group_id} 的用户 {event.user_id} 给消息 {event.message_id} 回应了 {like['count']} 个emoji {like['emoji_id']}")
    # 群戳一戳
    if event.notice_type == 'notify' and event.sub_type == 'poke':
        logger.info(f"群 {event.group_id} 的用户 {event.user_id} 戳了用户 {event.target_id}")


# 查询指令历史记录
get_cmd_history = CmdHandler(["/cmd_history", "/cmdh"], logger)
get_cmd_history.check_superuser()
@get_cmd_history.handle()
async def _(ctx: HandlerContext):
    global _cmd_history
    args = ctx.get_args()
    try: limit = int(args)
    except: limit = 10
    msg = "【历史记录】\n"
    for context in _cmd_history:
        time = context.time.strftime("%Y-%m-%d %H:%M:%S")
        msg += f"[{time}]\n"
        group_id, user_id = context.group_id, context.user_id
        if group_id:
            group_name = await get_group_name(ctx.bot, group_id)
            msg += f"<{group_name}({group_id})>\n"
            user_name = await get_group_member_name(group_id, user_id)
            msg += f"<{user_name}({user_id})>\n"
        else:
            user_name = context.event.sender.nickname
            msg += f"<{user_name}({user_id})>\n"
        msg += f"{context.trigger_cmd} {context.arg_text}"
        msg += "\n\n"
    return await ctx.asend_fold_msg_adaptive(msg.strip())


# 聊天记录转文本
forward_to_text = CmdHandler(["/转文本", "/to_text"], logger)
forward_to_text.check_wblist(gbl).check_cdrate(cd)
@forward_to_text.handle()
async def _(ctx: HandlerContext):
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
    async def get_forward_msg_text(bot, forward_seg, indent: int = 0) -> str:
        forward_id = forward_seg['data']['id']
        forward_content = forward_seg['data'].get("content")
        if not forward_content:
            forward_msg = await get_forward_msg(bot, forward_id)
            if not forward_msg:
                return "[转发消息(加载失败)]"
            forward_content = forward_msg['messages']

        text = " " * indent + f"=== 折叠消息 ===\n"
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
                    text += f"[图片]" if seg['data'].get('sub_type', 0) == 0 else "[表情]"
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
                    text += await get_forward_msg_text(bot, seg, indent + 4)
                elif mtype == "json":
                    text += json_msg_to_readable_text(mdata)
            text += "\n"
        text += " " * indent + "============\n"
        return text

    reply_msg = ctx.get_reply_msg()
    assert_and_reply(reply_msg, "请回复一条聊天记录")
    forward_seg = None
    for seg in reply_msg:
        if seg['type'] == 'forward':
            forward_seg = seg
            break
    assert_and_reply(forward_seg, "回复的消息不是聊天记录")
    text = await get_forward_msg_text(ctx.bot, forward_seg)

    return await ctx.asend_fold_msg_adaptive(text)