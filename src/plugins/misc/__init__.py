from ..utils import *
import yappi

config = Config('misc')
logger = get_logger("Misc")
file_db = get_file_db("data/misc/db.json", logger)
gbl = get_group_black_list(file_db, logger, "misc")
cd = ColdDown(file_db, logger)


profiler = CmdHandler(['/profiling', '/性能分析'], logger)
profiler.check_cdrate(cd).check_wblist(gbl).check_superuser()
@profiler.handle()
async def _(ctx: HandlerContext):
    if not yappi.is_running():
        args = ctx.get_args().strip()
        assert_and_reply(args in ('cpu', 'wall', ''), "参数错误，仅支持 'cpu' 或 'wall' 作为参数")
        clock_type = args or 'wall'
        yappi.start()
        logger.info(f"性能分析已启动 (clock_type={clock_type})")
        await ctx.asend_reply_msg(f"性能分析已启动 (clock_type={clock_type})。再次使用此命令以完成分析")
    else:
        yappi.stop()
        clock_type = yappi.get_clock_type()
        stats = yappi.get_func_stats()
        save_path = f"data/misc/profiler/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{clock_type}.prof"
        create_parent_folder(save_path)
        stats.save(save_path, type="pstat")
        yappi.clear_stats()
        logger.info(f"性能分析已完成，结果已保存至 {save_path}")
        await ctx.asend_reply_msg(f"性能分析已完成")
