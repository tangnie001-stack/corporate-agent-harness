"""API 响应码和消息常量。

所有错误码用大写字符串，对应的消息用 `{CODE}_MSG` 命名。
添加新错误码时必须同时添加 `_MSG`。"""


class Code:
    # 通用
    SUCCESS = "SUCCESS"
    SUCCESS_MSG = "操作成功"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INTERNAL_ERROR_MSG = "服务器内部错误"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    UNKNOWN_ERROR_MSG = "未知错误"
    NOT_FOUND = "NOT_FOUND"
    NOT_FOUND_MSG = "资源不存在"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    METHOD_NOT_ALLOWED_MSG = "请求方法不允许"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    VALIDATION_ERROR_MSG = "参数校验失败"

    # 认证
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_REQUIRED_MSG = "请先登录"
    AUTH_TOKEN_EXPIRED = "AUTH_TOKEN_EXPIRED"
    AUTH_TOKEN_EXPIRED_MSG = "Token 已过期，请重新登录"
    AUTH_WRONG_PASSWORD = "AUTH_WRONG_PASSWORD"
    AUTH_WRONG_PASSWORD_MSG = "密码错误"
    AUTH_ACCOUNT_EXISTS = "AUTH_ACCOUNT_EXISTS"
    AUTH_ACCOUNT_EXISTS_MSG = "账号已存在"

    # 知识库
    KB_NOT_FOUND = "KB_NOT_FOUND"
    KB_NOT_FOUND_MSG = "知识库不存在"
    KB_DELETE_FAILED = "KB_DELETE_FAILED"
    KB_DELETE_FAILED_MSG = "知识库删除失败"

    # 文件
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    FILE_TOO_LARGE_MSG = "文件超过 10MB 上限"
    FILE_TYPE_UNSUPPORTED = "FILE_TYPE_UNSUPPORTED"
    FILE_TYPE_UNSUPPORTED_MSG = "不支持的文件类型"
    FILE_UPLOAD_FAILED = "FILE_UPLOAD_FAILED"
    FILE_UPLOAD_FAILED_MSG = "文件上传到存储服务失败"
    FILE_DUPLICATE = "FILE_DUPLICATE"
    FILE_DUPLICATE_MSG = "文件已存在，请勿重复上传"

    # 文档
    DOC_PROCESSING_FAILED = "DOC_PROCESSING_FAILED"
    DOC_PROCESSING_FAILED_MSG = "文档处理失败"
    DOC_NOT_FOUND = "DOC_NOT_FOUND"
    DOC_NOT_FOUND_MSG = "文档不存在"
    DOC_DELETE_NOT_ALLOWED = "DOC_DELETE_NOT_ALLOWED"
    DOC_DELETE_NOT_ALLOWED_MSG = "只能删除自己上传的文档"
    DOC_STATUS_CONFLICT = "DOC_STATUS_CONFLICT"
    DOC_STATUS_CONFLICT_MSG = (
        "文档当前状态不允许删除，仅 ready 和 failed 状态的文档可删除"
    )

    # 会话
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_NOT_FOUND_MSG = "会话不存在"

    # 已下线端点
    GONE = "GONE"
    GONE_MSG = (
        "RAGAS 出题已下线。请使用 DeepEval Synthesizer："
        "python -m src.cli.testset_synth --kb-id <uuid> --version <n> --docs <文件...>"
    )
