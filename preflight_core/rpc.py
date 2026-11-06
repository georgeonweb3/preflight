"""Read-only JSON-RPC client with endpoint failover.

The client refuses, in code, to send anything outside a small allowlist of
read-only methods. There is no code path here that can sign or broadcast:
eth_sendTransaction and friends are rejected before any network call.

Failover exists because public BSC endpoints rate-limit and truncate under
load (observed repeatedly in production use of the parent skills).

Conventions kept from the parent bsc-mint-precheck skill:
- an RPC-level error on eth_call / eth_estimateGas is returned as
  {"__revert__": message} - a deterministic revert is an answer, not a
  transport failure, so we do not retry it on other endpoints;
- a transport failure returns {"__error__": message} for call-like methods
  and None for simple getters after every endpoint has been tried.
"""
import json
import urllib.error
import urllib.request

READ_ONLY_METHODS = {
    "eth_call",
    "eth_estimateGas",
    "eth_getCode",
    "eth_getStorageAt",
    "eth_blockNumber",
    "eth_chainId",
    "eth_gasPrice",
    "eth_getBalance",
}
_CALL_LIKE = {"eth_call", "eth_estimateGas"}


class Rpc:
    def __init__(self, endpoints, user_agent="preflight/0.1"):
        self.endpoints = list(endpoints)
        self.user_agent = user_agent
        self.errors = []

    def call(self, method, params):
        if method not in READ_ONLY_METHODS:
            raise ValueError(f"refusing non-read-only RPC method: {method}")
        last_err = None
        for ep in self.endpoints:
            try:
                payload = json.dumps({
                    "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
                }).encode()
                req = urllib.request.Request(ep, data=payload, headers={
                    "Content-Type": "application/json",
                    "User-Agent": self.user_agent,
                })
                with urllib.request.urlopen(req, timeout=15) as r:
                    j = json.loads(r.read())
                if "error" in j:
                    last_err = j["error"].get("message", str(j["error"]))
                    if method in _CALL_LIKE:
                        return {"__revert__": last_err}
                    return None
                return j.get("result")
            except Exception as e:  # endpoint down / rate limited -> try next
                last_err = str(e)
                self.errors.append(f"{ep}: {e}")
                continue
        if method in _CALL_LIKE:
            return {"__error__": last_err}
        return None

    def eth_call(self, to, data, from_addr=None, value_wei=0):
        tx = {"to": to, "data": data}
        if from_addr:
            tx["from"] = from_addr
        if value_wei:
            tx["value"] = hex(value_wei)
        return self.call("eth_call", [tx, "latest"])

    def estimate_gas(self, to, data, from_addr=None, value_wei=0):
        """Gas units for a call against current state, or a revert dict."""
        tx = {"to": to, "data": data}
        if from_addr:
            tx["from"] = from_addr
        if value_wei:
            tx["value"] = hex(value_wei)
        res = self.call("eth_estimateGas", [tx])
        if isinstance(res, str) and res.startswith("0x"):
            return int(res, 16)
        return res  # dict with __revert__/__error__, or None

    def gas_price_wei(self):
        res = self.call("eth_gasPrice", [])
        if isinstance(res, str) and res.startswith("0x"):
            return int(res, 16)
        return None
