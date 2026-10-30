"""Receiving API: start a check, send the slip photo and the delivery photo, count, confirm."""

from __future__ import annotations

import base64
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.db.config import get_settings
from backend.db.session import get_db
from backend.modules.agent.router import get_planner_client
from backend.modules.purchasing.models import PurchaseOrder, Receipt
from backend.modules.receiving import service
from backend.modules.receiving.vision import VisionError, read_photo, read_slip

router = APIRouter(tags=["receiving"])
DEMO_ASSETS = Path("demo-assets/receiving")
DEMO_FILES = {"slip": ("packing-slip-PS-58213.png", "image/png"), "photo": ("delivery-photo.jpg", "image/jpeg")}


class OrderLineOut(BaseModel):
    line_no: int
    ingredient_id: str
    supplier_item_number: str | None
    description: str | None
    quantity: Decimal
    unit: str
    packs: int | None
    pack_name: str | None


class OpenOrderOut(BaseModel):
    order_id: str
    po_number: str | None
    supplier_name: str
    status: str
    requested_delivery_at: datetime
    confirmed_delivery_at: datetime | None
    simulated: bool
    lines: list[OrderLineOut]


class ReceiptLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_no: int
    ingredient_id: str | None
    supplier_item_number: str | None
    description: str
    unit: str | None
    unit_size: Decimal | None
    ordered_quantity: Decimal | None
    slip_shipped_quantity: Decimal | None
    slip_backordered_quantity: Decimal | None
    seen_quantity: Decimal | None
    seen_may_hide_more: bool
    counted_quantity: Decimal | None
    rejected_quantity: Decimal
    rejected_reason: str | None
    accepted_quantity: Decimal | None
    status: str
    message: str
    display: dict[str, str] = Field(default_factory=dict, description="Readable quantities: ordered, shipped, seen, counted, accepted.")


class ReceiptOut(BaseModel):
    receipt_id: str
    cafe_id: str
    status: str
    order: OpenOrderOut | None
    slip_number: str | None
    slip_supplier: str | None
    slip_po_number: str | None
    slip_delivery: str | None
    has_slip_image: bool
    has_photo_image: bool
    photo_notes: str | None
    model: str | None
    lines: list[ReceiptLineOut]
    summary: dict[str, int]
    credit_request: dict[str, Any] | None
    confirmed_at: datetime | None
    confirmed_by: str | None
    muse_message: str = Field(description="One line for Muse to say (in the ear or on screen).")


def _order_out(order: PurchaseOrder | None) -> OpenOrderOut | None:
    if order is None:
        return None
    return OpenOrderOut(
        order_id=order.order_id, po_number=order.po_number, supplier_name=order.supplier_name, status=order.status,
        requested_delivery_at=order.requested_delivery_at, confirmed_delivery_at=order.confirmed_delivery_at,
        simulated=order.simulated,
        lines=[OrderLineOut(line_no=l.line_no, ingredient_id=l.ingredient_id, supplier_item_number=l.supplier_item_number,
                            description=l.description, quantity=l.quantity, unit=l.unit, packs=l.packs,
                            pack_name=l.pack_name) for l in order.lines],
    )


def _muse_line(receipt: Receipt) -> str:
    counts = service.summary(receipt)
    if receipt.status == "confirmed":
        added = [l for l in receipt.lines if l.accepted_quantity]
        text = "Received. Added to stock: " + ", ".join(
            f"{service._fmt(l.accepted_quantity, l.unit, l.unit_size)} {l.description.split(',')[0].lower()}" for l in added
        ) + "."
        if receipt.credit_request:
            text += f" Credit request {receipt.credit_request['credit_ref']} is {receipt.credit_request['status']}."
        return text
    if not receipt.lines:
        return "Look at the packing slip and I'll read it."
    if counts["needs_count"]:
        first = next(l for l in receipt.lines if l.status == "needs_count")
        return first.message
    if counts["problems"]:
        first = next(l for l in receipt.lines if l.status not in ("match", "needs_count"))
        return first.message + " Approve to accept what's here?"
    return f"Everything matches: {counts['match']} of {counts['lines']} lines. Approve to add it to stock?"


def _out(session: Session, receipt: Receipt) -> ReceiptOut:
    order = session.get(PurchaseOrder, receipt.order_id) if receipt.order_id else None
    lines = []
    for line in receipt.lines:
        out = ReceiptLineOut.model_validate(line)
        f = lambda q: service._fmt(q, line.unit, line.unit_size)  # noqa: E731
        out.display = {k: f(v) for k, v in {
            "ordered": line.ordered_quantity, "shipped": line.slip_shipped_quantity, "seen": line.seen_quantity,
            "counted": line.counted_quantity, "accepted": line.accepted_quantity,
        }.items() if v is not None}
        lines.append(out)
    return ReceiptOut(
        receipt_id=receipt.receipt_id, cafe_id=receipt.cafe_id, status=receipt.status, order=_order_out(order),
        slip_number=receipt.slip_number, slip_supplier=receipt.slip_supplier, slip_po_number=receipt.slip_po_number,
        slip_delivery=receipt.slip_delivery, has_slip_image=bool(receipt.slip_image_path),
        has_photo_image=bool(receipt.photo_image_path),
        photo_notes=(receipt.photo_read or {}).get("notes"), model=receipt.model, lines=lines,
        summary=service.summary(receipt), credit_request=receipt.credit_request,
        confirmed_at=receipt.confirmed_at, confirmed_by=receipt.confirmed_by, muse_message=_muse_line(receipt),
    )


