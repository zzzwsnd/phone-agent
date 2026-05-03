"""
数据库表结构定义

提供 visitor_records 表的 DDL 和建表函数
"""
from infra.mysql import execute


VISITOR_RECORDS_DDL = """
CREATE TABLE IF NOT EXISTS visitor_records (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    caller_number    VARCHAR(20) NOT NULL,
    license_plate    VARCHAR(20) DEFAULT NULL,
    visiting_company VARCHAR(100) DEFAULT NULL,
    visitor_phone    VARCHAR(20) DEFAULT NULL,
    purpose          VARCHAR(50) DEFAULT NULL,
    visitor_name     VARCHAR(50) DEFAULT NULL,
    call_room_name   VARCHAR(100) DEFAULT NULL,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_caller_number (caller_number),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def create_tables() -> None:
    """创建所有业务表（幂等）"""
    execute(VISITOR_RECORDS_DDL)
