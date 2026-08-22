"""
Persistent Versioned Algorithmic Trading Configuration & Snapshot Engine for QuantEdge AI.

Phase 5.7 Implementation:
1. Persistent Algo Configuration:
   - Stores user-defined TP, SL, risk per trade, max daily loss, leverage.
   - Enforces strict account isolation (User A cannot access or mutate User B's config).
   - Strict fail-safe defaults: algo_enabled=False, kill_switch_active=True.
2. Safe Configuration Versioning:
   - Every update increments the configuration version (1 -> 2 -> 3).
3. Immutable Trade Configuration Snapshots:
   - When a trade setup is generated/executed, a snapshot of the exact configuration
     version is created and bound to the trade record.
   - Modifying active configuration affects ONLY future trades; existing trades remain
     pinned to their original configuration snapshot.
4. Authoritative TP/SL Calculation & Geometry:
   - Long: SL < Entry < TP
   - Short: TP < Entry < SL
   - Fail-closed geometry validation: zero exchange orders on invalid geometry.
5. Multi-Tier Persistence & Serialization:
   - State import/export for cross-restart and multi-tier synchronization.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import logging
import threading
from typing import Optional, Dict, Any, Tuple, Union

from quantedge.strategy.models import TradeDirection, StrategyDirection

logger = logging.getLogger("algo_config")


# ── Configuration Validation Error ────────────────────────────────────────────


class AlgoConfigValidationError(Exception):
    """Raised when algorithm configuration parameters violate safety boundaries."""
    pass


# ── Persistent Algorithm Configuration ────────────────────────────────────────


@dataclass
class AlgoConfiguration:
    """Persistent, user-defined algorithm configuration linked to a specific trading account."""
    account_id: str
    user_id: str
    take_profit_pct: Decimal = Decimal("2.00")       # Default 2.0%
    stop_loss_pct: Decimal = Decimal("1.00")         # Default 1.0%
    risk_per_trade_pct: Decimal = Decimal("1.00")    # Default 1.0% of balance
    max_risk_usd: Optional[Decimal] = None
    max_daily_loss_usd: Decimal = Decimal("500.00")
    max_leverage: int = 100
    algo_enabled: bool = False                       # Fail-safe default
    kill_switch_active: bool = True                  # Fail-safe default
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        self._validate()

    def _validate(self) -> None:
        if self.take_profit_pct <= Decimal("0"):
            raise AlgoConfigValidationError("Take Profit percentage must be greater than 0")
        if self.stop_loss_pct <= Decimal("0"):
            raise AlgoConfigValidationError("Stop Loss percentage must be greater than 0")
        if self.risk_per_trade_pct <= Decimal("0") or self.risk_per_trade_pct > Decimal("100"):
            raise AlgoConfigValidationError("Risk per trade percentage must be between 0 and 100")
        if self.max_leverage < 1 or self.max_leverage > 100:
            raise AlgoConfigValidationError("Max leverage must be between 1 and 100")
        if self.max_daily_loss_usd <= Decimal("0"):
            raise AlgoConfigValidationError("Max daily loss limit must be greater than 0")

    def update(
        self,
        take_profit_pct: Optional[Decimal] = None,
        stop_loss_pct: Optional[Decimal] = None,
        risk_per_trade_pct: Optional[Decimal] = None,
        max_risk_usd: Optional[Decimal] = None,
        max_daily_loss_usd: Optional[Decimal] = None,
        max_leverage: Optional[int] = None,
        algo_enabled: Optional[bool] = None,
        kill_switch_active: Optional[bool] = None,
    ) -> "AlgoConfiguration":
        """Update configuration parameters, increment version, and record timestamp."""
        new_tp = take_profit_pct if take_profit_pct is not None else self.take_profit_pct
        new_sl = stop_loss_pct if stop_loss_pct is not None else self.stop_loss_pct
        new_risk = risk_per_trade_pct if risk_per_trade_pct is not None else self.risk_per_trade_pct
        new_max_risk = max_risk_usd if max_risk_usd is not None else self.max_risk_usd
        new_daily_loss = max_daily_loss_usd if max_daily_loss_usd is not None else self.max_daily_loss_usd
        new_lev = max_leverage if max_leverage is not None else self.max_leverage
        new_algo = algo_enabled if algo_enabled is not None else self.algo_enabled
        new_ks = kill_switch_active if kill_switch_active is not None else self.kill_switch_active

        # Check safety rule: cannot enable algo if kill switch is active
        if new_algo and new_ks:
            raise AlgoConfigValidationError("Cannot enable algorithmic trading while emergency kill switch is active")

        # Validate numeric ranges
        if new_tp <= Decimal("0") or new_sl <= Decimal("0") or new_risk <= Decimal("0") or new_risk > Decimal("100") or new_daily_loss <= Decimal("0"):
            raise AlgoConfigValidationError("Invalid numeric ranges in configuration update")

        self.take_profit_pct = new_tp
        self.stop_loss_pct = new_sl
        self.risk_per_trade_pct = new_risk
        self.max_risk_usd = new_max_risk
        self.max_daily_loss_usd = new_daily_loss
        self.max_leverage = new_lev
        self.algo_enabled = new_algo
        self.kill_switch_active = new_ks
        self.version += 1
        self.updated_at = datetime.now(timezone.utc)
        return self

    def create_snapshot(self, setup_id: Optional[str] = None) -> "AlgoConfigurationSnapshot":
        """Create an immutable snapshot of the current configuration for a specific trade setup."""
        return AlgoConfigurationSnapshot(
            setup_id=setup_id,
            account_id=self.account_id,
            user_id=self.user_id,
            version=self.version,
            take_profit_pct=self.take_profit_pct,
            stop_loss_pct=self.stop_loss_pct,
            risk_per_trade_pct=self.risk_per_trade_pct,
            max_risk_usd=self.max_risk_usd,
            max_daily_loss_usd=self.max_daily_loss_usd,
            max_leverage=self.max_leverage,
            algo_enabled_at_snapshot=self.algo_enabled,
            kill_switch_active_at_snapshot=self.kill_switch_active,
            snapshot_timestamp=datetime.now(timezone.utc),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize configuration to dictionary."""
        return {
            "account_id": self.account_id,
            "user_id": self.user_id,
            "take_profit_pct": str(self.take_profit_pct),
            "stop_loss_pct": str(self.stop_loss_pct),
            "risk_per_trade_pct": str(self.risk_per_trade_pct),
            "max_risk_usd": str(self.max_risk_usd) if self.max_risk_usd is not None else None,
            "max_daily_loss_usd": str(self.max_daily_loss_usd),
            "max_leverage": self.max_leverage,
            "algo_enabled": self.algo_enabled,
            "kill_switch_active": self.kill_switch_active,
            "version": self.version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AlgoConfiguration":
        """Reconstruct configuration from serialized dictionary."""
        return cls(
            account_id=data["account_id"],
            user_id=data["user_id"],
            take_profit_pct=Decimal(str(data["take_profit_pct"])),
            stop_loss_pct=Decimal(str(data["stop_loss_pct"])),
            risk_per_trade_pct=Decimal(str(data["risk_per_trade_pct"])),
            max_risk_usd=Decimal(str(data["max_risk_usd"])) if data.get("max_risk_usd") is not None else None,
            max_daily_loss_usd=Decimal(str(data.get("max_daily_loss_usd", "500.00"))),
            max_leverage=int(data.get("max_leverage", 100)),
            algo_enabled=bool(data.get("algo_enabled", False)),
            kill_switch_active=bool(data.get("kill_switch_active", True)),
            version=int(data.get("version", 1)),
            created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(data["updated_at"]) if "updated_at" in data else datetime.now(timezone.utc),
        )


# ── Immutable Trade Configuration Snapshot ───────────────────────────────────


@dataclass(frozen=True)
class AlgoConfigurationSnapshot:
    """Immutable snapshot of the algorithm configuration used for a specific trade execution."""
    setup_id: Optional[str]
    account_id: str
    user_id: Optional[str]
    version: int
    take_profit_pct: Decimal
    stop_loss_pct: Decimal
    risk_per_trade_pct: Decimal
    max_risk_usd: Optional[Decimal]
    max_daily_loss_usd: Decimal
    max_leverage: int
    algo_enabled_at_snapshot: bool
    kill_switch_active_at_snapshot: bool
    snapshot_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def calculate_tp_sl(
        self,
        entry_price: Decimal,
        direction: Union[TradeDirection, StrategyDirection, str],
        tick_size: Decimal = Decimal("0.50"),
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """Calculate authoritative Stop Loss and Take Profit prices from snapshotted percentages.

        Returns:
            (stop_loss_price, take_profit_price, risk_reward_ratio)
        """
        is_long = direction in (TradeDirection.LONG, StrategyDirection.LONG, "LONG", "BUY")

        if is_long:
            sl_raw = entry_price * (Decimal("1") - (self.stop_loss_pct / Decimal("100")))
            tp_raw = entry_price * (Decimal("1") + (self.take_profit_pct / Decimal("100")))
        else:
            sl_raw = entry_price * (Decimal("1") + (self.stop_loss_pct / Decimal("100")))
            tp_raw = entry_price * (Decimal("1") - (self.take_profit_pct / Decimal("100")))

        # Quantize to tick size
        sl_price = (sl_raw / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_size
        tp_price = (tp_raw / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick_size

        # Verify geometry
        if is_long:
            if not (sl_price < entry_price < tp_price):
                raise AlgoConfigValidationError(
                    f"Invalid LONG TP/SL geometry: SL ({sl_price}) must be < Entry ({entry_price}) < TP ({tp_price})"
                )
            risk_dist = entry_price - sl_price
            reward_dist = tp_price - entry_price
        else:
            if not (tp_price < entry_price < sl_price):
                raise AlgoConfigValidationError(
                    f"Invalid SHORT TP/SL geometry: TP ({tp_price}) must be < Entry ({entry_price}) < SL ({sl_price})"
                )
            risk_dist = sl_price - entry_price
            reward_dist = entry_price - tp_price

        if risk_dist <= Decimal("0") or reward_dist <= Decimal("0"):
            raise AlgoConfigValidationError("Risk or reward distance is zero or negative")

        rr = reward_dist / risk_dist
        return sl_price, tp_price, rr

    def to_dict(self) -> Dict[str, Any]:
        """Serialize snapshot to dictionary."""
        return {
            "setup_id": self.setup_id,
            "account_id": self.account_id,
            "user_id": self.user_id,
            "version": self.version,
            "take_profit_pct": str(self.take_profit_pct),
            "stop_loss_pct": str(self.stop_loss_pct),
            "risk_per_trade_pct": str(self.risk_per_trade_pct),
            "max_risk_usd": str(self.max_risk_usd) if self.max_risk_usd is not None else None,
            "max_daily_loss_usd": str(self.max_daily_loss_usd),
            "max_leverage": self.max_leverage,
            "algo_enabled_at_snapshot": self.algo_enabled_at_snapshot,
            "kill_switch_active_at_snapshot": self.kill_switch_active_at_snapshot,
            "snapshot_timestamp": self.snapshot_timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AlgoConfigurationSnapshot":
        """Reconstruct snapshot from serialized dictionary."""
        return cls(
            setup_id=data.get("setup_id"),
            account_id=data["account_id"],
            user_id=data.get("user_id"),
            version=int(data["version"]),
            take_profit_pct=Decimal(str(data["take_profit_pct"])),
            stop_loss_pct=Decimal(str(data["stop_loss_pct"])),
            risk_per_trade_pct=Decimal(str(data["risk_per_trade_pct"])),
            max_risk_usd=Decimal(str(data["max_risk_usd"])) if data.get("max_risk_usd") is not None else None,
            max_daily_loss_usd=Decimal(str(data.get("max_daily_loss_usd", "500.00"))),
            max_leverage=int(data.get("max_leverage", 100)),
            algo_enabled_at_snapshot=bool(data.get("algo_enabled_at_snapshot", False)),
            kill_switch_active_at_snapshot=bool(data.get("kill_switch_active_at_snapshot", True)),
            snapshot_timestamp=datetime.fromisoformat(data["snapshot_timestamp"]) if "snapshot_timestamp" in data else datetime.now(timezone.utc),
        )


# ── Thread-Safe Algo Configuration Store ──────────────────────────────────────


class AlgoConfigStore:
    """Thread-safe store managing persistent configurations and immutable trade snapshots."""

    def __init__(self):
        # Key: (user_id, account_id) -> AlgoConfiguration
        self._configs: Dict[Tuple[str, str], AlgoConfiguration] = {}
        # Key: setup_id -> AlgoConfigurationSnapshot
        self._trade_snapshots: Dict[str, AlgoConfigurationSnapshot] = {}
        self._lock = threading.Lock()

    def get_config(self, user_id: str, account_id: str) -> Optional[AlgoConfiguration]:
        """Retrieve user configuration with strict ownership isolation."""
        with self._lock:
            return self._configs.get((user_id, account_id))

    def get_or_create_default(self, user_id: str, account_id: str) -> AlgoConfiguration:
        """Retrieve existing config or create a persistent record with fail-safe defaults."""
        with self._lock:
            key = (user_id, account_id)
            if key not in self._configs:
                self._configs[key] = AlgoConfiguration(
                    account_id=account_id,
                    user_id=user_id,
                    algo_enabled=False,
                    kill_switch_active=True,
                    version=1,
                )
            return self._configs[key]

    def update_config(
        self,
        user_id: str,
        account_id: str,
        take_profit_pct: Optional[Decimal] = None,
        stop_loss_pct: Optional[Decimal] = None,
        risk_per_trade_pct: Optional[Decimal] = None,
        max_risk_usd: Optional[Decimal] = None,
        max_daily_loss_usd: Optional[Decimal] = None,
        max_leverage: Optional[int] = None,
        algo_enabled: Optional[bool] = None,
        kill_switch_active: Optional[bool] = None,
    ) -> AlgoConfiguration:
        """Update configuration and increment version. Ensures account ownership."""
        with self._lock:
            key = (user_id, account_id)
            if key not in self._configs:
                self._configs[key] = AlgoConfiguration(
                    account_id=account_id,
                    user_id=user_id,
                    algo_enabled=False,
                    kill_switch_active=True,
                    version=1,
                )
            config = self._configs[key]
            return config.update(
                take_profit_pct=take_profit_pct,
                stop_loss_pct=stop_loss_pct,
                risk_per_trade_pct=risk_per_trade_pct,
                max_risk_usd=max_risk_usd,
                max_daily_loss_usd=max_daily_loss_usd,
                max_leverage=max_leverage,
                algo_enabled=algo_enabled,
                kill_switch_active=kill_switch_active,
            )

    def create_trade_snapshot(self, user_id: str, account_id: str, setup_id: str) -> AlgoConfigurationSnapshot:
        """Create and persist an immutable configuration snapshot for a new trade setup."""
        with self._lock:
            key = (user_id, account_id)
            if key not in self._configs:
                self._configs[key] = AlgoConfiguration(
                    account_id=account_id,
                    user_id=user_id,
                    algo_enabled=False,
                    kill_switch_active=True,
                    version=1,
                )
            config = self._configs[key]
            snapshot = config.create_snapshot(setup_id=setup_id)
            self._trade_snapshots[setup_id] = snapshot
            return snapshot

    def get_trade_snapshot(self, setup_id: str) -> Optional[AlgoConfigurationSnapshot]:
        """Retrieve the immutable snapshot bound to an existing trade setup."""
        with self._lock:
            return self._trade_snapshots.get(setup_id)

    def export_state(self) -> Dict[str, Any]:
        """Export all configurations and trade snapshots for persistent storage / recovery across restarts."""
        with self._lock:
            return {
                "configs": {
                    f"{u}:{a}": c.to_dict()
                    for (u, a), c in self._configs.items()
                },
                "trade_snapshots": {
                    s_id: snap.to_dict()
                    for s_id, snap in self._trade_snapshots.items()
                },
            }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Load state from persistent storage, restoring versioned configs and historical trade snapshots."""
        with self._lock:
            for key_str, c_dict in state.get("configs", {}).items():
                config = AlgoConfiguration.from_dict(c_dict)
                self._configs[(config.user_id, config.account_id)] = config
            for s_id, snap_dict in state.get("trade_snapshots", {}).items():
                self._trade_snapshots[s_id] = AlgoConfigurationSnapshot.from_dict(snap_dict)
