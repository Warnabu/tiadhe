with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\postgresql_db.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove the leftover duplicate code
content = content.replace(
    '''        except psycopg2.OperationalError as e:
            if "SSL" in str(e) or "connection" in str(e).lower():
                print(f"PostgreSQL SSL/connection error (attempt {attempt + 1}/3): {e}")
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
            else:
                raise
        except Exception:
            raise
            cur.execute(query, params)
            if fetch:
                rows = cur.fetchall()
                return [dict(r) for r in rows]
            conn.commit()
            return None


def execute_one(query: str, params: Tuple = ()) -> Optional[Dict[str, Any]]:''',
    '''        except psycopg2.OperationalError as e:
            if "SSL" in str(e) or "connection" in str(e).lower():
                print(f"PostgreSQL SSL/connection error (attempt {attempt + 1}/3): {e}")
                if attempt == 2:
                    raise
                time.sleep(0.5 * (attempt + 1))
            else:
                raise
        except Exception:
            raise


def execute_one(query: str, params: Tuple = ()) -> Optional[Dict[str, Any]]:'''
)

with open(r'C:\Users\moned\Documents\opencodeprueba\tihade\files\postgresql_db.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')