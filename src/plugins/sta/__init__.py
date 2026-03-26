from ..utils import *
from .draw import draw_sta, reset_jieba, draw_date_count_plot, draw_word_count_plot, draw_sta_sum
from ..record.sql import query_msg_by_range, query_msg_count


config = Config("sta")
logger = get_logger("Sta")
file_db = get_file_db("data/sta/db.json", logger)
gbl = get_group_black_list(file_db, logger, "sta")
cd = ColdDown(file_db, logger)

notify_gwl = get_group_white_list(file_db, logger, "sta_notify", is_service=False)


STATICSTIC_TIME = config.get('statistic_time')

NAME_LEN_LIMIT_CFG = config.item('name_len_limit')
PLOT_TOPK1_CFG = config.item('pie_topk')
PLOT_TOPK2_CFG = config.item('plot_topk')
PLOT_INTERVAL_CFG = config.item('plot_interval')
STA_WORD_TOPK_CFG = config.item('sta_word_topk')


# 获取某天统计图数据
async def get_day_statistic(group_id, date=None):
    if date is None: date = datetime.now().strftime("%Y-%m-%d")
    start_time = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d 00:00:00")
    end_time   = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d 23:59:59")
    recs = await query_msg_by_range(group_id, 
                     datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S"), 
                     datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S"))
    logger.info(f'获取{date}的统计图: 共获取到{len(recs)}条消息')
    if len(recs) == 0: return f"{date} 的消息记录为空"
    # 统计发言数
    user_count = Counter()
    for rec in recs: user_count.inc(rec['user_id'])
    sorted_user_count = sorted(user_count.items(), key=lambda x: x[1], reverse=True)
    # 计算出需要的topk
    need_k = len(sorted_user_count)
    topk_user = [user for user, _ in sorted_user_count[:need_k]]
    # 获取topk的名字
    topk_name = []
    for user in topk_user:
        try:
            name = truncate(await get_group_member_name(group_id, user), NAME_LEN_LIMIT_CFG.get())
            topk_name.append(name)
        except:
            topk_name.append(str(user))
    # 发送图片
    return await get_image_cq(
        await draw_sta(group_id, recs, PLOT_INTERVAL_CFG.get(), PLOT_TOPK1_CFG.get(), PLOT_TOPK2_CFG.get(), topk_user, topk_name, date),
    )

# 获取长时间统计数据
async def get_long_statistic(group_id, start_date: datetime, end_date: datetime):
    start_time = start_date.strftime("%Y-%m-%d 00:00:00")
    end_time   = end_date.strftime("%Y-%m-%d 23:59:59")
    start_time = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
    end_time   = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    recs = await query_msg_by_range(group_id, start_time, end_time)
    logger.info(f'绘制从{start_date}到{end_date}的长时间统计图: 共获取到{len(recs)}条消息')

    if len(recs) == 0: return f"从{start_date}到{end_date}的消息记录为空"

    # 统计发言数
    user_count = Counter()
    for rec in recs: user_count.inc(rec['user_id'])
    sorted_user_count = sorted(user_count.items(), key=lambda x: x[1], reverse=True)
    # 计算出需要的topk
    need_k = len(sorted_user_count)
    topk_user = [user for user, _ in sorted_user_count[:need_k]]
    # 获取topk的名字
    topk_name = []
    for user in topk_user:
        try:
            name = truncate(await get_group_member_name(group_id, user), NAME_LEN_LIMIT_CFG.get())
            topk_name.append(name)
        except:
            topk_name.append(str(user))
    # 画图
    date = f"{start_date.strftime('%Y-%m-%d')}~{end_date.strftime('%Y-%m-%d')}"
    return await get_image_cq(
        await draw_sta_sum(group_id, recs, PLOT_INTERVAL_CFG.get(), PLOT_TOPK1_CFG.get(), PLOT_TOPK2_CFG.get(), topk_user, topk_name, date),
    )

# 获取总消息量关于时间的统计图数据
async def get_date_count_statistic(group_id, days, user_id=None):
    t = datetime.now()
    dates, counts = [], []
    user_counts = None if user_id is None else []
    for i in range(days):
        date = (t - timedelta(days=i)).strftime("%Y-%m-%d")
        start_time = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d 00:00:00")
        end_time   = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d 23:59:59")
        cnt = await query_msg_count(group_id, 
                         datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S"), 
                         datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S"))
        if user_id is not None:
            user_cnt = await query_msg_count(group_id,
                                datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S"), 
                                datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S"),
                                user_id)
            user_counts.append(user_cnt)
        dates.append(datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S"))
        counts.append(cnt)
    with TempFilePath(".png") as save_path:
        draw_date_count_plot(dates, counts, save_path, user_counts)
        return await get_image_cq(save_path)

# 获取某个词的统计图
async def get_word_statistic(group_id, days, word):
    words = word.split('，') if '，' in word else word.split(',')
    t = datetime.now()
    dates = []
    user_counts = Counter()
    user_date_counts = [Counter() for _ in range(days)]
    for i in range(days):
        date = (t - timedelta(days=i)).strftime("%Y-%m-%d")
        start_time = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d 00:00:00")
        end_time   = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d 23:59:59")
        msgs = await query_msg_by_range(group_id,
                         datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S"), 
                         datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S"))
        for msg in msgs:
            text = extract_text(msg['msg'])
            if any([word in text for word in words]):
                user_counts.inc(str(msg['user_id']))
                user_date_counts[i].inc(str(msg['user_id']))
        dates.append(datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S"))
    sorted_user_counts = sorted(user_counts.items(), key=lambda x: x[1], reverse=True)
    topk_user = [str(user) for user, _ in sorted_user_counts[:STA_WORD_TOPK_CFG.get()]]
    topk_name = []
    for user in topk_user:
        try:
            name = await get_group_member_name(group_id, user)
            topk_name.append(name)
        except:
            topk_name.append(str(user))
    with TempFilePath(".png") as save_path:
        draw_word_count_plot(dates, topk_user, topk_name, user_counts, user_date_counts, word, save_path)
        return await get_image_cq(save_path)

# ------------------------------------------------ 聊天逻辑 ------------------------------------------------


# 发送每日统计图
sta = CmdHandler(["/sta day",], logger)
sta.check_cdrate(cd).check_wblist(gbl).check_group()
@sta.handle()
async def _(ctx: HandlerContext):
    try:
        date = ctx.get_args().strip()
        if date.count('-') == 2:
            datetime.strptime(date, "%Y-%m-%d")
        else:
            delta_day = int(date)
            date = (datetime.now() + timedelta(days=delta_day)).strftime("%Y-%m-%d")
    except:
        logger.info(f'日期格式错误, 使用当前日期')
        date = None
    res = await get_day_statistic(ctx.group_id, date)
    return await ctx.asend_reply_msg(res)


# 发送长时间统计图
sta_sum = CmdHandler(["/sta_sum", "/sta_summary"], logger)
sta_sum.check_cdrate(cd).check_wblist(gbl).check_group()
@sta_sum.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip().split()
    end_date, start_date = None, None

    try:
        if len(args) == 1:
            args = args[0].strip().replace('/', '-')
            if args.count('-') == 0:
                days = int(args)
                end_date = datetime.now()
                start_date = (end_date - timedelta(days=days))
            else:
                start_date = datetime.strptime(args, "%Y-%m-%d")
                end_date = datetime.now()
        elif len(args) == 2:
            st = args[0].strip().replace('/', '-')
            ed = args[1].strip().replace('/', '-')
            start_date = datetime.strptime(st, "%Y-%m-%d")
            end_date = datetime.strptime(ed, "%Y-%m-%d")
        else:
            raise ValueError
    except:
        assert_and_reply(False, f"""
使用方式: 
/sta_sum [起始日期] [结束日期]
/sta_sum [起始日期]
/sta_sum [天数]
""".strip())

    res = await get_long_statistic(ctx.group_id, start_date, end_date)
    return await ctx.asend_reply_msg(res)


# 发送总消息量关于时间的统计图
sta_time = CmdHandler(["/sta_time"], logger)
sta_time.check_cdrate(cd).check_wblist(gbl).check_group()
@sta_time.handle()
async def _(ctx: HandlerContext):
    cqs = extract_cq_code(ctx.get_msg())
    user_id = None
    if 'at' in cqs and len(cqs['at']) > 0:
        user_id = cqs['at'][0]['qq']
    try:
        days = int(ctx.get_args().strip().split()[0])
    except:
        logger.info(f'日期格式错误, 使用默认30天')
        days = 30
    res = await get_date_count_statistic(ctx.group_id, days, user_id)
    return await ctx.asend_reply_msg(res)


# 发送某个词的统计图
sta_word = CmdHandler(["/sta_word"], logger)
sta_word.check_cdrate(cd).check_wblist(gbl).check_group()
@sta_word.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip().split()
    assert_and_reply(args, "请输入需要查询的词")
    word = args[0]
    try:
        days = int(args[1])
    except:
        logger.info(f'日期格式错误, 使用默认30天')
        days = 30
    res = await get_word_statistic(ctx.group_id, days, word)
    return await ctx.asend_reply_msg(res)


# 添加用户词汇
msgadd = CmdHandler(["/sta_add"], logger)
msgadd.check_superuser().check_wblist(gbl)
@msgadd.handle()
async def _(ctx: HandlerContext):
    words = ctx.get_args().strip().split()
    assert_and_reply(words, "请输入需要添加的词")
    userwords = file_db.get("userwords", [])
    stopwords = file_db.get("stopwords", [])
    for word in words:
        if word in stopwords: stopwords.remove(word)
        if word not in userwords: userwords.append(word)
    file_db.set("userwords", userwords)
    file_db.set("stopwords", stopwords)
    reset_jieba()
    return await ctx.asend_reply_msg(f"成功添加{len(words)}条用户词汇")
   

# 添加停用词汇
msgban = CmdHandler(["/sta_ban"], logger)
msgadd.check_superuser().check_wblist(gbl)
@msgban.handle()
async def _(ctx: HandlerContext):
    words = ctx.get_args().strip().split()
    if len(words) == 0:
        return await msgban.finish("输入为空")
    userwords = file_db.get("userwords", [])
    stopwords = file_db.get("stopwords", [])
    for word in words:
        if word in userwords: userwords.remove(word)
        if word not in stopwords: stopwords.append(word)
    file_db.set("userwords", userwords)
    file_db.set("stopwords", stopwords)
    reset_jieba()
    return await ctx.asend_reply_msg(f"成功添加{len(words)}条停用词汇")
 

# ------------------------------------------------ 定时任务 ------------------------------------------------


# 定时统计消息
@scheduler.scheduled_job("cron", hour=STATICSTIC_TIME[0], minute=STATICSTIC_TIME[1], second=STATICSTIC_TIME[2])
async def cron_statistic():
    for group_id in notify_gwl.get():
        if group_id in gbl.get(): continue
        
        cancel_date = file_db.get("cancel_date", {})
        if group_id in cancel_date and cancel_date[group_id] == datetime.now().strftime("%Y-%m-%d"):
            logger.info(f'{group_id} 今天取消了统计图发送', flush=True)
            continue

        logger.info(f'尝试发送 {group_id} 统计图', flush=True)
        try:
            res = await get_day_statistic(group_id)
            await send_group_msg_by_bot(group_id, res)
        except Exception as e:
            logger.print_exc(f'发送 {group_id} 统计图失败')


# 取消今天的统计自动发送
cancel_today = CmdHandler("/sta_cancel_today", logger)
cancel_today.check_superuser().check_group().check_wblist(gbl).check_wblist(notify_gwl)
@cancel_today.handle()
async def _(ctx: HandlerContext):
    group_id = ctx.group_id
    cancel_date = file_db.get("cancel_date", {})
    if group_id in cancel_date and cancel_date[group_id] == datetime.now().strftime("%Y-%m-%d"):
        cancel_date.pop(group_id)
        file_db.set("cancel_date", cancel_date)
        return await ctx.asend_reply_msg(f'恢复今天的统计自动发送')
    else:
        cancel_date[group_id] = datetime.now().strftime("%Y-%m-%d")
        file_db.set("cancel_date", cancel_date)
        return await ctx.asend_reply_msg(f'取消今天的统计自动发送')
 