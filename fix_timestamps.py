with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\comunidad_pg.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix limpiar function - replace _ts_to_dt with int()
content = content.replace(
    'corte_antiguo = _ts_to_dt(ahora - 90 * 86400)',
    'corte_antiguo = int(ahora - 90 * 86400)'
)
content = content.replace(
    'corte_reciente = _ts_to_dt(ahora - 7200)',
    'corte_reciente = int(ahora - 7200)'
)

with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\comunidad_pg.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')