with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\servidor.py', 'r', encoding='utf-8') as f:
    content = f.read()

old = '''def tablero(clave, objetivo, ahora, por_destino=6):
    k = (clave, ahora.strftime("%Y%m%d%H%M"))  # las respuestas cambian como mucho cada minuto
    if k in _cache:
        return _cache[k]
    if len(_cache) > 800:
        _cache.clear()
    grupos = {}
    for r in g.proximas(objetivo, ahora, HORARIOS, BASE, com=COM, gracia=1):
        lista = grupos.setdefault(r["destino"], [])
        if sum(1 for x in lista if x["linea"] == r["linea"]) < 3 and len(lista) < por_destino:
            dif = (r["llegada"] - ahora).total_seconds() / 60
            lista.append(dict(linea=r["linea"], texto=g.texto_espera(r, ahora), hora=f"{r['llegada']:%H:%M}", minutos=round(dif),
                           prec=r["prec"], cal=r["cal"], nota=r["nota"], viaje=r["viaje"], n=r["n"],
                           subir=-12 <= dif <= min(20, 12 + 0.25 * r["off"])))  # solo se puede avisar cuando est� pasando
    orden = sorted(grupos.items(), key=lambda kv: kv[1][0]["minutos"])
    _cache[k] = dict(ahora=f"{ahora:%H:%M}", dia=f"{DIAS[ahora.weekday()]} {ahora:%d/%m}",
                 grupos=[dict(destino=d, salidas=s) for d, s in orden])
    return _cache[k]'''

new = '''def tablero(clave, objetivo, agora, por_destino=6):
    k = (clave, agora.strftime("%Y%m%d%H%M"))  # las respuestas cambian como mucho cada minuto
    if k in _cache:
        return _cache[k]
    if len(_cache) > 800:
        _cache.clear()
    grupos = {}
    try:
        for r in g.proximas(objetivo, agora, HORARIOS, BASE, com=COM, gracia=1):
            lista = grupos.setdefault(r["destino"], [])
            if sum(1 for x in lista if x["linea"] == r["linea"]) < 3 and len(lista) < por_destino:
                dif = (r["llegada"] - agora).total_seconds() / 60
                lista.append(dict(linea=r["linea"], texto=g.texto_espera(r, agora), hora=f"{r['llegada']:%H:%M}", minutos=round(dif),
                               prec=r["prec"], cal=r["cal"], nota=r["nota"], viaje=r["viaje"], n=r["n"],
                               subir=-12 <= dif <= min(20, 12 + 0.25 * r["off"])))  # solo se puede avisar cuando est� pasando
    except Exception as e:
        import traceback
        print(f"ERROR in tablero: {e}")
        traceback.print_exc()
        raise
    orden = sorted(grupos.items(), key=lambda kv: kv[1][0]["minutos"])
    _cache[k] = dict(ahora=f"{agora:%H:%M}", dia=f"{DIAS[agora.weekday()]} {agora:%d/%m}",
                 grupos=[dict(destino=d, salidas=s) for d, s in orden])
    return _cache[k]'''

content = content.replace(old, new)

with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\servidor.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')