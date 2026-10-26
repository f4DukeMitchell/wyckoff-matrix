import datetime

# Official NYSE & NASDAQ Market Holidays (2025 - 2028)
US_MARKET_HOLIDAYS = {
    # 2025
    datetime.date(2025, 1, 1),   # New Year's Day
    datetime.date(2025, 1, 20),  # MLK Jr. Day
    datetime.date(2025, 2, 17),  # Washington's Birthday (Presidents Day)
    datetime.date(2025, 4, 18),  # Good Friday
    datetime.date(2025, 5, 26),  # Memorial Day
    datetime.date(2025, 6, 19),  # Juneteenth
    datetime.date(2025, 7, 4),   # Independence Day
    datetime.date(2025, 9, 1),   # Labor Day
    datetime.date(2025, 11, 27), # Thanksgiving
    datetime.date(2025, 12, 25), # Christmas
    
    # 2026
    datetime.date(2026, 1, 1),   # New Year's Day
    datetime.date(2026, 1, 19),  # MLK Jr. Day
    datetime.date(2026, 2, 16),  # Presidents Day
    datetime.date(2026, 4, 3),   # Good Friday
    datetime.date(2026, 5, 25),  # Memorial Day
    datetime.date(2026, 6, 19),  # Juneteenth
    datetime.date(2026, 7, 3),   # Independence Day (Observed)
    datetime.date(2026, 9, 7),   # Labor Day
    datetime.date(2026, 11, 26), # Thanksgiving
    datetime.date(2026, 12, 25), # Christmas
    
    # 2027
    datetime.date(2027, 1, 1),   # New Year's Day
    datetime.date(2027, 1, 18),  # MLK Jr. Day
    datetime.date(2027, 2, 15),  # Presidents Day
    datetime.date(2027, 3, 26),  # Good Friday
    datetime.date(2027, 5, 31),  # Memorial Day
    datetime.date(2027, 6, 18),  # Juneteenth (Observed)
    datetime.date(2027, 7, 5),   # Independence Day (Observed)
    datetime.date(2027, 9, 6),   # Labor Day
    datetime.date(2027, 11, 25), # Thanksgiving
    datetime.date(2027, 12, 24), # Christmas (Observed)
}

def is_market_day(d: datetime.date = None) -> bool:
    """Returns True if date is Monday-Friday and NOT an official US stock market holiday."""
    if d is None:
        d = datetime.date.today()
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    if d in US_MARKET_HOLIDAYS:
        return False
    return True
