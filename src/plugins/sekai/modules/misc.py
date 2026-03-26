from ...utils import *
from ...record import after_record_hook
from ..common import *
from ..handler import *
from ..asset import *
from ..draw import *
from ..sub import SekaiGroupSubHelper, SekaiUserSubHelper
from .card_extractor import CardExtractor, CardExtractResult, CardThumbnail
from ..gameapi import get_gameapi_config, request_gameapi
from .profile import (
    get_card_full_thumbnail, 
    get_player_bind_id,
    get_player_bind_count,
    process_hide_uid,
)
from .card import (
    has_after_training, 
    only_has_after_training,
    get_character_name_by_id,
    get_card_full_thumbnail,
    get_card_image,
    get_character_sd_image,
)


md_update_group_sub = SekaiGroupSubHelper("update", "MasterData更新通知", ALL_SERVER_REGIONS)
ad_result_sub = SekaiUserSubHelper("ad", "广告奖励推送", ['jp'], hide=True)


# ======================= 指令处理 ======================= #

pjsk_update = SekaiCmdHandler([
    "/pjsk update", "/pjsk refresh", "/pjsk更新",
])
pjsk_update.check_cdrate(cd).check_wblist(gbl)
@pjsk_update.handle()
async def _(ctx: SekaiHandlerContext):
    mgr = RegionMasterDbManager.get(ctx.region)
    msg = f"{get_region_name(ctx.region)}MasterData数据源"
    for source in await mgr.get_all_sources(force_update=True):
        msg += f"\n[{source.name}] {source.version}"
    return await ctx.asend_reply_msg(msg.strip())


ngword = SekaiCmdHandler([
    "/pjsk ng", "/pjsk ngword", "/pjsk ng word",
    "/pjsk屏蔽词", "/pjsk屏蔽", "/pjsk敏感", "/pjsk敏感词",
])
ngword.check_cdrate(cd).check_wblist(gbl)
@ngword.handle()
async def _(ctx: SekaiHandlerContext):
    text = ctx.get_args()
    assert_and_reply(text, "请输入要查询的文本")
    words = await ctx.md.ng_words.get()
    def check():
        ret = []
        for word in words:
            if word in text:
                ret.append(word)
        return ret
    ret = await run_in_pool(check)
    if ret:
        await ctx.asend_reply_msg(f"检测到屏蔽词：{', '.join(ret)}")
    else:
        await ctx.asend_reply_msg("未检测到屏蔽词")


upload_help = SekaiCmdHandler([
    "/抓包帮助", "/抓包", "/pjsk upload help",
])
upload_help.check_cdrate(cd).check_wblist(gbl)
@upload_help.handle()
async def _(ctx: SekaiHandlerContext):
    text = Path(f"{SEKAI_CONFIG_DIR}/upload_help.txt").read_text(encoding="utf-8")
    return await ctx.asend_msg(text.strip())


