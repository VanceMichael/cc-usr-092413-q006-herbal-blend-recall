"""领域错误类型。路由层统一映射为 HTTP 响应。"""


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"


class ValidationError(DomainError):
    """输入不合法: 守恒破坏、数量不足、单位不一致等。"""
    status_code = 422
    code = "validation_error"


class ConflictError(DomainError):
    """状态冲突: 库存版本变化、流水号内容不一致、非法状态流转。"""
    status_code = 409
    code = "conflict"


class ForbiddenError(DomainError):
    """职责分离约束: 如提出人不得兼任复核人。"""
    status_code = 403
    code = "forbidden"
