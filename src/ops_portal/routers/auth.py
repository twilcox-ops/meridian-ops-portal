"""/login, /auth/callback, /logout.

TODO: redirect to Entra ID via auth/msal_client.py, handle the auth-code
callback, populate auth/session.py's server-side session from the ID
token's roles claim. A signed-out user must be able to reach nothing.
"""