card_extractor = CardExtractor()
extract_card = SekaiCmdHandler([
    "/提取卡牌"
], regions=['jp'])
extract_card.check_cdrate(cd).check_wblist(gbl)
@extract_card.handle()
async def _(ctx: SekaiHandlerContext):
    await ctx.block()
    global card_extractor
    bot, event = ctx.bot, ctx.event
    reply_msg = ctx.get_reply_msg()
    assert_and_reply(reply_msg, f"请回复一张图片")
    cqs = extract_cq_code(reply_msg)
    assert_and_reply('image' in cqs, f"请回复一张图片")
    img = await download_image(cqs['image'][0]['url'])
    
    if not card_extractor.is_initialized():
        card_thumbs = []
        for card in await ctx.md.cards.get():
            card_id = card['id']
            rarity = card['cardRarityType']
            attr = card['attr']
            assetbundle_name = card['assetbundleName']
            img_dir = 'data/sekai/assets/rip/jp/thumbnail/chara_rip'
            if not only_has_after_training(card):
                normal_path = await ctx.rip.get_asset_cache_path(f'thumbnail/chara_rip/{assetbundle_name}_normal.png')
                if normal_path:
                    card_thumbs.append(CardThumbnail(
                        id=card_id,
                        rarity=rarity,
                        attr=attr,
                        is_aftertraining=False,
                        img_path=os.path.join(img_dir, f"{assetbundle_name}_normal.png"),
                    ))
            if has_after_training(card):
                aftertraining_path = await ctx.rip.get_asset_cache_path(f'thumbnail/chara_rip/{assetbundle_name}_after_training.png')
                if aftertraining_path:
                    card_thumbs.append(CardThumbnail(
                        id=card_id,
                        rarity=rarity,
                        attr=attr,
                        is_aftertraining=True,
                        img_path=os.path.join(img_dir, f"{assetbundle_name}_after_training.png"),
                    ))
        t = datetime.now()
        await run_in_pool(card_extractor.init, card_thumbs)
        logger.info(f"CardExtractor initialized in {datetime.now() - t} seconds")
    
    t = datetime.now()
    result: CardExtractResult = await run_in_pool(card_extractor.extract_cards, img)
    logger.info(f"CardExtractor extracted {len(result.cards)} cards in {datetime.now() - t} seconds")
    
    with Canvas(bg=FillBg(WHITE)).set_padding(BG_PADDING) as canvas:
        with Grid(col_count=result.grid.cols).set_sep(8, 8):
            for row_idx in range(result.grid.rows):
                for col_idx in range(result.grid.cols):
                    with HSplit().set_sep(0):
                        w = 64
                        try:
                            import cv2
                            img = result.grid.get_grid_image(row_idx, col_idx)
                            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                            img = Image.fromarray(img)
                            ImageBox(img, size=(w, w))
                        except:
                            Spacer(w, w)
                            Spacer(w, w)
                            continue

                        card = find_by_predicate(result.cards, lambda c: c.row_idx == row_idx and c.col_idx == col_idx)
                        if card is None:
                            ImageBox(UNKNOWN_IMG, size=(w, w))
                        else:
                            pcard = {
                                'defaultImage': "special_training" if card.is_aftertraining else "normal",
                                'specialTrainingStatus': "done" if card.is_aftertraining else "none",
                                'level': card.level,
                                'masterRank': card.master_rank,
                            }
                            custom_text = None if card.level is not None else f"SLv.{card.skill_level}"
                            thumb = await get_card_full_thumbnail(ctx, card.id, pcard=pcard, custom_text=custom_text)
                            ImageBox(thumb, size=(w, w))
        
    return await ctx.asend_reply_msg(
        await get_image_cq(
            await canvas.get_img(),
            low_quality=True,
        )
    )


