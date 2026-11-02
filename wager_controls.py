"""Whole-dollar UI limits; backend money calculations remain cent-precise."""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR


def maximum_dollars(balance):
    return max(0, min(5000, int(Decimal(str(balance)).to_integral_value(rounding=ROUND_FLOOR))))


def whole_dollars(amount):
    try:
        value = Decimal(str(amount))
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("Enter a whole-dollar wager.")
        return int(value)
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid virtual wager.") from None


def initial_wager(draft, limit):
    """Normalize only an unsent UI draft, including older decimal widget state."""
    previous = Decimal(str(draft.get("stake_dollars", draft.get("stake", 50))))
    return max(1, min(max(1, limit), int(previous.to_integral_value(rounding=ROUND_FLOOR))))
