from ..utils import *
from .imgexp import search_image
import yt_dlp
from tenacity import retry, wait_fixed, stop_after_attempt
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


config = Config('imgexp')
logger = get_logger('ImgExp')
file_db = get_file_db('data/imgexp/imgexp.json', logger)
cd = ColdDown(file_db, logger)
gbl = get_group_black_list(file_db, logger, 'imgexp')


# ==================== 图像反查 ==================== #

search = CmdHandler(['/search', '/搜图'], logger)
search.check_cdrate(cd).check_wblist(gbl)
@search.handle()
async def _(ctx: HandlerContext):
    img_data = await ctx.aget_image_datas(return_first=True)
    img, results = await search_image(img_data['url'], int(img_data.get('file_size', 0)))
    msg = ""
    for result in results:
        if result.results:
            msg += f"来自 {result.source} 的结果:\n"
            for i, item in enumerate(result.results):
                msg += f"#{i+1}\n{item.url}\n"
    return await ctx.asend_fold_msg([await get_image_cq(img), msg.strip()])


# ==================== 视频下载 ==================== #

async def aget_video_info(url):
    def get_video_info(url):
        with yt_dlp.YoutubeDL({}) as ydl:
            info = ydl.extract_info(url, download=False)
            info = ydl.sanitize_info(info)
        return info
    return await run_in_pool(get_video_info, url)

async def adownload_video(url, path, maxsize, lowq):
    def download_video(url, path, maxsize):
        opts = {
            'format': config.get('ytdlp.best_format') if not lowq else config.get('ytdlp.worst_format'),
            'outtmpl': path,
            'noplaylist': True,
            'progress_hooks': [],
            'max_filesize': maxsize,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    return await run_in_pool(download_video, url, path, maxsize)

ytdlp = CmdHandler(['/yt-dlp', '/ytdlp', '/yt_dlp', '/video', '/xvideo'], logger)
ytdlp.check_cdrate(cd).check_wblist(gbl, allow_private=True)
@ytdlp.handle()
async def _(ctx: HandlerContext):
    parser = ctx.get_argparser()
    parser.add_argument('url', type=str)
    parser.add_argument('--info', '-i', action='store_true')
    parser.add_argument('--gif', '-g', action='store_true')
    parser.add_argument('--low-quality', '-l', action='store_true')
    args = await parser.parse_args(error_reply=
"""
使用方式: /ytdlp <url> [-i] [-g] [-l]
-i: 仅获取视频信息 -l: 下载低质量视频 -g: 转换为GIF(自动压缩)
示例: /ytdlp https://www.youtube.com/watch?v=xxxx -g
"""
.strip())

    if args.info:
        logger.info(f'获取视频信息: {args.url}')
        info = await aget_video_info(args.url)

        title = info.get('title', '')
        uploader = info.get('uploader', '')
        description = info.get('description', '')
        thumbnail = info.get('thumbnail', '')
        video_url = info.get('url', '')
        ext = info.get('ext', '')
        logger.info(f'获取视频信息: title={title} video_url={video_url}')

        msg = ""
        if title:
            msg += f"Title: {title}\n"
        if uploader:
            msg += f"Uploader: {uploader}\n"
        if description:
            msg += f"{description}\n"
        if thumbnail:
            msg += f"{await get_image_cq(thumbnail, allow_error=True, logger=logger)}\n" 
        if video_url:
            msg += f"{video_url}"
        return await ctx.asend_fold_msg_adaptive(msg.strip())

    else:
        logger.info(f'下载视频: {args.url}')

        with TempFilePath("mp4") as tmp_save_path:
            await ctx.asend_reply_msg("正在下载视频...")

            download_size_limit = int(config.get('ytdlp.size_limit') * 1024 * 1024)
            await adownload_video(args.url, tmp_save_path, download_size_limit, args.low_quality)

            if not os.path.exists(tmp_save_path):
                return await ctx.asend_reply_msg(f"视频下载失败，可能是超过大小限制({download_size_limit/1024/1024:.1f}MB)或其他原因")

            if os.path.getsize(tmp_save_path) > download_size_limit:
                return await ctx.asend_reply_msg(f"视频大小超过限制")

            if args.gif:
                with TempFilePath("gif") as gif_path:
                    await run_in_pool(convert_video_to_gif, tmp_save_path, gif_path)
                    await ctx.asend_msg(await get_image_cq(gif_path))
            else:
                await ctx.asend_video(tmp_save_path)


# ==================== 图片下载 ==================== #

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), reraise=True)
async def get_x_content(url: str) -> tuple[str, list[str]]:
    """从 X 帖子 URL 中提取文本内容和图片链接。"""
    image_urls = []

    async def block_agressive_resources(route):
        """拦截图片以外的非必要资源"""
        if route.request.resource_type in ["font", "stylesheet", "media", "websocket"]:
            await route.abort()
        elif "google-analytics" in route.request.url or "monitor" in route.request.url:
            await route.abort()
        else:
            await route.continue_()
    
    async with PlaywrightPage() as page:
        await page.route("**/*", block_agressive_resources)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)

            # 等待推文核心内容出现
            tweet_selector = 'article[data-testid="tweet"]'
            try:
                await page.wait_for_selector(tweet_selector, state="visible", timeout=10000)
            except PlaywrightTimeoutError:
                raise ReplyException(f"未能找到推文内容，可能是由登录墙、已被删除或网络超时引起，请稍后再试")

            # 处理“敏感内容”或“显示更多”按钮
            sensitive_overlay_selector = '[data-testid="tweet"] div[role="button"]:has-text("View"), [data-testid="tweet"] div[role="button"]:has-text("Show")'
            if await page.locator(sensitive_overlay_selector).count() > 0:
                try:
                    # 点击所有覆盖层
                    overlays = await page.locator(sensitive_overlay_selector).all()
                    for overlay in overlays:
                        if await overlay.is_visible():
                            await overlay.click(force=True)
                            await page.wait_for_timeout(500) # 给一点渲染时间
                except Exception as e:
                    raise ReplyException(f"尝试点击敏感内容遮罩时出错: {get_exc_desc(e)}")

            # 提取图片
            photo_selector = 'div[data-testid="tweetPhoto"] img'
            try:
                await page.wait_for_selector(photo_selector, state="attached", timeout=3000)
            except PlaywrightTimeoutError:
                pass

            img_locators = await page.locator(photo_selector).all()
            
            for locator in img_locators:
                src = await locator.get_attribute("src")
                if src:
                    # URL 清洗/优化
                    clean_src = src
                    if "pbs.twimg.com/media" in src:
                        if "name=" in src:
                            clean_src = re.sub(r'name=[a-z0-9]+', 'name=large', src)
                        else:
                            clean_src = src + "&name=large"
                            
                    if clean_src not in image_urls:
                        image_urls.append(clean_src)

            # 提取用户名
            user_locator = page.locator(f'{tweet_selector} [data-testid="User-Name"]')
            username_text = await user_locator.inner_text()
            display_name = username_text.split('\n')[0] if username_text else "Unknown"

            # 提取推文正文
            text_locator = page.locator(f'{tweet_selector} [data-testid="tweetText"]')
            content = ""
            if await text_locator.count() > 0:
                content = await text_locator.inner_text()

            full_content = f"{display_name}: {content.strip()}"

        except Exception as e:
            # 保存调试用页面截图
            screenshot_path = f"data/imgexp/debug/x_{int(time.time())}.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            raise e

    return full_content, image_urls