chara_bd = SekaiCmdHandler([
    "/pjsk chara birthday", "/角色生日", "/生日",
])
chara_bd.check_cdrate(cd).check_wblist(gbl)
@chara_bd.handle()
async def _(ctx: SekaiHandlerContext):
    # 获取角色生日信息
    async def get_bd_info(cid: int) -> dict:
        info = { 'cid': cid }
        m, d = get_character_birthday(cid)
        info['month'] = m
        info['day'] = d
        info['next'] = get_character_next_birthday_dt(ctx.region, cid)

        for card in await ctx.md.cards.get():
            if card['characterId'] == cid and card['cardRarityType'] == 'rarity_birthday':
                info.setdefault('cards', []).append(card)
        return info

    args = ctx.get_args().strip()

    bd_infos: list[dict] = [await get_bd_info(i) for i in range(1, 27)]
    bd_infos.sort(key=lambda x: x['next'])

    # 判断是否五周年
    is_fifth_anniv = is_fifth_anniversary(ctx.region)

    if not args:
        info = bd_infos[0]
    elif args.isdigit():
        idx = int(args) - 1
        assert_and_reply(0 <= idx < len(bd_infos), "角色生日索引超出范围")
        info = bd_infos[idx]
    else:
        cid = get_cid_by_nickname(args)
        assert_and_reply(cid, f"""
使用方式:
查询最近的角色生日: "{ctx.original_trigger_cmd}"
查询第二近的角色生日: "{ctx.original_trigger_cmd} 2"
查询指定角色下次生日: "{ctx.original_trigger_cmd} 角色名"
""".strip())
        info = find_by(bd_infos, 'cid', cid)

    style1 = TextStyle(DEFAULT_BOLD_FONT, 24, BLACK)
    style2 = TextStyle(DEFAULT_FONT, 20, BLACK)

    card_thumbs = await batch_gather(*[get_card_full_thumbnail(ctx, card, False) for card in info['cards']])
    card_image = await get_card_image(ctx, random.choice(info['cards']), False)
    next_time: datetime = info['next']
    month = info['month']
    day = info['day']

    if is_fifth_anniv:
        gacha_start,    gacha_end   = next_time - timedelta(days=4), next_time + timedelta(days=3)
        live_start,     live_end    = next_time - timedelta(days=0), next_time + timedelta(days=1)
        drop_start,     drop_end    = next_time - timedelta(days=3), next_time + timedelta(days=0)
        flower_start,   flower_end  = next_time - timedelta(days=3), next_time + timedelta(days=3)
        party_start,    party_end   = next_time - timedelta(days=0), next_time + timedelta(days=3)
    else:
        gacha_start,    gacha_end   = next_time - timedelta(days=0), next_time + timedelta(days=7)
        live_start,     live_end    = next_time - timedelta(days=0), next_time + timedelta(days=1)

    def draw_time_range(label: str, start: datetime, end: datetime):
        end = end - timedelta(minutes=1)
        with HSplit().set_sep(8).set_content_align('l').set_item_align('l'):
            TextBox(f"{label} ", style1)
            start_text = f"{start.strftime('%m-%d %H:%M')}({get_readable_datetime(start, False)})"
            end_text = f"{end.strftime('%m-%d %H:%M')}({get_readable_datetime(end, False)})"
            TextBox(f"{start_text} ~ {end_text}", style2)

    cid = info['cid']
    colorcode = (await ctx.md.game_character_units.find_by_id(cid))['colorCode']

    with Canvas(bg=ImageBg(card_image)).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('c').set_item_align('c').set_padding(16).set_sep(8) \
            .set_item_bg(roundrect_bg()).set_bg(roundrect_bg()):
        
            with HSplit().set_sep(16).set_padding(16).set_content_align('c').set_item_align('c'):
                ImageBox(await get_character_sd_image(cid), size=(None, 80), shadow=True)
                title_img = await SekaiHandlerContext.from_region("jp").rip.img(f"character/label_horizontal/chr_h_lb_{cid}.png")
                ImageBox(title_img, size=(None, 60))
                TextBox(f"{month}月{day}日", 
                        TextStyle(DEFAULT_HEAVY_FONT, 32, (100, 100, 100), 
                                  use_shadow=True, shadow_offset=2, shadow_color=color_code_to_rgb(colorcode)))

            with VSplit().set_sep(4).set_padding(16).set_content_align('l').set_item_align('l'):
                with HSplit().set_sep(8).set_padding(0).set_content_align('l').set_item_align('l'):
                    TextBox(f"({get_region_name(ctx.region)}) 距离下次生日还有{(next_time - datetime.now()).days}天", style1)
                    Spacer(w=16)
                    TextBox(f"应援色", style1)
                    TextBox(colorcode, TextStyle(DEFAULT_FONT, 20, ADAPTIVE_WB)) \
                        .set_bg(RoundRectBg(color_code_to_rgb(colorcode), radius=4)).set_padding(8)

                draw_time_range("🎰卡池开放时间", gacha_start, gacha_end)
                draw_time_range("🎤虚拟LIVE时间", live_start, live_end)

            if is_fifth_anniv:
                with VSplit().set_sep(4).set_padding(16).set_content_align('l').set_item_align('l'):
                    draw_time_range("💧露滴掉落时间", drop_start, drop_end)
                    draw_time_range("🌱浇水开放时间", flower_start, flower_end)
                    draw_time_range("🎂派对开放时间", party_start, party_end)

            with HSplit().set_sep(4).set_padding(16).set_content_align('l').set_item_align('l'):
                TextBox(f"卡牌", style1)
                Spacer(w=8)
                with Grid(col_count=6).set_sep(4, 4):
                    for i in range(len(card_thumbs)):
                        with VSplit().set_sep(2).set_content_align('c').set_item_align('c'):
                            ImageBox(card_thumbs[i], size=(80, 80), shadow=True)
                            TextBox(f"{info['cards'][i]['id']}", TextStyle(DEFAULT_FONT, 16, (50, 50, 50)))
                
            with Grid(col_count=13).set_sep(2, 2).set_padding(16).set_content_align('c').set_item_align('c'):
                idx = 0
                start_cid = 6
                for i, item in enumerate(bd_infos):
                    if item['cid'] == start_cid:
                        idx = i
                        break
                for _ in range(len(bd_infos)):
                    chara_id = bd_infos[idx % len(bd_infos)]['cid']
                    idx += 1
                    with VSplit().set_sep(0).set_content_align('c').set_item_align('c'):
                        b = ImageBox(get_chara_icon_by_chara_id(chara_id), size=(40, 40)).set_padding(4)
                        if chara_id == cid:
                            b.set_bg(roundrect_bg(radius=8))
                        month, day = get_character_birthday(chara_id)
                        TextBox(f"{month}/{day}", TextStyle(DEFAULT_FONT, 14, (50, 50, 80)))

    add_watermark(canvas)

    return await ctx.asend_reply_msg(
        await get_image_cq(
            await canvas.get_img(),
            low_quality=True,
        )
    )
            


