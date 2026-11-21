with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\comunidad_pg.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix indentation on line with ids_ok
content = content.replace(
    'base = acuerdo[0]\nids_ok = {f["id"] for f in acuerdo}\n        agora_int = int(ahora)',
    'base = acuerdo[0]\n        ids_ok = {f["id"] for f in acordo}\n        agora_int = int(ahora)'
)

with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\comunidad_pg.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')