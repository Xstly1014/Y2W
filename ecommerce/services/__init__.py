"""Services package — business logic layer.

Routes never touch ORM objects directly; they go through services which
encapsulate transactions, inventory reservations, and money calculations.
"""
