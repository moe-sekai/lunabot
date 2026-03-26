from ..utils import *
from .common import *
from .handler import *

# ======================= 群聊订阅 ======================= #

class SekaiGroupSubHelper:
    all_subs: List['SekaiGroupSubHelper'] = []

    def __init__(self, id: str, name: str, regions: List[str], hide: bool = False):
        self.id = id
        self.name = name
        self.regions = regions
        self.hide = hide
        self.subs = {
            region: GroupSubHelper(
                f"{name}({region_name})_群聊",
                file_db,
                logger,
            ) for region, region_name in zip(regions, ALL_SERVER_REGION_NAMES)
        }
        SekaiGroupSubHelper.all_subs.append(self)

    @classmethod
    def find_by_ids(cls, ids: List[str]) -> list['SekaiGroupSubHelper']:
        return find_by_predicate(cls.all_subs, lambda s: s.id in ids, mode='all')

    def do_sub(self, ctx: SekaiHandlerContext) -> str:
        if ctx.region not in self.subs:
            return f"❌ 群聊订阅 {self.name} 不支持服务器 {ctx.region}"
        self.subs[ctx.region].sub(ctx.group_id)
        return f"✅ 成功开启本群 {self.name}({get_region_name(ctx.region)})"
    
    def do_unsub(self, ctx: SekaiHandlerContext) -> str:
        if ctx.region not in self.subs:
            return f"❌ 群聊订阅 {self.name} 不支持服务器 {ctx.region}"
        self.subs[ctx.region].unsub(ctx.group_id)
        return f"✅ 成功关闭本群 {self.name}({get_region_name(ctx.region)})"
    
    @classmethod
    def do_get_sub(cls, ctx: SekaiHandlerContext) -> str:
        msg = "当前群聊开启:\n"
        has_sub = False
        for sub in cls.all_subs:
            sub_regions = []
            for region in sub.regions:
                if sub.is_subbed(region, ctx.group_id):
                    sub_regions.append(region)
            if sub_regions:
                msg += f"{sub.name}({', '.join(sub_regions)})\n"
                has_sub = True
        if not has_sub:
            msg += "无\n"
        msg += "---\n"
        msg += "所有可开启项目:\n"
        for sub in cls.all_subs:
            if not sub.hide:
                msg += f"{sub.id}: {sub.name}({', '.join(sub.regions)})\n"
        msg += "---\n"
        msg += "使用\"/pjsk开启 英文项目名\"开启订阅\n"
        return msg.strip()

    def _check_region(self, region):
        if region not in self.regions:
            raise Exception(f"群聊订阅 {self.name} 不支持服务器 {region}")

    def is_subbed(self, region, group_id):
        self._check_region(region)
        return self.subs[region].is_subbed(group_id)

    def sub(self, region, group_id):
        self._check_region(region)
        return self.subs[region].sub(group_id)

    def unsub(self, region, group_id):
        self._check_region(region)
        return self.subs[region].unsub(group_id)

    def get_all(self, region):
        self._check_region(region)
        return self.subs[region].get_all()

    def clear(self, region):
        self._check_region(region)
        return self.subs[region].clear()


sekai_group_sub = SekaiCmdHandler([
    "/pjsk group sub", "/pjsk 群订阅", "/pjsk 群聊订阅", "/pjsk 开启",
])
sekai_group_sub.check_cdrate(cd).check_wblist(gbl).check_superuser()
@sekai_group_sub.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    msg = ""
    subs = None
    # 提取订阅项目
    if args:
        if not (subs := SekaiGroupSubHelper.find_by_ids(args.split())):
            msg += f"无效的订阅项目: {args}\n"
    if not subs:
        # 无订阅项目则列出所有订阅状态
        msg += SekaiGroupSubHelper.do_get_sub(ctx)
    else:
        # 订阅指定项目
        for sub in subs:
            msg += sub.do_sub(ctx) + "\n"
    return await ctx.asend_reply_msg(msg.strip())


sekai_group_unsub = SekaiCmdHandler([
    "/pjsk group unsub", "/pjsk 取消群订阅", "/pjsk 取消群聊订阅", "/pjsk 关闭",
])
sekai_group_unsub.check_cdrate(cd).check_wblist(gbl).check_superuser()
@sekai_group_unsub.handle()
async def _(ctx: SekaiHandlerContext):
    args = ctx.get_args().strip()
    msg = ""
    subs = None
    # 提取订阅项目
    if args:
        if not (subs := SekaiGroupSubHelper.find_by_ids(args.split())):
            msg += f"无效的订阅项目: {args}\n"
    if not subs:
        # 无订阅项目则列出所有订阅状态
        msg += SekaiGroupSubHelper.do_get_sub(ctx)
    else:
        # 取消订阅指定项目
        for sub in subs:
            msg += sub.do_unsub(ctx) + "\n"
    return await ctx.asend_reply_msg(msg.strip())



# ======================= 用户订阅 ======================= #

