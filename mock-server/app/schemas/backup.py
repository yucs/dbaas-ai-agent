"""备份接口 schema。"""

from pydantic import Field

from .service_detail import ApiSchema


class BackupRecord(ApiSchema):
    """对外返回的备份记录。"""

    backup_id: str = Field(description="单个备份文件唯一 ID")
    task_id: str = Field(description="产生该备份记录的任务 ID")
    service_name: str = Field(description="服务组名称")
    service_type: str = Field(description="服务组类型")
    child_service_name: str = Field(description="子服务名称")
    child_service_type: str = Field(description="子服务类型，例如 mysql、tidb、tikv")
    unit_name: str = Field(description="备份所属或执行的单元名称")
    backup_type: str = Field(description="备份类型，例如 full、incremental、ddl、table")
    backup_path: str = Field(description="备份文件路径")
    size_bytes: int = Field(description="备份文件大小，单位 byte")
    storage_type: str | None = Field(default=None, description="存储类型，例如 NAS、S3")
    compress_mode: str | None = Field(default=None, description="压缩模式，例如 none、gzip、zstd")
    started_at: str | None = Field(default=None, description="备份开始时间，格式 YYYY-MM-DD HH:mm:ss")
    finished_at: str | None = Field(default=None, description="备份结束时间，未完成时可为 null")
    expires_at: str | None = Field(default=None, description="备份过期时间，未设置时可为 null")
    duration_seconds: int | None = Field(default=None, description="备份耗时，单位秒，未完成时可为 null")
    task_status: str = Field(description="任务状态，例如 created、running、succeeded、failed")
    task_error: str | None = Field(default=None, description="任务错误信息，没有错误时为 null")
    valid_status: str | None = Field(default=None, description="备份有效性校验状态，没有值时为 null")
    remark: str | None = Field(default=None, description="备注")
