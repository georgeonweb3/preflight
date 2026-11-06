# Build notes - Preflight v1 (built 2026-11-06, local only)

Status: built and proven locally in `~/workspace/preflight/`. NOT pushed
anywhere. George gives the final go for the public GitHub push after
reviewing this.

## What was reused from the existing skills (not reinvented)

From `~/workspace/custom-skills/bsc-mint-precheck/`:
- Pure-Python Keccak-256 + selector computation (ported into
  `preflight_core/keccak.py`; see the bug note below).
- Minimal ABI encode/decode helpers (`abi.py`).
- The whole probe design: probe tables for price / open-status /
  wallet-count / wallet-bool / mint candidates, EIP-1967 proxy slots,
  selector fingerprinting of the runtime code (and of the
  implementation when proxied), supply vs max-supply math, parallel
  probing with a thread pool.
- Simulation flow: eth_call each bytecode-present mint candidate from
  the check wallet, value 0 first, then value = probed price, revert
  reasons captured.
- URL resolution ranking: explorer links, then addresses near
  contract/mint keywords, then page order. UNRESOLVED_INPUT when
  nothing resolves, no guessing.
- Experience library format and matching (exact contract, then Jaccard
  ABI-shape similarity), including the "history is a hint, live checks
  always re-run" rule. Seed data in `data/experience_library.jsonl`
  carries five real entries from the skill's library (ACAT, BNB
  JIAYOU, BLOB Miner, Nomio, AGENT MAO), with ACAT/JIAYOU selector
  fingerprints filled in from the live example runs.

From `~/workspace/custom-skills/bsc-gas-guard/`:
- Gas approach: median eth_gasPrice across endpoints, band table,
  rolling 24h local baseline, USD cost table, eth_estimateGas for an
  exact call. One adaptation: the native price comes from
  data-api.binance.vision first (api.binance.com is geo-blocked from
  some networks), CoinGecko and DexScreener as fallbacks.

## What is new in Preflight

- Package layout (`preflight_core/`) with the pipeline split into
  single-job modules: chains, rpc, probes, sourcify, gas, resolve,
  library, verdict, check, cli.
- Chain adapters as data (`chains.py`): BSC first-class, Base included
  because the EVM probe logic generalises cleanly. Gas bands are only
  reported where the config says they are calibrated (BSC), rather
  than pretending Base bands exist.
- Sourcify verification (`sourcify.py`): the parent skill explicitly
  left this out; Preflight folds it in. API v2 only (see bug note).
- The verdict engine (`verdict.py`): CLEAR / CAUTION / BLOCKED with
  itemised, level-tagged reasons, as a pure function (unit-tested on
  synthetic checks, including the ACAT and JIAYOU shapes).
- The standard JSON schema `preflight.verdict/1` and the
  screenshot-friendly text card.

## Bugs found while building (all fixed, all instructive)

1. Two Keccak round constants were mistyped during the port
   (indices 21-22). The built-in `--selftest` caught it before
   anything else ran; constants now match the production skill
   byte-for-byte and the self-test passes. This is why the self-test
   ships in the CLI.
2. Sourcify's v1 API has been removed upstream (it returns a 404
   removal notice). Worse, v2 answers unverified contracts with an
   HTTP 404 whose body carries the verdict (null match fields), and
   urllib raises on 404, so the first live JIAYOU run reported
   verification "unknown" instead of "none" and the signature
   UNVERIFIED_AND_REVERTING block did not fire. Fixed: v2 only, the
   error body is parsed, 404 + null matches = "none", one retry on
   transport failure, and the response mapping is unit-tested
   (`tests/test_sourcify.py`). Re-ran both examples after the fix.
3. The text card printed "holds None" when a contract does not answer
   balanceOf. Fixed to "holds unknown"; example cards re-rendered
   from their saved JSON so text and JSON stay consistent.

## Cut from v1, and why

- Library recording (`--record-outcome` in the skill): the library is
  read-only in v1 plus seed data. Keeps the public surface small;
  recording returns when there are outside users whose outcomes are
  worth recording.
- Base gas bands: not calibrated, so not shown. Honesty over coverage.
- Explorer (BscScan/BaseScan) verification as a second source:
  Sourcify only in v1; explorer APIs need keys and vary per chain.
- Commit/reveal guided probing: those flows need each project's own
  guide (the Agent Periodic Lab lesson). v1 flags NO_KNOWN_MINT_FUNCTION
  as a caution instead of pretending to check them.
- Batch mode, hosted UI, agent-seatbelt code: doors 2 and 3 are spec
  only, per the ship order.

## Known rough edge

A full check took ~28s wall-clock on public BSC RPCs during the
example runs (parallel probes, some endpoints slow to fail over). Fine
for a pre-sign gate; endpoint/timeout tuning is the first optimisation
if it ever annoys.

## Test results

- `python3 -m preflight_core.cli --selftest`: OK.
- `python3 -m unittest discover -s tests`: 22 tests, OK (keccak
  vectors, input resolution, verdict rules incl. both historical
  shapes, Sourcify response mapping, gas bands).
- Live runs: see `examples/` (ACAT and JIAYOU, 2026-11-06), plus
  URL-mode and UNRESOLVED_INPUT paths exercised live.

## Remaining steps to publish (nothing here is done yet)

1. George reviews this build and says go.
2. Create the repo: suggested name `preflight` under
   github.com/georgeonweb3 (check the name is free at creation time).
3. `git init` in `~/workspace/preflight/`, first commit. `.gitignore`
   already excludes `__pycache__` and the runtime gas history.
4. Topics/tags: bnb-chain, bsc, base, nft, mint, security, precheck,
   web3, python, ai-agents.
5. Tag `v0.1.0` and cut a GitHub release summarising the two example
   verdicts (they are the best demo the project has).
6. Add a verdict-card screenshot as the repo's social preview image.
7. Launch: X thread + Farcaster cast built from the JIAYOU example
   ("flag said open, price said free, still blocked"), then awesome
   list submissions once there is traction to point at.
