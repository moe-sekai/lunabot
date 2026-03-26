from ...utils import *
from ..common import *
import aiosqlite

RANKING_NAME_LEN_LIMIT = 32

DB_PATH = SEKAI_DATA_DIR + "/db/sk_{region}/{event_id}_ranking.db"

_conns: Dict[str, aiosqlite.Connection] = {}
_created_table_keys: Dict[str, bool] = {}


async def get_conn(region, event_id, create) -> Optional[aiosqlite.Connection]:
    path = DB_PATH.format(region=region, event_id=event_id)
    create_parent_folder(path)
    if not create and not os.path.exists(path):
        return None

    global _conns
    if _conns.get(path) is None:
        _conns[path] = await aiosqlite.connect(path)
        await _conns[path].execute("PRAGMA journal_mode=WAL;") 
        logger.info(f"连接sqlite数据库 {path} 成功")

    conn = _conns[path]
    
    cache_key = f"{region}_{event_id}"
    
    if not _created_table_keys.get(cache_key):
        # 建表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ranking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT,
                name TEXT,
                score INTEGER,
                rank INTEGER,
                ts INTEGER
            )
        """)
        # 创建索引
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ranking_rank_ts 
            ON ranking (rank, ts)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ranking_uid 
            ON ranking (uid)
        """)
        await conn.commit()
        _created_table_keys[cache_key] = True

    return conn


async def archive_database(region: str, event_id: int):
    """
    归档数据库：合并WAL数据，删除临时文件，并优化文件大小。
    """
    path = DB_PATH.format(region=region, event_id=event_id)
    if not os.path.exists(path):
        return
    
    global _conns, _created_table_keys
    if path in _conns:
        await _conns[path].close()
        del _conns[path]
        cache_key = f"{region}_{event_id}"
        if cache_key in _created_table_keys:
            del _created_table_keys[cache_key]

    async with aiosqlite.connect(path) as conn:
        logger.info(f"尝试归档数据库 {path} ...")
        await conn.execute("VACUUM;")
        cursor = await conn.execute("PRAGMA journal_mode = DELETE;")
        mode = await cursor.fetchone()
        await cursor.close()
        if mode[0] != 'delete':
            logger.warning(f"切换模式失败，当前模式: {mode[0]}。可能是仍有其他程序连接着数据库。")
        await conn.commit()

    wal_path = path + "-wal"
    shm_path = path + "-shm"
    if os.path.exists(wal_path) or os.path.exists(shm_path):
        logger.warning("警告：WAL文件仍然存在，可能有其他进程（如读取端）占用了数据库！")
    else:
        logger.info(f"数据库 {path} 归档完成。")


@dataclass
class Ranking:
    uid: str
    name: str
    score: int
    rank: int
    time: datetime
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row):
        return cls(
            id=row[0],
            uid=row[1],
            name=row[2],
            score=row[3],
            rank=row[4],
            time=datetime.fromtimestamp(row[5])
        )
    
    @classmethod
    def from_sk(cls, data: dict, time: datetime = None):
        return cls(
            uid=data["userId"],
            name=data["name"],
            score=data["score"],
            rank=data["rank"],
            time=time or datetime.now(),
        )
    

def query_update_time(
    region: str, 
    event_id: int,
) -> Optional[datetime]:
    """检查表更新时间"""
    path = DB_PATH.format(region=region, event_id=event_id)
    if not os.path.exists(path):
        return None
    ret = datetime.fromtimestamp(os.path.getmtime(path))
    if os.path.exists(path + "-wal"):
        ret = max(ret, datetime.fromtimestamp(os.path.getmtime(path + "-wal")))
    return ret


