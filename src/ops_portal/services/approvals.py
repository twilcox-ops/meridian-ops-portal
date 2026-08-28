"""Maker-checker approval workflow.

Scope (decided): this gates Project 2 review-queue corrections
specifically — requested by one user, approved by a different user,
executed after approval, fully logged via services/audit.py.

TODO: request_correction(), approve(), and the same-user self-approval
block (requester != approver, enforced server-side).
"""