class SekaiUserSubHelper:
    all_subs: List['SekaiUserSubHelper'] = []

    def __init__(self, id: str, name: str, regions: List[str], related_group_sub: SekaiGroupSubHelper = None, only_one_group=False, hide=False):
        self.id = id
        self.name = name
        self.regions = regions
        self.related_group_sub = related_group_sub
        self.hide = hide
        self.subs = {
            region: GroupUserSubHelper(
                f"{name}({region_name})_用户",
                file_db,
                logger,
            ) for region, region_name in zip(regions, ALL_SERVER_REGION_NAMES)
        }
        self.only_one_group = only_one_group
        SekaiUserSubHelper.all_subs.append(self)

    @classmethod
    def find_by_ids(cls, ids: List[str]) -> list['SekaiUserSubHelper']:
        return find_by_predicate(cls.all_subs, lambda s: s.id in ids, mode='all')

    def do_sub(self, ctx: SekaiHandlerContext) -> str:
        if ctx.region not in self.subs:
            return f"❌ 订阅 {self.name} 不支持服务器 {ctx.region}"
        has_other_group_sub = False
        if self.only_one_group:
            # 检测是否在其他群聊订阅
            for uid, gid in self.subs[ctx.region].get_all():
                if uid == ctx.user_id and gid != ctx.group_id:
                    has_other_group_sub = True
                    self.subs[ctx.region].unsub(uid, gid)
        self.subs[ctx.region].sub(ctx.user_id, ctx.group_id)
        msg = f"✅ 成功订阅 {self.name}({get_region_name(ctx.region)})\n"
        if has_other_group_sub:
            msg += "⚠️ 已自动取消你在其他群聊的订阅\n"
        # 对应群聊功能未开启
        if self.related_group_sub and ctx.group_id not in self.related_group_sub.get_all(ctx.region):
            msg += f"⚠️ 该订阅对应的群聊功能 {self.related_group_sub.name}({get_region_name(ctx.region)}) 在本群未开启！"
            msg += "如需使用请联系BOT超管"
        return msg.strip()
    
    def do_unsub(self, ctx: SekaiHandlerContext) -> str:
        if ctx.region not in self.subs:
            return f"❌ 订阅 {self.name} 不支持服务器 {ctx.region}"
        self.subs[ctx.region].unsub(ctx.user_id, ctx.group_id)
        return f"✅ 成功取消订阅 {self.name}({get_region_name(ctx.region)})"
    
    @classmethod
    def do_get_sub(cls, ctx: SekaiHandlerContext) -> str:
        msg = "你在当前群聊的订阅:\n"
        has_related_not_on = False
        has_sub = False
        for sub in cls.all_subs:
            sub_regions = []
            for region in sub.regions:
                if sub.is_subbed(region, ctx.user_id, ctx.group_id):
                    # 标记对应群聊功能未开启的订阅
                    if sub.related_group_sub and ctx.group_id not in sub.related_group_sub.get_all(region):
                        has_related_not_on = True
                        region = region + "*"
                    sub_regions.append(region)
            if sub_regions:
                msg += f"{sub.name}({', '.join(sub_regions)})\n"
                has_sub = True
        if has_related_not_on:
            msg += "---\n"
            msg += "带*的订阅对应的群聊功能在本群未开启！"
            msg += "如需使用请联系BOT超管\n"
        if not has_sub:
            msg += "无\n"
        msg += "---\n"
        msg += "所有可订阅项目:\n"
        for sub in cls.all_subs:
            if not sub.hide:
                msg += f"{sub.id}: {sub.name}({', '.join(sub.regions)})\n"
        msg += "---\n"
        msg += "使用\"/pjsk订阅 项目\"订阅，例如发送\"/cnpjsk订阅 live\"订阅国服live提醒"
        return msg.strip()

    def _check_region(self, region):
        if region not in self.regions:
            raise Exception(f"用户订阅 {self.name} 不支持服务器 {region}")

    def is_subbed(self, region, user_id, group_id):
        self._check_region(region)
        return self.subs[region].is_subbed(user_id, group_id)

    def sub(self, region, user_id, group_id):
        self._check_region(region)
        return self.subs[region].sub(user_id, group_id)

    def unsub(self, region, user_id, group_id):
        self._check_region(region)
        return self.subs[region].unsub(user_id, group_id)
    
    def get_all(self, region, group_id) -> List[int]:
        self._check_region(region)
        ret = self.subs[region].get_all()
        return [x[0] for x in ret if x[1] == group_id]
    
    def get_all_gid_uid(self, region) -> List[Tuple[int, int]]:
        self._check_region(region)
        return self.subs[region].get_all()

    def clear(self, region):
        self._check_region(region)
        return self.subs[region].clear()


sekai_user_sub = SekaiCmdHandler([
    "/pjsk sub", "/pjsk 订阅", "/pjsk 用户订阅",
])
sekai_user_sub.check_cdrate(cd).check_wblist(gbl)
@sekai_user_sub.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    msg = ""
    # 提取订阅项目
    subs = None
    if args:
        if not (subs := SekaiUserSubHelper.find_by_ids(args.split())):
            msg += f"无效的订阅项目: {args}\n"
    if not subs:
        # 无订阅项目则列出所有订阅状态
        msg += SekaiUserSubHelper.do_get_sub(ctx)
    else:
        # 订阅指定项目
        for sub in subs:
            msg += sub.do_sub(ctx) + "\n"
    return await ctx.asend_reply_msg(msg.strip())


sekai_user_unsub = SekaiCmdHandler([
    "/pjsk unsub", "/pjsk 取消订阅", "/pjsk 取消用户订阅",
])
sekai_user_unsub.check_cdrate(cd).check_wblist(gbl)
@sekai_user_unsub.handle()
async def _(ctx: HandlerContext):
    args = ctx.get_args().strip()
    msg = ""
    # 提取订阅项目
    subs = None
    if args:
        if not (subs := SekaiUserSubHelper.find_by_ids(args.split())):
            msg += f"无效的订阅项目: {args}\n"
    if not subs:
        # 无订阅项目则列出所有订阅状态
        msg += SekaiUserSubHelper.do_get_sub(ctx)
    else:
        # 取消订阅指定项目
        for sub in subs:
            msg += sub.do_unsub(ctx) + "\n"
    return await ctx.asend_reply_msg(msg.strip())
