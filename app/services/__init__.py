"""Application services (use cases).

Dependencies arrive through constructors, typed against domain ports, and are wired in app/di.py. Services own
transaction boundaries (UnitOfWork.commit) and must not import infrastructure or interface code.
"""