ximg = CmdHandler(['/x img', '/tw img', '/推图'], logger)
ximg.check_cdrate(cd).check_wblist(gbl, allow_private=True)
@ximg.handle()
async def _(ctx: HandlerContext):
    parser = ctx.get_argparser()
    parser.add_argument('url', type=str)
    parser.add_argument('--vertical',   '-V', action='store_true')
    parser.add_argument('--horizontal', '-H', action='store_true')
    parser.add_argument('--grid',       '-G', action='store_true')
    parser.add_argument('--fold',       '-f', action='store_true')
    parser.add_argument('--gif',        '-g', action='store_true')
    args = await parser.parse_args(error_reply=(
"""
使用方式: /ximg <url> [-V] [-H] [-G] [-f]
-V: 垂直拼图 -H: 水平拼图 -G 网格拼图 
-f 折叠回复 -g 转换为GIF
不加参数默认各个图片分开发送
示例: /ximg https://x.com/xxx/status/12345 -G               
"""
.strip()))
    url = args.url
    assert url, '请提供X推文网页链接'
    assert [args.vertical, args.horizontal, args.grid].count(True) <= 1, '只能选择一种拼图模式'
    concat_mode = 'v' if args.vertical else 'h' if args.horizontal else 'g' if args.grid else None

    try:
        logger.info(f'获取X图片链接: {url}')
        content, image_urls = await get_x_content(url)
        image_urls = image_urls[:16]
        logger.info(f'获取到图片链接: {image_urls}')
    except Exception as e:
        logger.error(f'获取X图片链接失败: {get_exc_desc(e)}')
        raise ReplyException(f'获取图片链接失败: {get_exc_desc(e)}')
    
    if not image_urls:
        return await ctx.asend_reply_msg('在推文中没有找到图片，可能是输入网页链接不正确或其他原因')
    
    msg = url + "\n" + truncate(content, 64)

    force_download_image = True     # 不下载到本地再发送可能导致napcat报错

    if force_download_image or concat_mode or args.gif:
        images = await asyncio.gather(*[download_image(u) for u in image_urls])
    else:
        images = image_urls

    if concat_mode:
        concated_image = await run_in_pool(concat_images, images, concat_mode)
        images = [concated_image]

    for img in images:
        if args.gif:
            with TempFilePath("gif", remove_after=timedelta(minutes=3)) as gif_path:
                await run_in_pool(save_transparent_static_gif, img, gif_path)
                msg += await get_image_cq(gif_path)
        elif isinstance(img, Image.Image):
            msg += await get_image_cq(img)
        else:
            msg += await get_image_cq(img, send_url_as_is=True)

    if args.fold:
        return await ctx.asend_fold_msg(msg)
    else:
        return await ctx.asend_msg(msg)


