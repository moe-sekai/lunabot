from ..utils import *
import pandas as pd
from pdf2image import convert_from_path

config = Config('bird')
logger = get_logger("Bird")
file_db = get_file_db("data/bird/db.json", logger)
cd = ColdDown(file_db, logger)
gbl = get_group_black_list(file_db, logger, 'bird')


# ================================ 逻辑处理 ================================ #

WIKI_IMAGE_CACHE_PATH = "data/bird/wiki_image_cache/{name}/"
QUERY_TOPK_CFG = config.item('query_topk')
MAX_EDIT_DISTANCE_CFG = config.item('max_edit_distance')
FOLK_NAME_MAX_CFG = config.item('folk_name_max')


# 初始化鸟类列表
_bird_data = None
async def get_birds():
    global _bird_data
    if _bird_data is None:
        _bird_data = pd.read_csv("data/bird/birds.csv", encoding='utf-8')
    return _bird_data


# 获取wiki页面图片，返回图片路径列表
async def get_wiki_page_image(ctx: HandlerContext, name: str, url: str, refresh: bool=False) -> list[str]:
    await ctx.block(name)
    cache_dir = WIKI_IMAGE_CACHE_PATH.format(name=name)
    if not refresh and (files := glob.glob(os.path.join(cache_dir, "*.jpg"))):
        return sorted(files)
    try:
        await ctx.asend_reply_msg(f"正在获取{name}的wiki页面...")
        async with PlaywrightPage() as page:
            await page.goto(url, wait_until='networkidle')
            pdf = await page.pdf(
                format='A4', # 设置页面格式
                print_background=True, # 确保背景打印
                margin={"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"} # 边距设置
            )
        # page.pdf本身返回的就是bytes
        with TempFilePath("pdf") as pdf_path:
            with open(pdf_path, 'wb') as f:
                f.write(pdf)
            pages = convert_from_path(pdf_path, dpi=75)
        remove_folder(cache_dir)
        os.makedirs(cache_dir, exist_ok=True)
        save_paths = []
        for i, page in enumerate(pages):
            save_path = os.path.join(cache_dir, f"{i:02d}.jpg")
            page.save(save_path, 'JPEG', quality=85, optimize=True, subsampling=2, progressive=False)
            save_paths.append(save_path)
        return save_paths
    except Exception as e:
        logger.error(f"获取wiki页面 {name} ({url}) 图片失败: {get_exc_desc(e)}")
        raise ReplyException(f"获取{name}的wiki页面图片失败")


# ================================ 指令处理 ================================ #
        
bird = CmdHandler(['/bird', '/查鸟', '/鸟'], logger)
bird.check_cdrate(cd).check_wblist(gbl)
@bird.handle()
async def handle_bird(ctx: HandlerContext):
    birds = await get_birds()

    bird_name = ctx.get_args().strip()
    refresh = False
    if 'refresh' in bird_name:
        refresh = True
        bird_name = bird_name.replace('refresh', '').strip()

    assert_and_reply(bird_name, "请输入要查询的鸟类名称")

    # 查找精确匹配
    if bird := birds[birds['chinese_name'] == bird_name].to_dict(orient='records'):
        bird = bird[0]
        msgs = []
        scientific_name = bird['scientific_name']
        chinese_name = bird['chinese_name']
        protect_level = bird['protect_level']
        folk_names = bird['alias']
        url = bird['url']

        if protect_level == '-':
            protect_level = "无"
        msgs.append(f"{chinese_name} ({scientific_name})\n保护级别: {protect_level}\n俗名: {folk_names}\nwiki页面: {url}")
        for img_path in await get_wiki_page_image(ctx, chinese_name, url, refresh):
            msgs.append(await get_image_cq(img_path, low_quality=True))
        return await ctx.asend_fold_msg(msgs)
    
    def search_blur_folk():
        # 查找模糊匹配
        all_names = birds['chinese_name'].tolist()
        edit_distance = {}
        for name in all_names:
            edit_distance[name] = levenshtein_distance(bird_name, name)
        edit_distance = sorted(edit_distance.items(), key=lambda x:x[1])
        edit_distance = [x for x in edit_distance if x[1] <= MAX_EDIT_DISTANCE_CFG.get()]
        topk = QUERY_TOPK_CFG.get()
        blur_names = [x[0] for x in edit_distance[:topk]]
        blur_names = blur_names[:topk]
        
        # 查找俗名里面有的
        folk_names = []
        for row in birds.itertuples():
            if bird_name in str(row.alias):
                folk_names.append(row.chinese_name)
        folk_names = folk_names[:FOLK_NAME_MAX_CFG.get()]

        logger.info(f"鸟类查询：{bird_name}，模糊匹配: {blur_names} 俗名匹配: {folk_names}")
        return blur_names, folk_names
    
    blur_names, folk_names = await run_in_pool(search_blur_folk)

    res = f"没有找到这个鸟类哦，该功能目前仅收录国内有分布的鸟类\n"
    if len(folk_names) > 0:
        res += f"\"{bird_name}\"可能是这些鸟的俗名：{', '.join(folk_names)}\n"
    if len(blur_names) > 0:
        res += f"模糊匹配：{', '.join(blur_names)}\n"
    
    return await ctx.asend_reply_msg(res.strip())



    

        





