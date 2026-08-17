"""Central model registry.

SQLAlchemy only knows about a table once its class has been imported. Every
module's models are re-exported here so ``Base.metadata`` is complete before
``create_all`` runs. Each new room appends its import below.
"""

from app.modules.identity.models import User

__all__ = ["User"]
