"""Chain configuration.

A chain adapter is data, not code: RPC endpoints, the Sourcify chain id,
the block-explorer domains used when resolving a project URL, the native
token symbol and its price ticker. Every probe in Preflight is plain EVM
eth_call, so a new chain is a new entry here, not a new code path.

BSC is first-class: the gas bands and probe behaviour were calibrated on
it in production. Base ships as a second adapter because the probe logic
generalises cleanly; its gas bands are NOT calibrated yet, so the gas
module reports price without a band there rather than faking one.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Chain:
    key: str                      # CLI name, e.g. "bsc"
    name: str                     # display name, e.g. "BNB Chain"
    chain_id: int
    rpcs: tuple
    native_symbol: str            # "BNB", "ETH"
    price_symbol: str             # Binance ticker stem, e.g. "BNBUSDT"
    coingecko_id: str             # price fallback id
    explorer_domains: tuple       # used to rank addresses found on a page
    gas_bands_calibrated: bool = False


BSC = Chain(
    key="bsc",
    name="BNB Chain (BSC)",
    chain_id=56,
    rpcs=(
        "https://bsc-dataseed.binance.org",
        "https://bsc-dataseed1.bnbchain.org",
        "https://bsc-dataseed2.bnbchain.org",
        "https://bsc-dataseed1.defibit.io",
        "https://bsc-rpc.publicnode.com",
    ),
    native_symbol="BNB",
    price_symbol="BNBUSDT",
    coingecko_id="binancecoin",
    explorer_domains=("bscscan.com", "bscscan.io"),
    gas_bands_calibrated=True,
)

BASE = Chain(
    key="base",
    name="Base",
    chain_id=8453,
    rpcs=(
        "https://mainnet.base.org",
        "https://base-rpc.publicnode.com",
        "https://base.drpc.org",
    ),
    native_symbol="ETH",
    price_symbol="ETHUSDT",
    coingecko_id="ethereum",
    explorer_domains=("basescan.org",),
    gas_bands_calibrated=False,
)

CHAINS = {c.key: c for c in (BSC, BASE)}
