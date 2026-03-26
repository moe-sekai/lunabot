from ..utils import *
import aiosqlite
from datetime import datetime


config = Config('water')
logger = get_logger("Water")

DB_PATH = "data/water/hash.sqlite"
HASH_TABLE_NAME = "hash_{}"

_conn: aiosqlite.Connection = None         # 连接
_created_table_group_ids = set()             # 是否创建过表


# 获得连接 
async def get_conn(group_id: int | list[int]):
    global _conn, _created_table_group_ids
    if _conn is None:
        create_parent_folder(DB_PATH)
        _conn = await aiosqlite.connect(DB_PATH)
        logger.info(f"连接sqlite数据库 {DB_PATH} 成功")

    table_created = False
    if isinstance(group_id, int):
        group_id = [group_id]
    for gid in group_id:
        if gid not in _created_table_group_ids:
            # 创建表 (ID, 类型，hash, msg_id, user_id, nickname, time, unique_id)
            await _conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {HASH_TABLE_NAME.format(gid)} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT,
                    phash TEXT,
                    msg_id INTEGER,
                    user_id INTEGER,
                    nickname TEXT,
                    time INTEGER,
                    unique_id TEXT
                )
            """)       
            _created_table_group_ids.add(gid)
            table_created = True
    if table_created:
        await _conn.commit()

    return _conn

# 插入一条hash数据
async def insert_hash(group_id: int, type: str, hash: str, msg_id: int, user_id: int, nickname: str, time: int, unique_id: str):
    if isinstance(time, datetime):
        time = time.timestamp()
    conn = await get_conn(group_id)
    await conn.execute(f"""
        INSERT INTO {HASH_TABLE_NAME.format(group_id)} (type, phash, msg_id, user_id, nickname, time, unique_id)
        VALUES (?,?,?,?,?,?,?)
        """, (type, hash, msg_id, user_id, nickname, time, unique_id))
    await conn.commit()
    # logger.debug(f"插入hash数据 type={type} hash={hash} msg_id={msg_id} user_id={user_id} nickname={nickname} time={time} unique_id={unique_id}")

# 插入多条hash数据
async def insert_hashes(hashes: list):
    group_id_hashes = {}
    for h in hashes:
        group_id = h['group_id']
        group_id_hashes.setdefault(group_id, []).append(h)
    
    conn = await get_conn(list(group_id_hashes.keys()))
    for group_id, hashes in group_id_hashes.items():
        insert_query = f'''
            INSERT INTO {HASH_TABLE_NAME.format(group_id)} (type, phash, msg_id, user_id, nickname, time, unique_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        '''
        values = []
        for hash in hashes:
            values.append((
                hash['type'], 
                hash['hash'], 
                hash['msg_id'], 
                hash['user_id'], 
                hash['nickname'], 
                hash['time'].timestamp() if isinstance(hash['time'], datetime) else hash['time'], 
                hash['unique_id']
            ))
        await conn.executemany(insert_query, values)
    await conn.commit()

    logger.debug(f"插入来自 {len(hashes)} 个群的 {len(hashes)} 条hash数据")
                 
# hash row 转换为 dict
def hash_row_to_dict(row):
    if isinstance(row[6], str):
        time = datetime.strptime(row[6], "%Y-%m-%d %H:%M:%S")
    else:
        time = datetime.fromtimestamp(row[6])
    return {
        "id": row[0],
        "type": row[1],
        "hash": row[2],
        "msg_id": row[3],
        "user_id": row[4],
        "nickname": row[5],
        "time": time,
        "unique_id": row[7],
    }

# 根据类型和hash查询记录
async def query_by_hash(group_id: int, type: str, hash: str) -> list:
    conn = await get_conn(group_id)
    cursor = await conn.execute(f"""
        SELECT * FROM {HASH_TABLE_NAME.format(group_id)}
        WHERE type = ? AND phash = ?
        """, (type, hash))
    rows = await cursor.fetchall()
    await cursor.close()
    return [hash_row_to_dict(row) for row in rows]

# 根据类型和msg_id查询记录
async def query_by_msg_id(group_id: int, type: str, msg_id: int) -> list:
    conn = await get_conn(group_id)
    cursor = await conn.execute(f"""
        SELECT * FROM {HASH_TABLE_NAME.format(group_id)}
        WHERE type = ? AND msg_id = ?
        """, (type, msg_id))
    rows = await cursor.fetchall()
    await cursor.close()
    return [hash_row_to_dict(row) for row in rows]

# 根据类型和unique_id查询记录
async def query_by_unique_id(group_id: int, type: str, unique_id: str) -> list:
    conn = await get_conn(group_id)
    cursor = await conn.execute(f"""
        SELECT * FROM {HASH_TABLE_NAME.format(group_id)}
        WHERE type = ? AND unique_id = ?
        """, (type, unique_id))
    rows = await cursor.fetchall()
    await cursor.close()
    return [hash_row_to_dict(row) for row in rows]

