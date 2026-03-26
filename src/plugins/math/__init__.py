from ..utils import *
from asteval import Interpreter
from .oeis import oeis_query

config = Config('math')
logger = get_logger("Math")
file_db = get_file_db("data/math/db.json", logger)
cd = ColdDown(file_db, logger)
gbl = get_group_black_list(file_db, logger, 'math')


_aeval = None

eval = CmdHandler(["/eval", "/计算"], logger)
eval.check_cdrate(cd).check_wblist(gbl)
@eval.handle()
async def _(ctx: HandlerContext):
    expr = ctx.get_args().strip()
    assert_and_reply(expr, "请输入表达式")
    logger.info(f"计算 {expr}")
    global _aeval
    if _aeval is None:
        _aeval = Interpreter()
    result = _aeval(expr)
    return await ctx.asend_reply_msg(str(result))


query = CmdHandler(["/oeis"], logger)
query.check_cdrate(cd).check_wblist(gbl)
@query.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    sequences = await oeis_query(args, n=config.get('oeis_search_num'))
    logger.info(f"查询 OEIS 序列: {args} 共 {len(sequences)} 条结果")
    assert_and_reply(sequences, "未找到相关序列")
    msg = ""
    for seq in sequences:
        msg += f"【{seq.id}】{seq.name}\n"
        msg += f"{seq.sequence}\n"
        msg += f"Formula: {seq.formula}\n"
        msg += "\n"
    return await ctx.asend_fold_msg_adaptive(msg.strip())

