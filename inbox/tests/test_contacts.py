"""Tests for contacts.py — phone normalization and contact resolution."""

from __future__ import annotations

from contacts import ContactBook, _digits_only, _phone_variants

# ── _digits_only ────────────────────────────────────────────────────────────


class TestDigitsOnly:
    def test_strips_non_digits(self):
        assert _digits_only("+1 (415) 555-1234") == "14155551234"

    def test_returns_none_for_empty(self):
        assert _digits_only("") is None

    def test_returns_none_for_no_digits(self):
        assert _digits_only("abc") is None

    def test_already_clean(self):
        assert _digits_only("4155551234") == "4155551234"

    def test_international_format(self):
        assert _digits_only("+44 20 7946 0958") == "442079460958"


# ── _phone_variants ─────────────────────────────────────────────────────────


class TestPhoneVariants:
    def test_empty_input(self):
        assert _phone_variants("") == []

    def test_no_digits(self):
        assert _phone_variants("hello") == []

    def test_10_digit_us(self):
        variants = _phone_variants("4155551234")
        assert "4155551234" in variants
        assert "+4155551234" in variants
        # Should also have 11-digit variant with leading 1
        assert "14155551234" in variants
        assert "+14155551234" in variants

    def test_11_digit_us_with_leading_1(self):
        variants = _phone_variants("14155551234")
        assert "14155551234" in variants
        assert "+14155551234" in variants
        # Should also have 10-digit short form
        assert "4155551234" in variants
        assert "+4155551234" in variants

    def test_formatted_phone(self):
        variants = _phone_variants("+1 (415) 555-1234")
        assert "14155551234" in variants
        assert "4155551234" in variants

    def test_no_duplicates(self):
        variants = _phone_variants("4155551234")
        assert len(variants) == len(set(variants))

    def test_short_number_no_expansion(self):
        # 7 digits — not 10 or 11, so no US expansion
        variants = _phone_variants("5551234")
        assert variants == ["5551234", "+5551234"]

    def test_international_no_expansion(self):
        # 12 digits — not 10 or 11
        variants = _phone_variants("442079460958")
        assert variants == ["442079460958", "+442079460958"]


# ── ContactBook ─────────────────────────────────────────────────────────────


class TestContactBook:
    def _make_book(self, mapping: dict[str, str]) -> ContactBook:
        book = ContactBook()
        book._map = mapping
        return book

    def test_resolve_direct_hit_email(self):
        book = self._make_book({"alice@example.com": "Alice Smith"})
        assert book.resolve("alice@example.com") == "Alice Smith"

    def test_resolve_case_insensitive_email(self):
        book = self._make_book({"alice@example.com": "Alice Smith"})
        assert book.resolve("Alice@Example.COM") == "Alice Smith"

    def test_resolve_phone_direct(self):
        book = self._make_book({"4155551234": "Bob Jones"})
        assert book.resolve("4155551234") == "Bob Jones"

    def test_resolve_phone_variant_match(self):
        book = self._make_book({"14155551234": "Bob Jones"})
        # Input is 10-digit, but map has 11-digit — variants should match
        assert book.resolve("4155551234") == "Bob Jones"

    def test_resolve_formatted_phone(self):
        book = self._make_book({"+14155551234": "Bob Jones"})
        assert book.resolve("+1 (415) 555-1234") == "Bob Jones"

    def test_resolve_unknown_returns_raw(self):
        book = self._make_book({})
        assert book.resolve("+99999999999") == "+99999999999"

    def test_resolve_empty_returns_empty(self):
        book = self._make_book({"foo": "bar"})
        assert book.resolve("") == ""

    def test_resolve_strips_whitespace(self):
        book = self._make_book({"alice@example.com": "Alice"})
        assert book.resolve("  alice@example.com  ") == "Alice"
