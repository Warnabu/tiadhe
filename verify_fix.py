import json
d = json.load(open('datos/paradas_reales.json'))
for k, v in d.items():
    if v.get('trazado'):
        p = v['paradas']
        print(f'{k}: {len(p)} paradas, k={p[0]["k"]:.3f}->{p[-1]["k"]:.3f}, valida={v.get("valida")}, ajustado={v.get("ajustado")}')