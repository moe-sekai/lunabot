from .utils import *
import aiosqlite

RANKING_NAME_LEN_LIMIT = 32

SEKAI_DATA_DIR = "data/sekai"
DB_PATH = SEKAI_DATA_DIR + "/db/sk_{region}/{event_id}_ranking.db"

_conns: dict[str, aiosqlite.Connection] = {}
_created_table_keys: dict[str, bool] = {}


async def get_conn(region, event_id, create) -> Optional[aiosqlite.Connection]:
    path = DB_PATH.format(region=region, event_id=event_id)
    create_parent_folder(path)
    if not create and not os.path.exists(path):
        return None

    global _conns
    if _conns.get(path) is None:
        _conns[path] = await aiosqlite.connect(path)
        await _conns[path].execute("PRAGMA journal_mode=WAL;") 
        info(f"连接sqlite数据库 {path} 成功")

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


async def close_conn(region: str):
    for key in list(_conns.keys()):
        if key.startswith(DB_PATH.format(region=region, event_id="")):
            await _conns[key].close()
            del _conns[key]
            info(f"关闭sqlite数据库连接 {key} 成功")


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


async def insert_rankings(region: str, event_id: int, rankings: list[Ranking]):
    conn = await get_conn(region, event_id, create=True)

    for ranking in rankings:
        ranking.name = ranking.name[:RANKING_NAME_LEN_LIMIT]
        await conn.execute("""
            INSERT INTO ranking (uid, name, score, rank, ts) VALUES (?, ?, ?, ?, ?)
        """, (ranking.uid, ranking.name, ranking.score, ranking.rank, ranking.time.timestamp()))

    await conn.commit()

