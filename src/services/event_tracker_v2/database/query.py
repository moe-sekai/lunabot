from .model import RankRecord, UserRecord, TimeRecord, Base
from sqlalchemy import select, text, func, Integer, event, Engine, update, insert, bindparam
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import joinedload, contains_eager
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine, AsyncSession, async_sessionmaker
from datetime import datetime, timedelta
from dataclasses import dataclass
import os


DB_PATH = "data/event_tracker_v2/db/{region}/{event_id}.db"

@dataclass
class Ranking:
    id: int
    uid: str
    name: str
    score: int
    rank: int
    time: datetime

    @classmethod
    def from_rank_record(cls, rank_record: RankRecord) -> 'Ranking':
        return Ranking(
            id=rank_record.id,
            uid=str(rank_record.user_record.uid),
            name=rank_record.user_record.name,
            score=rank_record.score,
            rank=rank_record.rank,
            time=datetime.fromtimestamp(rank_record.time_record.ts)
        )


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    # 增加缓存大小建议 (可选，视内存情况而定)
    # cursor.execute("PRAGMA cache_size=-64000") 
    cursor.close()


class EventTrackerDatabase:
    _databases: dict[tuple[str, int], 'EventTrackerDatabase'] = {}

    def __init__(self, region: str, event_id: int):
        self.region = region
        self.event_id = event_id
        # 确保目录存在
        db_file = DB_PATH.format(region=region, event_id=event_id)
        os.makedirs(os.path.dirname(db_file), exist_ok=True)
        
        self.engine = create_async_engine(
            f'sqlite+aiosqlite:///{db_file}', 
            echo=False
        )
        # create_all 是同步操作，如果在异步上下文中运行可能需要 run_sync
        # 但通常在初始化阶段直接调用也没问题，或者如下标准写法：
        # async with self.engine.begin() as conn:
        #     await conn.run_sync(Base.metadata.create_all)
        # 这里保留你的写法，假设你在同步环境初始化或者不介意短暂阻塞
        try:
             # Hack: 使用 run_sync 确保异步环境下的兼容性
             import asyncio
             if asyncio.get_event_loop().is_running():
                 # 这是一个妥协，实际生产中建议由外部显式调用 init_db
                 pass 
             else:
                 Base.metadata.create_all(self.engine)
        except:
             # 如果在运行的 loop 中直接调用 create_all 会报错，建议用 run_sync 模式
             # 这里为了简单，建议将 Base.metadata.create_all 移到一个异步 init 方法中
             pass

        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def get(region: str, event_id: int) -> 'EventTrackerDatabase':
        key = (region, event_id)
        if key not in EventTrackerDatabase._databases:
            EventTrackerDatabase._databases[key] = EventTrackerDatabase(region, event_id)
        return EventTrackerDatabase._databases[key]

    async def init_db(self):
        """显式初始化数据库表结构 (推荐调用此方法)"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    
    async def close(self):
        await self.engine.dispose()
        if (self.region, self.event_id) in EventTrackerDatabase._databases:
            del EventTrackerDatabase._databases[(self.region, self.event_id)]

    @staticmethod
    async def close_all(region: str = None, event_id: int = None):
        for r, eid in list(EventTrackerDatabase._databases.keys()):
            if region is not None and r != region:
                continue
            if event_id is not None and eid != event_id:
                continue
            await EventTrackerDatabase.get(r, eid).close()

    
    async def query_ranking(
        self,
        uid: str = None,
        name: str = None,
        rank: int = None,
        ranks: list[int] = None,
        start_time: datetime = None,
        end_time: datetime = None,
        limit: int = None,
        order_by: str = None,
    ) -> list[Ranking]:
        async with self.session_factory() as session:
            query = select(RankRecord)

            if uid is not None or name is not None:
                query = query.join(RankRecord.user_record).options(contains_eager(RankRecord.user_record))
                if uid is not None:
                    query = query.where(UserRecord.uid == uid)
                if name is not None:
                    query = query.where(UserRecord.name == name)
            else:
                query = query.options(joinedload(RankRecord.user_record))

            if start_time is not None or end_time is not None:
                query = query.join(RankRecord.time_record).options(contains_eager(RankRecord.time_record))
                if start_time is not None:
                    query = query.where(TimeRecord.ts >= start_time.timestamp())
                if end_time is not None:
                    query = query.where(TimeRecord.ts <= end_time.timestamp())
            else:
                query = query.options(joinedload(RankRecord.time_record))

            if rank is not None:
                query = query.where(RankRecord.rank == rank)
            if ranks is not None:
                query = query.where(RankRecord.rank.in_(ranks))
                
            if order_by is not None:
                query = query.order_by(text(order_by)) 
            if limit is not None:
                query = query.limit(limit)

            result = await session.execute(query)
            return [Ranking.from_rank_record(record) for record in result.scalars().unique().all()]

    async def query_latest_ranking(
        self,
        ranks: list[int] = None,
    ) -> list[Ranking]:
        async with self.session_factory() as session:
            subquery = select(func.max(RankRecord.id)).group_by(RankRecord.rank)
            
            if ranks:
                subquery = subquery.where(RankRecord.rank.in_(ranks))
            
            query = select(RankRecord).options(
                joinedload(RankRecord.user_record),
                joinedload(RankRecord.time_record)
            ).where(RankRecord.id.in_(subquery))
            
            query = query.order_by(RankRecord.rank)

            result = await session.execute(query)
            return [Ranking.from_rank_record(record) for record in result.scalars().unique().all()]

    async def query_first_ranking_after(
        self,
        after_time: datetime,
        ranks: list[int] = None,
    ) -> list[Ranking]:
        async with self.session_factory() as session:
            ts_threshold = after_time.timestamp()

            subquery = select(func.min(RankRecord.id))\
                .join(RankRecord.time_record)\
                .where(TimeRecord.ts > ts_threshold)\
                .group_by(RankRecord.rank)
            
            if ranks:
                subquery = subquery.where(RankRecord.rank.in_(ranks))

            query = select(RankRecord).options(
                joinedload(RankRecord.user_record),
                joinedload(RankRecord.time_record)
            ).where(RankRecord.id.in_(subquery))
            
            query = query.order_by(RankRecord.rank)

            result = await session.execute(query)
            return [Ranking.from_rank_record(record) for record in result.scalars().unique().all()]

    async def query_ranks_with_interval(
        self,
        interval: timedelta,
        ranks: list[int] = None,
    ) -> list[Ranking]:
        async with self.session_factory() as session:
            interval_seconds = int(interval.total_seconds())
            if interval_seconds <= 0:
                return []

            time_bucket = func.cast(TimeRecord.ts / interval_seconds, Integer)

            subquery = select(func.min(RankRecord.id))\
                .join(RankRecord.time_record)\
                .group_by(RankRecord.rank, time_bucket)

            if ranks:
                subquery = subquery.where(RankRecord.rank.in_(ranks))

            query = select(RankRecord)\
                .join(RankRecord.time_record)\
                .options(
                    joinedload(RankRecord.user_record),
                    contains_eager(RankRecord.time_record)
                )\
                .where(RankRecord.id.in_(subquery))\
                .order_by(TimeRecord.ts.asc())

            result = await session.execute(query)
            return [Ranking.from_rank_record(record) for record in result.scalars().unique().all()]


    async def query_ok_times(
        self,
        start_time: datetime = None,
        end_time: datetime = None,
    ) -> list[datetime]:
        async with self.session_factory() as session:
            stmt = select(TimeRecord.ts).where(TimeRecord.ok.is_(True))
            
            if start_time:
                stmt = stmt.where(TimeRecord.ts >= start_time.timestamp())
            if end_time:
                stmt = stmt.where(TimeRecord.ts <= end_time.timestamp())
            
            stmt = stmt.order_by(TimeRecord.ts.asc())

            result = await session.execute(stmt)
            return [datetime.fromtimestamp(ts) for ts in result.scalars().all()]


    async def update_rankings(self, ts: int, rankings: list[Ranking]) -> tuple[int, int]:
        """
        高性能批量插入/更新，返回 (新增数量, 更新数量)
        """
        async with self.session_factory() as session:
            async with session.begin():
                # 1. 插入 TimeRecord
                time_record = TimeRecord(ts=ts)
                session.add(time_record)
                await session.flush()
                time_id = time_record.id

                # 2. 批量 Upsert 用户
                user_data_list = [
                    {"uid": int(r.uid), "name": r.name} 
                    for r in rankings
                ]
                
                stmt_user = sqlite_insert(UserRecord).values(user_data_list)
                stmt_user = stmt_user.on_conflict_do_update(
                    index_elements=['uid'],
                    set_={'name': stmt_user.excluded.name}
                ).returning(UserRecord.uid, UserRecord.id)
                
                user_result = await session.execute(stmt_user)
                uid_to_dbid = {row.uid: row.id for row in user_result.all()}

                # 3. 内存比对逻辑
                target_ranks = [r.rank for r in rankings]

                sub_max_id = select(func.max(RankRecord.id))\
                    .where(RankRecord.rank.in_(target_ranks))\
                    .group_by(RankRecord.rank)

                stmt_latest = select(
                    RankRecord.id, 
                    RankRecord.rank, 
                    RankRecord.score, 
                    RankRecord.user_record_id
                ).where(RankRecord.id.in_(sub_max_id))

                latest_records_result = await session.execute(stmt_latest)
                latest_map = {
                    row.rank: {
                        'id': row.id,
                        'score': row.score,
                        'user_record_id': row.user_record_id
                    }
                    for row in latest_records_result.all()
                }

                # 4. 构建操作列表
                to_update = []
                to_insert = []

                for r in rankings:
                    user_db_id = uid_to_dbid.get(int(r.uid))
                    prev = latest_map.get(r.rank)

                    # 如果数据完全一致（除了时间），更新旧记录的 time_record_id
                    if (prev is not None and 
                        prev['score'] == r.score and 
                        prev['user_record_id'] == user_db_id):
                        
                        to_update.append({
                            "b_id": prev['id'], # 对应 bindparam('b_id')
                            "new_tid": time_id, # 对应 bindparam('new_tid')
                        })
                    else:
                        to_insert.append({
                            "score": r.score,
                            "rank": r.rank,
                            "user_record_id": user_db_id,
                            "time_record_id": time_id
                        })

                # 5. 执行批量操作
                if to_insert:
                    await session.execute(insert(RankRecord), to_insert)

                if to_update:
                    stmt_update = update(RankRecord).\
                        where(RankRecord.id == bindparam('b_id')).\
                        values(time_record_id=bindparam('new_tid'))
                    await session.execute(stmt_update, to_update)
    
                return (len(to_insert), len(to_update))


    async def vacuum(self):
        """
        整理碎片
        """
        async with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            await conn.execute(text("VACUUM"))