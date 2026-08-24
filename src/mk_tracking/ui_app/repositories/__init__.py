"""Data repository implementations."""

from .base import Repository
from .json_repository import JsonRepository
from .postgres_repository import PostgresRepository

__all__ = ["JsonRepository", "PostgresRepository", "Repository"]
