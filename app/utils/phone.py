import re


def normalize_phone(raw: str, default_country_code: str = "91") -> str:
    """Return the number as +<countrycode><number>, e.g. 98765 43210 -> +919876543210."""
    digits = re.sub(r"\D", "", raw)  # keep digits only
    if len(digits) == 10:  # no country code given, assume default
        digits = default_country_code + digits
    return "+" + digits