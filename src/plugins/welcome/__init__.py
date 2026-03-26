from ..utils import *
from nonebot import on_notice
from nonebot.adapters.onebot.v11 import NoticeEvent
import asyncio


config = Config('welcome')
logger = get_logger("Welcome")
file_db = get_file_db("data/welcome/db.json", logger)
gwl = get_group_white_list(file_db, logger, 'welcome')
cd = ColdDown(file_db, logger)


# 防止神秘原因导致的重复通知
increase_notified = set()
decrease_notified = set()


# 更新群成员信息
async def update_member_info(group_id=None):
    if group_id is not None:
        bot = await aget_group_bot(group_id, raise_exc=True)
        groups = [await get_group(bot, group_id)]
    else:
        gwl_group_ids = gwl.get()
        groups = [g for g in await get_all_bot_group_list() if int(g['group_id']) in gwl_group_ids]

    for group in groups:
        try:
            group_id = group['group_id']
            if group_id in gwl.get(): return
            members = await get_group_users(bot, group_id)
            id_names = {}
            for info in members:
                if info['card'] != "":
                    id_names[str(info['user_id'])] = info['card']
                else:
                    id_names[str(info['user_id'])] = info['nickname']
            file_db.set(f'{group_id}_members', id_names)
            logger.debug(f'群 {group_id} 成员信息更新完毕')

        except Exception as e:
            logger.print_exc(f"更新群 {group_id} 成员信息失败")


# 处理加群
async def handle_increase(group_id, user_id, sub_type):
    bot = await aget_group_bot(group_id, raise_exc=True)
    if group_id not in gwl.get(): return
    if str(user_id) == str(bot.self_id): return
    group_id, user_id = group_id, user_id
    logger.info(f'{user_id} 加入 {group_id}')

    guid = f"{group_id}_{user_id}"
    if guid in increase_notified: return
    increase_notified.add(guid)
    decrease_notified.discard(guid)

    try:
        nickname = await get_group_member_name(group_id, user_id)
        name = f"{nickname}({user_id})"
    except:
        nickname = ""
        name = str(user_id)

    welcome_info = file_db.get(f'welcome_infos', {}).get(str(group_id))
    if welcome_info:
        msg = welcome_info
        if "{user}" in msg:
            msg = msg.replace("{user}", nickname)
        if "{user_id}" in msg:
            msg = msg.replace("{user_id}", str(user_id))
        if "@" in msg:
            msg = msg.replace("@", f"[CQ:at,qq={user_id}]")
    else:
        if sub_type == 'approve':
            msg = f"{name} 加入群聊"
        elif sub_type == 'invite':
            msg = f"{name} 被邀请进入群聊"
        else:
            msg = f"{name} 加入群聊"

    await send_group_msg_by_bot(group_id, msg)
    await asyncio.sleep(3)
    return await update_member_info(group_id)

# 处理退群
async def handle_decrease(group_id, user_id, sub_type):
    bot = await aget_group_bot(group_id, raise_exc=True)
    if group_id not in gwl.get(): return
    if str(user_id) == str(bot.self_id): return
    group_id, user_id = group_id, user_id
    logger.info(f'{user_id} 离开 {group_id}')

    guid = f"{group_id}_{user_id}"
    if guid in decrease_notified: return
    decrease_notified.add(guid)
    increase_notified.discard(guid)

    members = file_db.get(f'{group_id}_members', {})
    name = members.get(str(user_id), '')

    await send_group_msg_by_bot(group_id, f"{name}({user_id}) 退出群聊")
    await asyncio.sleep(3)
    return await update_member_info(group_id)


# 加退群通知事件
join = on_notice()
@join.handle()
async def _(bot: Bot, event: NoticeEvent):
    if event.notice_type == 'group_increase':
        return await handle_increase(event.group_id, event.user_id, event.sub_type)
    # if event.notice_type == 'group_decrease':
    #     return await handle_decrease(event.group_id, event.user_id, event.sub_type)


# 定时更新
start_repeat_with_interval(
    config.get('group_info_update_interval'), 
    update_member_info, logger, '群成员信息更新',
)


# 设置入群欢迎信息
welcome_info = CmdHandler(["/welcome info", "/入群信息", "/欢迎信息"], logger)
welcome_info.check_wblist(gwl).check_superuser().check_cdrate(cd)
@welcome_info.handle()
async def _(ctx: HandlerContext):
    text = ctx.get_args().strip()
    group_id = str(ctx.group_id)
    if not text:
        welcome_infos = file_db.get(f'welcome_infos', {})
        del welcome_infos[group_id]
        file_db.set(f'welcome_infos', welcome_infos)
        return await ctx.asend_reply_msg(f"入群欢迎信息已清除")

    welcome_infos = file_db.get(f'welcome_infos', {})
    welcome_infos[group_id] = text
    file_db.set(f'welcome_infos', welcome_infos)
    await ctx.asend_reply_msg(f"已设置入群欢迎信息")



