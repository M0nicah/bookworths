"""Bookworths — clear books, real value.

Financial reconciliation and profit intelligence for Kenyan Instagram and
social-commerce sellers.
"""
__version__ = "1.0.0"
TAGLINE = "Clear books, real value"

from .schema import Account, Classification, ClassifiedTransaction, Direction, Transaction

__all__ = [
    "__version__", "TAGLINE",
    "Account", "Classification", "ClassifiedTransaction", "Direction", "Transaction",
]
