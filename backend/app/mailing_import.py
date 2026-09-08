"""Conservative CSV mapping: an absent status is never assumed to be consent."""

import csv
import io
import re
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, TypeAdapter, ValidationError


class ImportRow(BaseModel):
    email: EmailStr
    first_name: str = Field(default="", max_length=150)
    last_name: str = Field(default="", max_length=150)
    status: Literal["subscribed", "unsubscribed", "pending", "unknown"]
    lists: list[str] = Field(default_factory=list, max_length=50)
    source_details: dict[str, str] = Field(default_factory=dict)


class ImportPreview(BaseModel):
    csv_text: str = Field(max_length=5_000_000)
    default_status: Literal["unknown", "subscribed", "unsubscribed"] = "unknown"


class ImportBatch(BaseModel):
    rows: list[ImportRow] = Field(min_length=1, max_length=20)
    consent_confirmed: Literal[True]


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def parse_import(payload: ImportPreview):
    reader = csv.DictReader(io.StringIO(payload.csv_text.lstrip("\ufeff")), strict=True)
    if not reader.fieldnames:
        raise ValueError("The CSV file is empty.")
    normalized = [normalize(name) for name in reader.fieldnames]
    if len(set(normalized)) != len(normalized):
        raise ValueError("The CSV contains duplicate column names.")
    email_keys = ("email", "emailaddress")
    if not set(email_keys).intersection(normalized):
        raise ValueError("The CSV must include an Email or Email Address column.")
    contacts, errors = {}, []
    duplicates = 0
    priority = {"subscribed": 0, "unknown": 1, "pending": 2, "unsubscribed": 3}
    adapter = TypeAdapter(EmailStr)
    for number, raw in enumerate(reader, 2):
        if number > 5001:
            raise ValueError("Import at most 5,000 rows at a time.")
        if None in raw or any(value is None for value in raw.values()):
            errors.append({"row": number, "message": "The number of cells does not match the header."})
            continue
        row = {normalize(key): value.strip() for key, value in raw.items()}
        email = next((row[key] for key in email_keys if row.get(key)), "").lower()
        try:
            email = str(adapter.validate_python(email)).lower()
        except ValidationError:
            errors.append({"row": number, "message": "Invalid or missing email address."})
            continue
        value = next((row[key] for key in ("acceptsmarketing", "emailmarketingstatus", "subscriptionstatus", "subscribed", "status") if row.get(key)), "")
        status_map = {
            "subscribed": "subscribed", "subscriber": "subscribed", "true": "subscribed", "yes": "subscribed",
            "unsubscribed": "unsubscribed", "false": "unsubscribed", "no": "unsubscribed",
            "bounced": "unsubscribed", "suppressed": "unsubscribed", "complained": "unsubscribed",
            "pending": "pending", "unconfirmed": "pending", "notconfirmed": "pending",
        }
        status = status_map.get(normalize(value), "unknown") if value else payload.default_status
        try:
            lists = [name.strip() for name in row.get("mailinglists", "").split(",") if name.strip()]
            if any(len(name) > 150 for name in lists):
                raise ValueError("Mailing list name is too long.")
            item = ImportRow(email=email, first_name=row.get("firstname", ""), last_name=row.get("lastname", ""), status=status,
                lists=lists, source_details={key: row.get(key, "") for key in ["createdon", "subscribersince", "subscribersource", "acceptsmarketing", "tags"]})
        except ValidationError:
            errors.append({"row": number, "message": "A name exceeds the supported length."})
            continue
        if email in contacts:
            duplicates += 1
            merged_lists = sorted(set(item.lists) | set(contacts[email].lists))
            if priority[item.status] <= priority[contacts[email].status]:
                contacts[email].lists = merged_lists
                continue
            item.lists = merged_lists
        contacts[email] = item
    rows = list(contacts.values())
    return {"rows": [item.model_dump() for item in rows], "errors": errors, "duplicates": duplicates,
            "counts": {status: sum(item.status == status for item in rows) for status in priority}}
