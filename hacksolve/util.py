"""Shared helpers: API errors and request-body validation."""
from __future__ import annotations

import re
from datetime import date

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,190}\.[A-Za-z]{2,24}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details=None, headers=None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details, self.headers = details, headers or {}


class Fields:
    """Collects every validation problem so the client sees them all at once."""

    def __init__(self, body):
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", "Expected a JSON object")
        self.b, self.errors = body, {}

    def _get(self, key, required):
        v = self.b.get(key)
        if v is None or v == "":
            if required:
                self.errors[key] = "This field is required"
            return None
        return v

    def str(self, key, lo=1, hi=200, required=True, default=None):
        v = self._get(key, required)
        if v is None:
            return default
        if not isinstance(v, str):
            self.errors[key] = "Must be text"
            return default
        v = v.strip()
        if not lo <= len(v) <= hi:
            self.errors[key] = f"Must be {lo}-{hi} characters"
            return default
        return v

    def email(self, key="email"):
        v = self.str(key, 3, 254)
        if v is not None and not EMAIL_RE.match(v):
            self.errors[key] = "Enter a valid email address"
            return None
        return v.lower() if v else v

    def enum(self, key, choices, required=False, default=None):
        v = self._get(key, required)
        if v is None:
            return default
        if v not in choices:
            self.errors[key] = "Must be one of: " + ", ".join(choices)
            return default
        return v

    def date(self, key, required=False):
        v = self._get(key, required)
        if v is None:
            return None
        try:
            if not isinstance(v, str) or not DATE_RE.match(v):
                raise ValueError
            date.fromisoformat(v)
        except ValueError:
            self.errors[key] = "Use the format YYYY-MM-DD"
            return None
        return v

    def url(self, key, required=False):
        v = self.str(key, 1, 500, required)
        if v is not None and not re.match(r"^https?://[^\s<>\"']+$", v, re.I):
            self.errors[key] = "Enter a valid http(s) link"
            return None
        return v

    def has(self, key):
        return key in self.b

    def done(self):
        if self.errors:
            raise ApiError(422, "validation_error", "Some fields need attention", self.errors)
