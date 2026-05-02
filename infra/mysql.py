
import os
from contextlib import contextmanager
from typing import Generator

import mysql.connector
from mysql.connector.pooling import MySQLConnectionPool

_pool: MySQLConnectionPool | None = None


def _get_pool() -> MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = MySQLConnectionPool(
            pool_name="mibo",
            pool_size=int(os.getenv("MYSQL_POOL_SIZE", "5")),
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            database=os.getenv("MYSQL_DATABASE", "mibo"),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "jyf111229"),
            charset="utf8mb4",
            autocommit=True,
        )
    return _pool


@contextmanager
def get_conn() -> Generator:
    """获取连接，用完自动归还连接池"""
    conn = _get_pool().get_connection()
    try:
        yield conn
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[dict]:
    """执行查询，返回字典列表"""
    with get_conn() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        return cursor.fetchall()


def execute(sql: str, params: tuple = ()) -> int:
    """执行写操作，返回影响行数"""
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.rowcount
