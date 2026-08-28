"""Paper broker adapters."""

import uuid
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Dict, List

from .domain import (
    AccountSnapshot,
    AssetClass,
    OrderReceipt,
    OrderRequest,
    Position,
    Side,
    decimal,
    utc_now,
)
from .http import request_json


class Broker(ABC):
    @abstractmethod
    def account(self) -> AccountSnapshot:
        raise NotImplementedError

    @abstractmethod
    def positions(self) -> List[Position]:
        raise NotImplementedError

    @abstractmethod
    def submit(self, order: OrderRequest) -> OrderReceipt:
        raise NotImplementedError


class AlpacaPaperBroker(Broker):
    def __init__(self, base_url: str, api_key: str, api_secret: str):
        if "paper-api" not in base_url:
            raise ValueError("AlpacaPaperBroker requires the paper-api endpoint")
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }

    def account(self) -> AccountSnapshot:
        payload = request_json("GET", self.base_url + "/v2/account", headers=self.headers)
        if payload.get("trading_blocked") or payload.get("account_blocked"):
            raise RuntimeError("Alpaca reports that trading is blocked for this paper account")
        return AccountSnapshot(
            cash=decimal(payload["cash"]),
            equity=decimal(payload["equity"]),
            buying_power=decimal(payload["buying_power"]),
            last_equity=decimal(payload.get("last_equity", payload["equity"])),
            as_of=utc_now(),
        )

    def positions(self) -> List[Position]:
        payload = request_json("GET", self.base_url + "/v2/positions", headers=self.headers)
        items = payload if isinstance(payload, list) else payload.get("positions", [])
        result: List[Position] = []
        for item in items:
            asset_class = AssetClass.OPTION if item.get("asset_class") == "us_option" else AssetClass.EQUITY
            result.append(
                Position(
                    symbol=item["symbol"],
                    quantity=decimal(item["qty"]),
                    market_value=abs(decimal(item["market_value"])),
                    average_entry_price=decimal(item["avg_entry_price"]),
                    asset_class=asset_class,
                )
            )
        return result

    def submit(self, order: OrderRequest) -> OrderReceipt:
        body = {
            "symbol": order.symbol,
            "side": order.side.value,
            "type": order.order_type,
            "time_in_force": order.time_in_force,
            "client_order_id": order.client_order_id,
        }
        if order.notional is not None:
            body["notional"] = str(order.notional)
        elif order.quantity is not None:
            body["qty"] = str(order.quantity)
        else:
            raise ValueError("An order requires notional or quantity")
        payload = request_json("POST", self.base_url + "/v2/orders", headers=self.headers, body=body)
        return OrderReceipt(
            broker_order_id=str(payload["id"]),
            client_order_id=str(payload.get("client_order_id", order.client_order_id)),
            symbol=str(payload["symbol"]),
            side=Side(str(payload["side"])),
            status=str(payload["status"]),
            submitted_at=utc_now(),
        )


class SimulatedBroker(Broker):
    """In-process broker for end-to-end dry runs and tests; state resets per process."""

    def __init__(self, cash: Decimal, prices: Dict[str, Decimal] = None):
        self.cash = decimal(cash)
        self.last_equity = self.cash
        self.prices = {key.upper(): decimal(value) for key, value in (prices or {}).items()}
        self._positions: Dict[str, Position] = {}
        self.orders: List[OrderRequest] = []

    def set_price(self, symbol: str, price: Decimal) -> None:
        self.prices[symbol.upper()] = decimal(price)

    def account(self) -> AccountSnapshot:
        position_value = sum(
            position.quantity * self.prices.get(symbol, position.average_entry_price)
            for symbol, position in self._positions.items()
        )
        equity = self.cash + position_value
        return AccountSnapshot(
            cash=self.cash,
            equity=equity,
            buying_power=self.cash,
            last_equity=self.last_equity,
            as_of=utc_now(),
        )

    def positions(self) -> List[Position]:
        return [
            Position(
                symbol=position.symbol,
                quantity=position.quantity,
                market_value=position.quantity * self.prices.get(symbol, position.average_entry_price),
                average_entry_price=position.average_entry_price,
                asset_class=position.asset_class,
            )
            for symbol, position in self._positions.items()
        ]

    def submit(self, order: OrderRequest) -> OrderReceipt:
        price = self.prices.get(order.symbol)
        if price is None or price <= 0:
            raise ValueError("No simulated price for {}".format(order.symbol))
        if order.side == Side.BUY:
            notional = order.notional if order.notional is not None else order.quantity * price
            if notional > self.cash:
                raise ValueError("Insufficient simulated cash")
            quantity = notional / price
            current = self._positions.get(order.symbol)
            old_quantity = current.quantity if current else Decimal("0")
            old_cost = old_quantity * (current.average_entry_price if current else Decimal("0"))
            new_quantity = old_quantity + quantity
            average = (old_cost + notional) / new_quantity
            self.cash -= notional
            self._positions[order.symbol] = Position(
                symbol=order.symbol,
                quantity=new_quantity,
                market_value=new_quantity * price,
                average_entry_price=average,
                asset_class=order.asset_class,
            )
        else:
            current = self._positions.get(order.symbol)
            if not current:
                raise ValueError("Cannot sell a missing simulated position")
            quantity = order.quantity if order.quantity is not None else order.notional / price
            if quantity > current.quantity:
                raise ValueError("Cannot create a simulated short position")
            self.cash += quantity * price
            remaining = current.quantity - quantity
            if remaining == 0:
                del self._positions[order.symbol]
            else:
                self._positions[order.symbol] = Position(
                    symbol=current.symbol,
                    quantity=remaining,
                    market_value=remaining * price,
                    average_entry_price=current.average_entry_price,
                    asset_class=current.asset_class,
                )
        self.orders.append(order)
        return OrderReceipt(
            broker_order_id="sim-{}".format(uuid.uuid4().hex),
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            status="filled",
            submitted_at=utc_now(),
        )
