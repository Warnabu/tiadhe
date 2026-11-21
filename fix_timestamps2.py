with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\comunidad_pg.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix all _ts_to_dt calls for BIGINT columns - replace with int()
# Line 85: corte for registros.ts (BIGINT)
content = content.replace(
    'corte = _ts_to_dt(ahora - 86400)',
    'corte = int(ahora - 86400)'
)

# Line 89: agora_dt for dispositivos.creado, ult (TIMESTAMP per error) - KEEP as datetime
# Actually dispositivos.creado is timestamp with time zone per error, so keep _ts_to_dt
# But let's check: the error said column is timestamp but expression is numeric
# So we NEED datetime for dispositivos table

# Line 113: nonces.ts (BIGINT)
content = content.replace(
    'pg.execute("INSERT INTO nonces VALUES(%s,%s)", (nonce_key, _ts_to_dt(ahora)))',
    'pg.execute("INSERT INTO nonces VALUES(%s,%s)", (nonce_key, int(ahora)))'
)

# Line 151: agora_dt for dispositivos.strikes_ts, bloqueado - these might be timestamp too
# Keep as datetime for dispositivos table

# Line 153: _ts_to_dt(ahora + 86400) and _ts_to_dt(0) for dispositivos.bloqueado
# Keep as datetime for dispositivos table

# Line 158: _ts_to_dt(ahora - 86400) for subidas.ts (BIGINT)
content = content.replace(
    'cnt = pg.execute_one("SELECT COUNT(*) as c FROM subidas WHERE dev=%s AND ts>%s", (dev, _ts_to_dt(ahora - 86400)))["c"]',
    'cnt = pg.execute_one("SELECT COUNT(*) as c FROM subidas WHERE dev=%s AND ts>%s", (dev, int(ahora - 86400)))["c"]'
)

with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\comunidad_pg.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')