def _load(session: Session, receipt_id: str) -> Receipt:
    receipt = session.get(Receipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found")
    return receipt


class StartReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cafe_id: str = "CAFE001"
    order_id: str | None = Field(default=None, description="Omit to match by the PO number on the slip.")


class ImageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_base64: str | None = Field(default=None, description="The photo (from glasses or a phone), base64.")
    mime: Literal["image/jpeg", "image/png", "image/webp"] = "image/jpeg"
    use_demo_image: bool = Field(default=False, description="Use the demo photo in demo-assets/receiving.")


class CountIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    counted_units: int | None = Field(default=None, ge=0, description="Cartons / units counted (preferred).")
    counted_quantity: Decimal | None = Field(default=None, ge=0, description="Or the amount in base units (mL, g).")
    rejected_units: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=200)


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed_by: str = Field(default="owner", min_length=1, max_length=100)
    channel: Literal["web", "voice", "glasses", "api"] = "web"


def _image(body: ImageIn, kind: str) -> tuple[bytes, str]:
    if body.use_demo_image:
        name, mime = DEMO_FILES[kind]
        return (DEMO_ASSETS / name).read_bytes(), mime
    if not body.image_base64:
        raise HTTPException(status_code=422, detail="Send image_base64, or use_demo_image: true.")
    try:
        return base64.b64decode(body.image_base64.split(",")[-1], validate=True), body.mime
    except ValueError as error:
        raise HTTPException(status_code=422, detail="image_base64 is not valid base64.") from error


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except service.ReceivingError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("/orders", response_model=list[OpenOrderOut])
def list_open_orders(cafe_id: str = Query("CAFE001"), database_session: Session = Depends(get_db)) -> list[OpenOrderOut]:
    """Confirmed orders waiting to be received."""
    orders = service.open_orders(database_session, cafe_id)
    database_session.commit()
    return [_order_out(o) for o in orders]


@router.post("/receipts", response_model=ReceiptOut)
def start(body: StartReceipt, database_session: Session = Depends(get_db)) -> ReceiptOut:
    receipt = _guard(service.start_receipt, database_session, body.cafe_id, body.order_id)
    database_session.commit()
    return _out(database_session, receipt)


@router.get("/receipts/{receipt_id}", response_model=ReceiptOut)
def read(receipt_id: str, database_session: Session = Depends(get_db)) -> ReceiptOut:
    return _out(database_session, _load(database_session, receipt_id))


@router.post("/receipts/{receipt_id}/slip", response_model=ReceiptOut)
def add_slip(receipt_id: str, body: ImageIn, database_session: Session = Depends(get_db),
             client: Any = Depends(get_planner_client)) -> ReceiptOut:
    """Muse reads the packing slip photo; code matches it to the order."""
    receipt = _load(database_session, receipt_id)
    image, mime = _image(body, "slip")
    model = get_settings().planner_model
    try:
        slip, _ = read_slip(client, model, image, mime)
    except VisionError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    receipt = _guard(service.attach_slip, database_session, receipt, slip, model, image, mime)
    database_session.commit()
    return _out(database_session, receipt)


@router.post("/receipts/{receipt_id}/photo", response_model=ReceiptOut)
def add_photo(receipt_id: str, body: ImageIn, database_session: Session = Depends(get_db),
              client: Any = Depends(get_planner_client)) -> ReceiptOut:
    """Muse counts what it can see in the delivery photo; code compares with the slip and the order."""
    receipt = _load(database_session, receipt_id)
    image, mime = _image(body, "photo")
    model = get_settings().planner_model
    try:
        photo, _ = read_photo(client, model, image, mime)
    except VisionError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    receipt = _guard(service.attach_photo, database_session, receipt, photo, model, image, mime)
    database_session.commit()
    return _out(database_session, receipt)


@router.post("/receipts/{receipt_id}/lines/{line_no}", response_model=ReceiptOut)
def count_line(receipt_id: str, line_no: int, body: CountIn, database_session: Session = Depends(get_db)) -> ReceiptOut:
    """The owner's count (and anything rejected as damaged)."""
    receipt = _load(database_session, receipt_id)
    line = next((l for l in receipt.lines if l.line_no == line_no), None)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Line not found")
    unit = line.unit_size or Decimal(1)
    counted = body.counted_quantity if body.counted_quantity is not None else (
        Decimal(body.counted_units) * unit if body.counted_units is not None else None)
    rejected = Decimal(body.rejected_units) * unit if body.rejected_units is not None else None
    receipt = _guard(service.set_count, database_session, receipt, line_no, counted=counted, rejected=rejected,
                     reason=body.reason)
    database_session.commit()
    return _out(database_session, receipt)


@router.post("/receipts/{receipt_id}/confirm", response_model=ReceiptOut)
def confirm(receipt_id: str, body: ConfirmIn, database_session: Session = Depends(get_db)) -> ReceiptOut:
    """Owner approves: accepted quantities go into stock; shortfalls become a drafted credit request."""
    receipt = _guard(service.confirm, database_session, _load(database_session, receipt_id),
                     confirmed_by=body.confirmed_by, channel=body.channel)
    database_session.commit()
    return _out(database_session, receipt)


@router.post("/receipts/{receipt_id}/credit-request/send", response_model=ReceiptOut)
def send_credit(receipt_id: str, database_session: Session = Depends(get_db)) -> ReceiptOut:
    receipt = _guard(service.send_credit_request, database_session, _load(database_session, receipt_id))
    database_session.commit()
    return _out(database_session, receipt)


@router.get("/receipts/{receipt_id}/images/{kind}")
def image(receipt_id: str, kind: Literal["slip", "photo"], database_session: Session = Depends(get_db)) -> FileResponse:
    receipt = _load(database_session, receipt_id)
    path = receipt.slip_image_path if kind == "slip" else receipt.photo_image_path
    if not path or not Path(path).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No image")
    return FileResponse(path)
