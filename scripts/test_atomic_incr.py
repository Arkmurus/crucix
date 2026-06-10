"""Test the atomic SQL increment for R-F1493."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'aria_service'))
os.environ['ARIA_DATA_DIR'] = os.path.join(os.path.dirname(__file__), '..', 'data')
import asyncio

async def test():
    from intel import state_store as ss
    
    key = 'rf1493_test_sql'
    await ss.delete(key)
    
    await ss.connect()
    
    conn = ss._conn
    print(f'Connection: {conn}')
    
    # First insert with value '1'
    await conn.execute(
        "INSERT INTO state(key, value, kind, expires_at) "
        "VALUES(?, '1', 'string', NULL) "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + ? AS TEXT)",
        (key, 1),
    )
    await conn.commit()
    
    cur = await conn.execute('SELECT value FROM state WHERE key = ?', (key,))
    row = await cur.fetchone()
    print(f'After first insert: value={row[0] if row else None}')
    
    # Second increment
    await conn.execute(
        "INSERT INTO state(key, value, kind, expires_at) "
        "VALUES(?, '1', 'string', NULL) "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + ? AS TEXT)",
        (key, 1),
    )
    await conn.commit()
    
    cur = await conn.execute('SELECT value FROM state WHERE key = ?', (key,))
    row = await cur.fetchone()
    print(f'After second increment: value={row[0] if row else None}')
    
    await ss.delete(key)

asyncio.run(test())