async def query_ranking(
    region: str, 
    event_id: int, 
    uid: str = None,
    name: str = None,
    rank: int = None,
    start_time: datetime = None,
    end_time: datetime = None,
    limit: int = None,
    order_by: str = None,
) -> List[Ranking]:
    conn = await get_conn(region, event_id, create=False)
    if not conn:
        return []

    sql = "SELECT * FROM ranking WHERE 1=1"
    args = []

    if uid is not None:
        sql += " AND uid = ?"
        args.append(uid)

    if name is not None:
        name = name[:RANKING_NAME_LEN_LIMIT]
        sql += " AND name = ?"
        args.append(name)

    if rank is not None:
        sql += " AND rank = ?"
        args.append(rank)

    if start_time is not None:
        sql += " AND ts >= ?"
        args.append(start_time.timestamp())

    if end_time is not None:
        sql += " AND ts <= ?"
        args.append(end_time.timestamp())

    if order_by is not None:
        sql += f" ORDER BY {order_by}"

    if limit is not None:
        sql += f" LIMIT {limit}"

    cursor = await conn.execute(sql, args)
    rows = await cursor.fetchall()
    await cursor.close()

    return [Ranking.from_row(row) for row in rows]


async def query_latest_ranking(region: str, event_id: int, ranks: List[int] = None) -> List[Ranking]:
    conn = await get_conn(region, event_id, create=False)
    if not conn:
        return []
    if ranks:
        placeholders = ", ".join("?" for _ in ranks)
        sql = f"""
            SELECT * FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (PARTITION BY rank ORDER BY ts DESC) as rn
                FROM ranking
                WHERE rank IN ({placeholders})
            )
            WHERE rn = 1
            ORDER BY rank
        """
        cursor = await conn.execute(sql, ranks)
        rows = await cursor.fetchall()
        await cursor.close()
        return [Ranking.from_row(row) for row in rows]
    else:
        # 对于表中的每一个rank，找到最新的一条记录
        cursor = await conn.execute("""
            SELECT * FROM ranking WHERE id IN (
                SELECT MAX(id) FROM ranking GROUP BY rank
            ) ORDER BY rank
        """)
        rows = await cursor.fetchall()
        await cursor.close()
        return [Ranking.from_row(row) for row in rows]


async def query_first_ranking_after(
    region: str, 
    event_id: int, 
    after_time: datetime,
    ranks: List[int] = None,
) -> List[Ranking]:
    conn = await get_conn(region, event_id, create=False)
    if not conn:
        return []
    if ranks:
        # 对于ranks中的每一个rank，找到第一条记录
        placeholders = ", ".join("?" for _ in ranks)
        sql = f"""
            SELECT * FROM (
                SELECT
                    *,
                    ROW_NUMBER() OVER (PARTITION BY rank ORDER BY ts ASC) as rn
                FROM ranking
                WHERE rank IN ({placeholders}) AND ts > ?
            )
            WHERE rn = 1
            ORDER BY rank
        """
        params = ranks + [after_time.timestamp()]
        cursor = await conn.execute(sql, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [Ranking.from_row(row) for row in rows]
    else:
        # 对于表中的每一个rank，找到第一条记录
        cursor = await conn.execute("""
            SELECT * FROM ranking WHERE id IN (
                SELECT MIN(id) FROM ranking WHERE ts > ? GROUP BY rank
            ) ORDER BY rank
        """, (after_time.timestamp(),))
        rows = await cursor.fetchall()
        await cursor.close()
        return [Ranking.from_row(row) for row in rows]
    

async def query_ranks_with_interval(region: str, event_id: int, ranks: list[int], sample_interval_seconds: int):
    """
    以一定间隔采样ranks的记录
    """
    if not ranks:
        return {}
    conn = await get_conn(region, event_id, create=False)
    if not conn:
        return {}
    
    placeholders = ','.join('?' for _ in ranks)
    sql = f"""
        SELECT id, uid, name, score, rank, MIN(ts) as ts
        FROM ranking
        WHERE rank IN ({placeholders})
        GROUP BY rank, (ts / ?)
        ORDER BY ts ASC
    """
    
    args = list(ranks) + [sample_interval_seconds]
    
    async with conn.execute(sql, args) as cursor:
        rows = await cursor.fetchall()
        return [Ranking.from_row(row) for row in rows]