"""PostgreSQL 各表的 SQL 访问层（repo 包入口）。"""

from src.infra.db.repos.chat_repo import ChatRepo
from src.infra.db.repos.diag_repo import DiagRepo
from src.infra.db.repos.document_repo import DocumentRepo
from src.infra.db.repos.eval_repo import EvalRepo
from src.infra.db.repos.kb_repo import KbRepo
from src.infra.db.repos.user_repo import UserRepo

__all__ = [
    "ChatRepo",
    "DiagRepo",
    "DocumentRepo",
    "EvalRepo",
    "KbRepo",
    "UserRepo",
]
