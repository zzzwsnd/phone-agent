"""
访客记录数据库操作

提供访客记录的增删查功能，依赖 infra.mysql 的连接池
"""
from infra.mysql import query, execute, get_conn


def save_visitor_record(
    caller_number: str,
    license_plate: str | None = None,
    visiting_company: str | None = None,
    visitor_phone: str | None = None,
    purpose: str | None = None,
    visitor_name: str | None = None,
    call_room_name: str | None = None,
) -> int:
    """保存访客记录，返回插入行 ID

    使用同一连接执行 INSERT 和 LAST_INSERT_ID，确保获取正确 ID。
    """
    sql = """
        INSERT INTO visitor_records
            (caller_number, license_plate, visiting_company, visitor_phone, purpose, visitor_name, call_room_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    params = (caller_number, license_plate, visiting_company, visitor_phone, purpose, visitor_name, call_room_name)
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        cursor.execute("SELECT LAST_INSERT_ID() AS id")
        result = cursor.fetchone()
        return result[0]


def lookup_visitor_by_phone(phone: str) -> list[dict]:
    """按主叫号码查询历史访客记录，最多返回 5 条"""
    sql = """
        SELECT id, caller_number, license_plate, visiting_company,
               visitor_phone, purpose, visitor_name, call_room_name,
               created_at, updated_at
        FROM visitor_records
        WHERE caller_number = %s
        ORDER BY created_at DESC
        LIMIT 5
    """
    return query(sql, (phone,))


def list_visitors(limit: int = 50, offset: int = 0) -> list[dict]:
    """分页查询访客记录列表"""
    sql = """
        SELECT id, caller_number, license_plate, visiting_company,
               visitor_phone, purpose, visitor_name, call_room_name,
               created_at, updated_at
        FROM visitor_records
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """
    return query(sql, (limit, offset))


def format_return_visit_summary(records: list[dict]) -> str:
    """将回访记录格式化为简短中文摘要，用于注入 prompt"""
    if not records:
        return ""

    parts = []
    for r in records:
        segments = [f"该号码曾于{r['created_at'].strftime('%Y-%m-%d')}"]
        if r.get("visiting_company"):
            segments.append(f"来访{r['visiting_company']}")
        if r.get("purpose"):
            segments.append(r["purpose"])
        if r.get("visitor_name"):
            segments.append(f"姓名{r['visitor_name']}")
        parts.append("".join(segments))

    return "；".join(parts)
