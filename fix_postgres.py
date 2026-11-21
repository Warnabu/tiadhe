with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\postgresql_db.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix cleanup_old_nonces - nonces.ts is BIGINT
content = content.replace(
    '''def cleanup_old_nonces(max_age_seconds: int = 600, agora: int = None) -> None:
    """Remove expired nonces."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Atlantic/Canary")
    if agora is None:
        agora = int(time.time())
    corte = datetime.fromtimestamp(ahora - max_age_seconds, TZ)
    execute("DELETE FROM nonces WHERE ts < %s", (corte,))''',
    '''def cleanup_old_nonces(max_age_seconds: int = 600, agora: int = None) -> None:
    """Remove expired nonces."""
    if agora is None:
        agora = int(time.time())
    corte = agora - max_age_seconds
    execute("DELETE FROM nonces WHERE ts < %s", (corte,))'''
)

# Fix cleanup_old_ubicaciones - ubicaciones_activas.ts is BIGINT
content = content.replace(
    '''def cleanup_old_ubicaciones(max_age_seconds: int = 300, agora: int = None) -> None:
    """Remove stale active locations."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Atlantic/Canary")
    if agora is None:
        agora = int(time.time())
    corte = datetime.fromtimestamp(ahora - max_age_seconds, TZ)
    execute("DELETE FROM ubicaciones_activas WHERE ts < %s", (corte,))''',
    '''def cleanup_old_ubicaciones(max_age_seconds: int = 300, agora: int = None) -> None:
    """Remove stale active locations."""
    if agora is None:
        agora = int(time.time())
    corte = agora - max_age_seconds
    execute("DELETE FROM ubicaciones_activas WHERE ts < %s", (corte,))'''
)

with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\postgresql_db.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')