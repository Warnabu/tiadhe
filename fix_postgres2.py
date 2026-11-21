with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\postgresql_db.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Fix cleanup_old_nonces (lines 261-269)
# Fix cleanup_old_ubicaciones (lines 272-280)

new_lines = []
i = 0
while i < len(lines):
    line = lines[i]
    if 'def cleanup_old_nonces' in line:
        # Replace the function
        new_lines.append('def cleanup_old_nonces(max_age_seconds: int = 600, agora: int = None) -> None:\n')
        new_lines.append('    """Remove expired nonces."""\n')
        new_lines.append('    if agora is None:\n')
        new_lines.append('        agora = int(time.time())\n')
        new_lines.append('    corte = agora - max_age_seconds\n')
        new_lines.append('    execute("DELETE FROM nonces WHERE ts < %s", (corte,))\n')
        new_lines.append('\n')
        # Skip old function lines
        while i < len(lines) and 'def cleanup_old_ubicaciones' not in lines[i]:
            i += 1
        continue
    elif 'def cleanup_old_ubicaciones' in line:
        new_lines.append('def cleanup_old_ubicaciones(max_age_seconds: int = 300, agora: int = None) -> None:\n')
        new_lines.append('    """Remove stale active locations."""\n')
        new_lines.append('    if agora is None:\n')
        new_lines.append('        agora = int(time.time())\n')
        new_lines.append('    corte = agora - max_age_seconds\n')
        new_lines.append('    execute("DELETE FROM ubicaciones_activas WHERE ts < %s", (corte,))\n')
        # Skip old function lines
        while i < len(lines):
            i += 1
        continue
    new_lines.append(line)
    i += 1

with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\postgresql_db.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Done')