heyiwei = SekaiCmdHandler(["/pjsk detail", ])
heyiwei.check_cdrate(cd).check_wblist(gbl)
@heyiwei.handle()
async def _(ctx: SekaiHandlerContext):
    return await ctx.asend_reply_msg('使用"/挑战信息"和"/加成信息"查询相关内容')


# ======================= 定时通知 ======================= #

# masterdata更新通知
@RegionMasterDbManager.on_update()
async def send_masterdata_update_notify(
    region: str, source: str,
    version: str, last_version: str,
    asset_version: str, last_asset_version: str,
):
    region_name = get_region_name(region)

    # 防止重复通知
    last_notified_version = file_db.get(f"last_notified_md_version_{region}", None)
    if last_notified_version and get_version_order(last_notified_version) >= get_version_order(version):
        return
    file_db.set(f"last_notified_md_version_{region}", version)

    msg = f"从{source}获取{region_name}的MasterData版本更新: {last_version} -> {version}\n"
    if last_asset_version != asset_version:
        msg += f"解包资源版本: {last_asset_version} -> {asset_version}\n"
    msg = msg.strip()

    for group_id in md_update_group_sub.get_all(region):
        if not gbl.check_id(group_id): continue
        try:
            await send_group_msg_by_bot(group_id, msg)
        except Exception as e:
            logger.print_exc(f"在群聊发送 {group_id} 发送 {region} MasterData更新通知失败")
            continue


# 广告奖励推送
@repeat_with_interval(5, '广告奖励推送', logger)
async def msr_auto_push():
    for region in ALL_SERVER_REGIONS:
        region_name = get_region_name(region)
        ctx = SekaiHandlerContext.from_region(region)

        update_time_url = get_gameapi_config(ctx).ad_result_update_time_api_url
        result_url = get_gameapi_config(ctx).ad_result_api_url
        if not update_time_url or not result_url: continue
        if region not in ad_result_sub.regions: continue

        # 获取订阅的用户列表
        qids = list(set([qid for qid, gid in ad_result_sub.get_all_gid_uid(region)]))
        uids = set()
        for qid in qids:
            for i in range(get_player_bind_count(ctx, qid)):
                try:
                    if uid := get_player_bind_id(ctx, qid, index=i):
                        uids.add(uid)
                except:
                    pass
        if not uids: continue

        # 获取广告奖励更新时间
        try:
            update_times = await request_gameapi(update_time_url)
        except Exception as e:
            logger.warning(f"获取{region_name}广告奖励更新时间失败: {get_exc_desc(e)}")
            continue

        need_push_uids = [] # 需要推送的uid（没有距离太久的）
        for uid in uids:
            update_ts = update_times.get(uid, 0)
            if datetime.now() - datetime.fromtimestamp(update_ts) < timedelta(minutes=10):
                need_push_uids.append(uid)

        tasks = []
                
        for qid, gid in ad_result_sub.get_all_gid_uid(region):
            if check_in_blacklist(qid): continue
            if not gbl.check_id(gid): continue

            for i in range(get_player_bind_count(ctx, qid)):
                ad_result_pushed_time = file_db.get(f"{region}_ad_result_pushed_time", {})

                uid = get_player_bind_id(ctx, qid, index=i)
                if not uid or uid not in need_push_uids:
                    continue

                # 检查这个uid-qid是否已经推送过
                update_ts = int(update_times.get(uid, 0))
                key = f"{uid}-{qid}"
                if key in ad_result_pushed_time:
                    last_push_ts = int(ad_result_pushed_time.get(key, 0))
                    if last_push_ts >= update_ts:
                        continue
                ad_result_pushed_time[key] = update_ts
                file_db.set(f"{region}_ad_result_pushed_time", ad_result_pushed_time)
                
                tasks.append((gid, qid, uid))

        async def push(task):
            gid, qid, uid = task
            try:
                logger.info(f"在 {gid} 中自动推送用户 {qid} 的广告奖励")

                res = await request_gameapi(result_url.format(uid=uid))
                if not res.get('results'):
                    return
                
                msg = f"[CQ:at,qq={qid}]的{region_name}广告奖励\n"
                msg += f"{datetime.fromtimestamp(res['time']).strftime('%Y-%m-%d %H:%M:%S')}\n"
                msg += "\n".join(res['results'])

                await send_group_msg_by_bot(gid, msg.strip())
            except Exception as e:
                logger.print_exc(f'在 {gid} 中自动推送用户 {qid} 的{region_name}广告奖励失败')
                try: await send_group_msg_by_bot(gid, f"自动推送用户 [CQ:at,qq={qid}] 的{region_name}广告奖励失败: {get_exc_desc(e)}")
                except: pass

        await batch_gather(*[push(task) for task in tasks])
