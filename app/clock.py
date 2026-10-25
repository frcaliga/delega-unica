from datetime import date, datetime
from zoneinfo import ZoneInfo

ROMA = ZoneInfo("Europe/Rome")


def oggi() -> date:
    """Data corrente in Italia: le scadenze delle deleghe sono date italiane."""
    return datetime.now(ROMA).date()


def adesso() -> datetime:
    return datetime.now(ROMA)
